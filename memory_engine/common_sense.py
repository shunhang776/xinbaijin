"""
白槿的社交直觉 — AI Native 常识感知模块。
不是外挂推理器，是白槿听到对方说话后下意识会留意的细节。
经验从对话中自动复盘沉淀，自生长闭环。
直觉文本本身就是状态影响——语言比数值参数更 AI Native。
"""
from __future__ import annotations

import os
import json
import atexit
import asyncio
import hashlib
import logging
import threading
import httpx
import numpy as np
from pathlib import Path

try:
    import faiss
    faiss.omp_set_num_threads(1)
except ImportError:
    faiss = None
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("baijin.common_sense")

# ── 配置常量 ──
_API_URL = "https://api.deepseek.com/v1/chat/completions"
_MODEL = "deepseek-chat"
_HTTP_TIMEOUT = 2.0
_TOTAL_TIMEOUT = 3.0
_MAX_TOKENS = 200
_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
_EXPERIENCE_TOP_N = 3       # 经验库命中取 Top N
_EXPERIENCE_USE_THRESHOLD = 0.65  # 匹配度超过此值直接用经验，不调 LLM
_MAX_INTUITION_LEN = 200    # 单条经验直觉最大长度，防臃肿
_MERGE_SIM_THRESHOLD = 0.8  # 直觉合并语义相似度阈值，超过视为同义

# ── 全局线程池 ──
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="csense")


def _shutdown_executor():
    """安全关闭线程池。解释器关闭期间可能触发各种异常，全部静默。"""
    try:
        _EXECUTOR.shutdown(wait=False)
    except Exception:
        pass


atexit.register(_shutdown_executor)

# ── 经验库读写锁 + 加载标记 ──
_EXP_LOCK = threading.RLock()
_EXP_LOADED = False

_INTUITION_INDEX = None          # FAISS 索引实例
_FAISS_ENABLE_THRESHOLD = 0      # 强制启用 FAISS

# ── FAISS 直觉索引 ──

def _scene_hash(scene: str) -> str:
    """稳定的场景哈希，不受 Python 进程随机种子影响。"""
    return hashlib.md5(scene.encode("utf-8")).hexdigest()


class IntuitionIndex:
    """轻量 FAISS 索引，复用记忆引擎 HNSW 参数。内置锁，所有公共方法线程安全。"""

    def __init__(self):
        if faiss is None:
            raise RuntimeError("faiss 未安装")
        from memory_engine.config import VECTOR_DIM, HNSW_M, EF_CONSTRUCTION, EF_SEARCH
        self._dim = VECTOR_DIM
        self._index = faiss.IndexHNSWFlat(self._dim, HNSW_M)
        self._index.hnsw.efConstruction = EF_CONSTRUCTION
        self._index.hnsw.efSearch = EF_SEARCH
        self._scene_to_row: dict[str, int] = {}
        self._row_to_scene: dict[int, str] = {}
        self._next_row = 0
        self._lock = threading.RLock()

    def _rebuild_mapping(self, experiences: list[dict]):
        self._scene_to_row.clear()
        self._row_to_scene.clear()
        for i, e in enumerate(experiences):
            if e.get("_emb") is not None:
                sid = _scene_hash(e.get("scene", ""))
                self._scene_to_row[sid] = i
                self._row_to_scene[i] = sid
        self._next_row = len(experiences)

    def build(self, experiences: list[dict]):
        with self._lock:
            self._index.reset()
            vecs = []
            for e in experiences:
                emb = e.get("_emb")
                if emb is not None:
                    vecs.append(np.ascontiguousarray(emb, dtype=np.float32))
                else:
                    vecs.append(np.zeros(self._dim, dtype=np.float32))
            if vecs:
                self._index.add(np.array(vecs, dtype=np.float32))
                self._rebuild_mapping(experiences)
            logger.info("直觉 FAISS 索引构建完成: %d 条", self._index.ntotal)

    def search(self, query_vec, top_k: int = 5) -> list[str]:
        with self._lock:
            if self._index.ntotal == 0:
                return []
            q = np.array([query_vec], dtype=np.float32)
            _, indices = self._index.search(q, min(top_k, self._index.ntotal))
            results = []
            for idx in indices[0]:
                if idx == -1:
                    continue
                sid = self._row_to_scene.get(int(idx))
                if sid:
                    results.append(sid)
            return results

    def add(self, scene: str, emb):
        with self._lock:
            vec = np.array([emb], dtype=np.float32)
            self._index.add(vec)
            sid = _scene_hash(scene)
            row = self._next_row
            self._scene_to_row[sid] = row
            self._row_to_scene[row] = sid
            self._next_row += 1

    def save(self, path):
        with self._lock:
            faiss.write_index(self._index, str(path))

    def load(self, path, expected_count: int) -> bool:
        p = Path(path) if isinstance(path, str) else path
        if not p.exists():
            return False
        try:
            with self._lock:
                self._index = faiss.read_index(str(p))
            if self._index.ntotal != expected_count:
                self._index.reset()
                logger.info("FAISS 数量不匹配(%d≠%d)，重建",
                           self._index.ntotal, expected_count)
                return False
            return True
        except Exception as e:
            logger.warning("加载 FAISS 失败: %s", e)
            return False

    def __len__(self):
        return self._index.ntotal if self._index else 0


def _save_faiss_index():
    """进程退出时持久化 FAISS 索引。"""
    global _INTUITION_INDEX
    if _INTUITION_INDEX is None or len(_INTUITION_INDEX) == 0:
        return
    try:
        from memory_engine.config import SOCIAL_INTUITION_FAISS_PATH
        _INTUITION_INDEX.save(SOCIAL_INTUITION_FAISS_PATH)
        logger.info("FAISS 索引已持久化: %d 条", len(_INTUITION_INDEX))
    except Exception as e:
        logger.warning("FAISS 持久化失败: %s", e)


atexit.register(_save_faiss_index)

# ── 快速通道：本能反应，不调 LLM ──
# 直觉文本本身就是状态影响 —— "这么晚了还没睡" 自然让白槿更温柔
_FAST_PATH: list[dict] = [
    {"hour_range": (0, 6),
     "intuition": "这么晚了还没睡，他是不是有心事",
     "confidence": 0.95},
    {"hour_range": (6, 8),
     "intuition": "刚醒没多久，说话别太急，让他慢慢醒神",
     "confidence": 0.90},
]


# ── 经验库加载 / 保存 ──

def preload_experiences() -> int:
    """启动时预热：加载经验文件 + 编码 + 构建 FAISS 索引。返回经验条数。"""
    global _EXP_LOADED
    from memory_engine.config import SOCIAL_INTUITION_EXPERIENCES, SOCIAL_INTUITION_PATH

    with _EXP_LOCK:
        if _EXP_LOADED:
            return len(SOCIAL_INTUITION_EXPERIENCES)
        if SOCIAL_INTUITION_PATH.exists():
            try:
                with open(SOCIAL_INTUITION_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    SOCIAL_INTUITION_EXPERIENCES.clear()
                    SOCIAL_INTUITION_EXPERIENCES.extend(data)
            except Exception as e:
                logger.warning("加载社交经验文件失败，保留内存种子: %s", e)
        else:
            _save_experiences(SOCIAL_INTUITION_EXPERIENCES)
        _EXP_LOADED = True

    _pre_encode_experiences()
    _init_intuition_index()
    return len(SOCIAL_INTUITION_EXPERIENCES)


def _load_experiences() -> list[dict]:
    """获取经验列表。预热后 _EXP_LOADED 为 True，直接返回。"""
    from memory_engine.config import SOCIAL_INTUITION_EXPERIENCES
    with _EXP_LOCK:
        return list(SOCIAL_INTUITION_EXPERIENCES)


def _pre_encode_experiences():
    """批量预编码所有经验场景文本。启动时调用一次。"""
    from memory_engine.config import SOCIAL_INTUITION_EXPERIENCES
    uncached = [e for e in SOCIAL_INTUITION_EXPERIENCES if "_emb" not in e]
    if not uncached:
        return
    scenes = [e.get("scene", "") for e in uncached]
    emb = _get_embedder()
    if emb is None:
        return
    try:
        vecs = emb.encode_query(scenes)
        for i, e in enumerate(uncached):
            if isinstance(vecs[i], np.ndarray):
                e["_emb"] = vecs[i]
        logger.info("经验库预编码完成: %d 条", len(uncached))
        failed = sum(1 for e in uncached if "_emb" not in e)
        if failed:
            logger.warning("经验库预编码 %d 条失败，降级到逐条编码", failed)
    except Exception as e:
        logger.warning("经验库预编码失败: %s", e)


def _init_intuition_index():
    """初始化 FAISS 索引。经验数低于阈值则跳过。"""
    global _INTUITION_INDEX
    from memory_engine.config import SOCIAL_INTUITION_EXPERIENCES, SOCIAL_INTUITION_FAISS_PATH

    exp_count = len([e for e in SOCIAL_INTUITION_EXPERIENCES if e.get("_emb") is not None])
    if exp_count < _FAISS_ENABLE_THRESHOLD:
        logger.info("经验仅 %d 条(<%d)，跳过 FAISS，使用暴力余弦",
                   exp_count, _FAISS_ENABLE_THRESHOLD)
        return

    _INTUITION_INDEX = IntuitionIndex()
    if _INTUITION_INDEX.load(SOCIAL_INTUITION_FAISS_PATH, exp_count):
        _INTUITION_INDEX._rebuild_mapping(SOCIAL_INTUITION_EXPERIENCES)
        logger.info("FAISS 索引已恢复: %d 条", len(_INTUITION_INDEX))
    else:
        _INTUITION_INDEX.build(SOCIAL_INTUITION_EXPERIENCES)
        _INTUITION_INDEX.save(SOCIAL_INTUITION_FAISS_PATH)
        logger.info("FAISS 索引已构建: %d 条", len(_INTUITION_INDEX))


def _save_experiences(experiences: list[dict]):
    """保存经验库到 JSON 文件。自动过滤 _emb 等内部缓存字段。"""
    from memory_engine.config import SOCIAL_INTUITION_PATH
    try:
        clean = [{k: v for k, v in e.items() if not k.startswith("_")}
                 for e in experiences]
        with open(SOCIAL_INTUITION_PATH, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("保存社交经验文件失败: %s", e)


# ── BGE 语义相似度 ──

_embedder_cache = None
_embedder_lock = threading.RLock()


def _get_embedder():
    """懒加载 BGE embedder。不可用时返回 None，降级到字符相似度。"""
    global _embedder_cache
    if _embedder_cache is None:
        with _embedder_lock:
            if _embedder_cache is None:
                try:
                    from embedding.embedder import get_embedder
                    _embedder_cache = get_embedder()
                except Exception as e:
                    logger.warning("BGE embedder 加载失败，降级字符相似度: %s", e)
                    _embedder_cache = False  # False 表示不可用
    return _embedder_cache if _embedder_cache is not False else None


def encode_text(text: str) -> "np.ndarray | None":
    """将文本编码为 BGE 向量（公共接口，供外部复用）。"""
    emb = _get_embedder()
    if emb is None:
        return None
    try:
        vec = emb.encode_query([text])
        if isinstance(vec, np.ndarray):
            return vec[0]
    except Exception:
        pass
    return None


_encode_text = encode_text  # 内部别名，兼容旧调用


def _cosine_similarity(a: "np.ndarray", b: "np.ndarray") -> float:
    """两个向量的余弦相似度。"""
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


def _scene_similarity(a: str, b: str) -> float:
    """场景文本语义相似度。全程 BGE，无降级。"""
    emb_a = _encode_text(a)
    emb_b = _encode_text(b)
    if emb_a is None or emb_b is None:
        return 0.0  # embedder 不可用时返回不相似，不误合并
    return _cosine_similarity(emb_a, emb_b)


# ── 经验排序 ──

def _experience_match_score(exp: dict, user_msg: str) -> float:
    """计算经验与当前消息的匹配度。BGE 语义相似度 + 置信度加权。"""
    scene = exp.get("scene", "")
    if not scene or not user_msg:
        return 0.0
    sim = _scene_similarity(scene, user_msg)
    conf = exp.get("confidence", 0.5)
    return sim * 0.6 + conf * 0.4


def _rank_experiences(user_msg: str, experiences: list[dict],
                      top_n: int = _EXPERIENCE_TOP_N,
                      query_vec=None) -> tuple[list[dict], list[float]]:
    """经验匹配排序。query_vec 必须由上层传入，不内部兜底编码。"""
    global _INTUITION_INDEX
    if not experiences:
        return [], []
    emb_user = query_vec
    if emb_user is None:
        return [], []

    # FAISS 粗筛候选集（仅经验数达阈值时启用）
    if _INTUITION_INDEX is not None and len(_INTUITION_INDEX) > 0:
        candidates = _INTUITION_INDEX.search(emb_user, top_k=max(top_n * 2, 10))
        if candidates:
            hash_map = {_scene_hash(e.get("scene", "")): e for e in experiences}
            candidate_exps = []
            for sid in candidates:
                exp = hash_map.get(sid)
                if exp is not None:
                    candidate_exps.append(exp)
            if not candidate_exps:
                candidate_exps = list(experiences)
        else:
            candidate_exps = list(experiences)
    else:
        candidate_exps = list(experiences)

    # 余弦 + 置信度精排
    scored = []
    for e in candidate_exps:
        if "_emb" not in e:
            e["_emb"] = _encode_text(e.get("scene", ""))
        emb_scene = e["_emb"]
        sim = _cosine_similarity(emb_user, emb_scene) if (emb_user is not None and emb_scene is not None) else 0.0
        conf = e.get("confidence", 0.5)
        scored.append((e, sim * 0.6 + conf * 0.4))
    scored.sort(key=lambda x: -x[1])
    top = [(e, s) for e, s in scored[:top_n] if s > 0.0]
    if not top:
        return [], []
    return [e for e, _ in top], [s for _, s in top]


def _format_experiences_for_llm(experiences: list[dict]) -> str:
    """把经验库格式化为白槿能理解的经验文本。"""
    if not experiences:
        return ""
    lines = ["【你已有的社交经验】"]
    for i, e in enumerate(experiences):
        scene = e.get("scene", "")
        intuition = e.get("intuition", "")
        lines.append(f"{i + 1}. {scene} → {intuition}")
    return "\n".join(lines)


# ── 记忆召回 ──

def _recall_similar_memories(user_msg: str) -> str:
    """从长期记忆召回相似场景的历史对话，让直觉参考过往经验。"""
    try:
        from memory_engine import get_engine
        engine = get_engine()
        if engine and hasattr(engine, "retrieve"):
            results, _ = engine.retrieve.search_sync(user_msg, query_vec=None)
            results = results[:2]
            if results:
                lines = ["【之前类似的对话】"]
                for r in results[:2]:
                    content = r.get("content", "") if isinstance(r, dict) else str(r)
                    if content:
                        lines.append(f"- {content[:200]}")
                return "\n".join(lines)
    except Exception:
        pass
    return ""


# ── 快速通道：返回直觉文本 + 状态影响 ──

def _fast_path_check(hour: int) -> dict | None:
    """本能反应：返回 {intuition, confidence} 或 None。
    直觉文本本身就是状态影响——语言比数值更 AI Native。"""
    for entry in _FAST_PATH:
        lo, hi = entry["hour_range"]
        if lo <= hour < hi:
            return {
                "intuition": entry["intuition"],
                "confidence": entry.get("confidence", 1.0),
            }
    return None


# ── LLM 直觉推理 ──

async def _llm_intuit(user_msg: str, hour: int, recent_context: str,
                      experiences: list[dict]) -> str:
    """白槿听完对方的话，下意识会留意到什么。"""
    if not _API_KEY:
        logger.error("社交直觉跳过：未配置 DEEPSEEK_API_KEY")
        return ""

    similar = _recall_similar_memories(user_msg)
    ranked, _ = _rank_experiences(user_msg, experiences)
    exp_text = _format_experiences_for_llm(ranked)

    system_prompt = (
        "你是白槿，正在和对方聊天。\n"
        "请你根据对方刚说的话和当下情境，写下你听完后下意识会留意到的细节。\n\n"
        "要求：\n"
        "- 只写你心里会注意到的事，不用讲大道理，不用给解决方案\n"
        "- 优先留意对方的状态、情绪、当下在做什么\n"
        "- 用很自然的短句，每条一行，不要书面化、学术化\n"
        "- 如果没什么特别需要留意的，就输出「无」"
    )

    parts = [f"对方刚说：{user_msg}"]
    if recent_context:
        parts.append(f"最近的对话：\n{recent_context[-300:]}")
    if similar:
        parts.append(similar)
    if exp_text:
        parts.append(exp_text)
    parts.append(f"（补充：现在是北京时间{hour}点左右）")
    parts.append("你心里留意到：")
    user_prompt = "\n\n".join(parts)

    body = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": _MAX_TOKENS,
        "temperature": 0.3,
    }

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                _API_URL,
                headers={
                    "Authorization": f"Bearer {_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            if resp.status_code != 200:
                logger.warning("社交直觉 API 返回 %d: %s",
                               resp.status_code, resp.text[:200])
                return ""
            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                return ""
            content = choices[0].get("message", {}).get("content", "").strip()
            if content == "无" or not content:
                return ""
            return content
    except asyncio.TimeoutError:
        logger.warning("社交直觉超时, 输入片段: %.50s", user_msg)
        return ""
    except Exception as e:
        logger.warning("社交直觉失败: %s, 输入片段: %.50s", e, user_msg)
        return ""


def _best_experience_match(user_msg: str, experiences: list[dict],
                           query_vec=None) -> dict | None:
    """经验库最佳匹配。query_vec 传入则跳过编码。"""
    if not experiences:
        return None
    ranked, scores = _rank_experiences(user_msg, experiences, top_n=1,
                                       query_vec=query_vec)
    if not ranked or not scores:
        return None
    score = scores[0]
    if score >= _EXPERIENCE_USE_THRESHOLD:
        return {
            "intuition": ranked[0].get("intuition", ""),
            "score": score,
            "confidence": ranked[0].get("confidence", 0.5),
        }
    return None


def get_social_intuition(user_msg: str, recent_context: str = "",
                         query_vec=None) -> str:
    """白槿的社交直觉感知。主链路应传入预编码向量，未传则入口兜底编码。"""
    if query_vec is None:
        query_vec = encode_text(user_msg)  # 仅入口层兜底
    hour = datetime.now(timezone(timedelta(hours=8))).hour

    # 1. 本能反应（时间通道，零延迟）
    fast = _fast_path_check(hour)
    if fast:
        return fast["intuition"]

    experiences = _load_experiences()

    # 2. 经验匹配
    best = _best_experience_match(user_msg, experiences, query_vec=query_vec)
    if best:
        return best["intuition"]

    # 3. LLM 推理（经验匹配不够时兜底，~2s）
    try:
        future = _EXECUTOR.submit(
            lambda: asyncio.run(
                _llm_intuit(user_msg, hour, recent_context, experiences)))
        return future.result(timeout=_TOTAL_TIMEOUT)
    except FutureTimeoutError:
        logger.warning("社交直觉整体超时, 输入片段: %.50s", user_msg)
        return ""
    except RuntimeError:
        return ""
    except Exception as e:
        logger.warning("社交直觉整体失败: %s, 输入片段: %.50s", e, user_msg)
        return ""


# ── 自动复盘：对话结束后轻量自检，踩坑自动沉淀 ──

async def _reflect_and_learn(user_msg: str, reply: str, recent_context: str = ""):
    """轻量自检：白槿回顾自己的回复，如果不符合社交直觉就自动沉淀经验。"""
    if not _API_KEY:
        return
    if len(reply) < 10:
        return

    prompt = (
        "你是白槿，刚回复了对方一句话。请你快速扫一眼：\n\n"
        f"对方说：{user_msg}\n"
        f"你回复：{reply[:300]}\n\n"
        "你的回复有没有明显不符合社交常识的地方？比如：\n"
        "- 对方明显不方便时发了长篇大论\n"
        "- 对方需要共情时直接给了解决方案\n"
        "- 忽略了对方明显的情绪\n\n"
        "如果没问题，输出「无」。\n"
        "如果有问题，用两行输出：\n"
        "场景：xxx（对方当时的处境）\n"
        "直觉：xxx（这种情况下正确社交认知是什么）"
    )

    body = {
        "model": _MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 80,
        "temperature": 0.0,
    }

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.post(
                _API_URL,
                headers={
                    "Authorization": f"Bearer {_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            if resp.status_code != 200:
                return
            content = resp.json()["choices"][0]["message"]["content"].strip()
            if content == "无" or not content or "场景" not in content:
                return

            scene = ""
            intuition = ""
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("场景：") or line.startswith("场景:"):
                    scene = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                if line.startswith("直觉：") or line.startswith("直觉:"):
                    intuition = line.split("：", 1)[-1].split(":", 1)[-1].strip()

            if scene and intuition:
                add_experience(
                    scene=scene,
                    intuition=intuition,
                    note="自动复盘沉淀",
                    confidence=0.7,  # 自动沉淀的置信度低于人工种子
                )
                logger.info("社交直觉自动沉淀: %s", scene[:60])
    except Exception:
        pass  # 自检失败不影响主流程


def reflect_on_reply(user_msg: str, reply: str, recent_context: str = ""):
    """异步自检入口。fire-and-forget，不阻塞回复。
    recent_context 传入最近对话上下文，提高自检准确率。"""
    try:
        _EXECUTOR.submit(
            lambda: asyncio.run(
                _reflect_and_learn(user_msg, reply, recent_context)))
    except RuntimeError:
        pass  # 解释器关闭中
    except Exception:
        pass


# ── 经验库管理 ──

def _find_similar(scene: str, threshold: float = 0.7) -> int | None:
    """在经验库中查找相似场景，返回索引或 None。"""
    from memory_engine.config import SOCIAL_INTUITION_EXPERIENCES
    for i, exp in enumerate(SOCIAL_INTUITION_EXPERIENCES):
        if _scene_similarity(scene, exp.get("scene", "")) >= threshold:
            return i
    return None


def add_experience(scene: str, intuition: str, note: str = "",
                   confidence: float = 0.9):
    """沉淀一条社交经验。自动去重，线程安全，持久化。
    confidence: 0.9=人工种子, 0.7=自动复盘, 后续可动态调整。
    """
    from memory_engine.config import SOCIAL_INTUITION_EXPERIENCES
    with _EXP_LOCK:
        idx = _find_similar(scene)
        if idx is not None:
            existing = SOCIAL_INTUITION_EXPERIENCES[idx]
            existing_intuition = existing.get("intuition", "")
            # BGE 语义去重：同义不追加，取置信度高的
            if _scene_similarity(intuition, existing_intuition) >= _MERGE_SIM_THRESHOLD:
                if confidence > existing.get("confidence", 0.5):
                    existing["intuition"] = intuition  # 用置信度更高的替换
                existing["confidence"] = max(
                    existing.get("confidence", 0.5), confidence)
                return
            # 语义不同：追加剧合，总长度封顶
            merged = existing_intuition + "；" + intuition
            if len(merged) > _MAX_INTUITION_LEN:
                merged = merged[:_MAX_INTUITION_LEN]
            existing["intuition"] = merged
            if note:
                existing["note"] = (existing.get("note", "") + "|" + note).strip("|")
            existing["confidence"] = max(
                existing.get("confidence", 0.5), confidence)
            logger.info("社交经验已合并: %s", scene[:40])
        else:
            entry = {"scene": scene, "intuition": intuition, "confidence": confidence}
            if note:
                entry["note"] = note
            # 新增经验即时预编码，embedder 不可用时跳过
            try:
                entry["_emb"] = _encode_text(scene)
            except Exception:
                pass
            SOCIAL_INTUITION_EXPERIENCES.append(entry)
            # 同步 FAISS（仅内存追加，落盘由 atexit 统一处理）
            if _INTUITION_INDEX is not None and entry.get("_emb") is not None:
                _INTUITION_INDEX.add(scene, entry["_emb"])
            logger.info("社交经验已沉淀: %s (置信度%.1f)", scene[:40], confidence)
        _save_experiences(SOCIAL_INTUITION_EXPERIENCES)


# 兼容旧接口
add_rule = add_experience


def list_experiences() -> list[dict]:
    """列出当前所有社交经验。"""
    return _load_experiences()


list_rules = list_experiences
