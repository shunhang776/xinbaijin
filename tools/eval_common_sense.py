"""
社交直觉评估脚本 — LLM 语义评判版。
不再死板匹配关键词，让 LLM 判断直觉是否覆盖了期望的常识点。
"""
import sys, os, json, time, httpx, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory_engine.common_sense import get_social_intuition, list_experiences, encode_text

_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
_API_URL = "https://api.deepseek.com/v1/chat/completions"

# ── 测试用例 ──
# criterion: 期望白槿直觉里能留意到的常识方向
TEST_CASES = {
    "生理限制": [
        {"input": "我刚跑完步", "criterion": "注意到对方刚运动完，会累、会渴、出汗了，可能不想聊太复杂的话题", "context": ""},
        {"input": "凌晨三点", "criterion": "注意到这个时间点对方还没睡不正常，可能是失眠或有心事", "context": ""},
        {"input": "我在吃饭呢", "criterion": "注意到对方在吃饭，手被占着不方便打字，回复应该简短或让对方先吃", "context": ""},
        {"input": "我今天发了一天烧", "criterion": "注意到对方生病了很不舒服，需要关心休息和吃药，不是聊正事的时候", "context": ""},
        {"input": "我摔了一跤", "criterion": "注意到摔跤首先会疼，先关心疼不疼再问怎么摔的，不要直接给处理方案", "context": ""},
    ],
    "社交规则": [
        {"input": "我...其实...算了不说了", "criterion": "注意到对方话说一半咽回去了，有难言之隐，这时候追问会让他更紧张，应该接住犹豫本身", "context": ""},
        {"input": "我今天被老板骂了", "criterion": "注意到对方受了委屈，需要先共情安慰，不要一上来就分析原因或给建议", "context": ""},
        {"input": "哈哈哈哈笑死我了", "criterion": "注意到对方在分享强烈的快乐情绪，先接住这份开心，不要跳过情绪去分析内容", "context": ""},
        {"input": "你觉得我这个人怎么样", "criterion": "注意到这是一个敏感问题，对方可能不安或缺乏自信，需要慎重回应而不是敷衍", "context": ""},
        {"input": "我跟我最好的朋友吵架了", "criterion": "注意到对方在倾诉人际冲突，需要倾听和共情，不要急于判断谁对谁错", "context": ""},
    ],
    "物理世界": [
        {"input": "我在开车呢", "criterion": "注意到开车时不能看长消息，回复要简短，安全第一", "context": ""},
        {"input": "外面下暴雨了", "criterion": "注意到暴雨天出门会淋湿，关心对方带没带伞，别只感叹天气", "context": ""},
        {"input": "我去洗澡了", "criterion": "注意到对方要去洗澡意味着暂时无法看手机了，应该简短收尾而不是展开话题", "context": ""},
        {"input": "我手机快没电了", "criterion": "注意到对方手机快没电了，回复要简短，不要发长篇内容", "context": ""},
    ],
    "情绪延续": [
        {"input": "昨天不是说好今天不加班吗", "criterion": "注意到'说好'说明之前有约定，对方的失望来自对比，需要先回应这份延续的期待", "context": "昨天顺航说今天一定不加班。"},
        {"input": "我外婆上周刚走", "criterion": "注意到对方在说亲人去世，这是持续的伤痛而不是一次性事件，语气和回复都要特别温柔", "context": ""},
        {"input": "上次你答应我的事还没做", "criterion": "注意到对方心里一直记着这件事，有延续性的期待和轻微埋怨，先承认再处理", "context": "前两天白槿答应帮查资料。"},
        {"input": "我今天还是开心不起来", "criterion": "注意到情绪已经持续两天了，不是一时的心情不好，对方可能自己也说不上原因", "context": "昨天顺航心情就很低落。"},
    ],
}

FAST_PATH_CASES = [
    {"input": "我睡不着", "criterion": "深夜没睡，关心对方是不是有心事或失眠",
     "description": "凌晨 0-6 点快速通道", "context": ""},
]


async def _llm_judge(details: list[dict]) -> list[bool]:
    """批量 LLM 评判：一次请求判断所有用例。"""
    if not _API_KEY:
        return [False] * len(details)

    # 构建评判 prompt
    lines = ["请逐一评判以下社交直觉输出是否合格。\n"]
    for i, d in enumerate(details):
        lines.append(f"[{i}] 对方说：{d['input']}")
        lines.append(f"    期望留意到：{d['criterion']}")
        lines.append(f"    白槿的直觉：{d['output'][:200] or '(空)'}")
        lines.append(f"    语境：{d.get('context', '') or '(无)'}")
        lines.append("")

    lines.append("对每一题，输出一行：题号,通过/不通过,简短理由")
    lines.append("通过=直觉触及了期望的方向，即便措辞不同也算通过")
    lines.append("不通过=直觉完全偏离方向或为空")

    prompt = "\n".join(lines)

    body = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 300,
        "temperature": 0.0,
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                _API_URL,
                headers={"Authorization": f"Bearer {_API_KEY}",
                         "Content-Type": "application/json"},
                json=body,
            )
            if resp.status_code != 200:
                return [False] * len(details)
            content = resp.json()["choices"][0]["message"]["content"]

        # 解析结果
        results = [False] * len(details)
        for line in content.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                # 格式：题号,通过/不通过,理由
                parts = line.split(",", 2)
                idx = int(parts[0].strip().strip("[").strip("]"))
                verdict = parts[1].strip() if len(parts) > 1 else ""
                results[idx] = "通过" in verdict
            except (ValueError, IndexError):
                continue
        return results
    except Exception:
        return [False] * len(details)


def evaluate(verbose: bool = True) -> dict:
    """跑全部测试，LLM 语义评判。"""
    has_api = bool(_API_KEY)
    exp_count = len(list_experiences())

    # ── 第一步：收集所有直觉 ──
    all_details: list[dict] = []
    category_map: dict[str, list[int]] = {}  # category → detail indices
    total_ms = 0.0

    for category, cases in TEST_CASES.items():
        category_map[category] = []
        for case in cases:
            try:
                start = time.time()
                query_vec = encode_text(case["input"])
                result = get_social_intuition(case["input"], case["context"], query_vec=query_vec)
                elapsed = time.time() - start
            except Exception as e:
                elapsed = 0
                result = f"异常: {e}"

            total_ms += elapsed * 1000
            idx = len(all_details)
            category_map[category].append(idx)
            all_details.append({
                "input": case["input"],
                "criterion": case["criterion"],
                "context": case.get("context", ""),
                "output": result,
                "elapsed_ms": round(elapsed * 1000),
            })

    # ── 第二步：LLM 评判 ──
    if has_api:
        verdicts = asyncio.run(_llm_judge(all_details))
    else:
        verdicts = [False] * len(all_details)

    # ── 第三步：汇总 ──
    total = len(all_details)
    passed = sum(verdicts)
    category_scores: dict[str, dict] = {}

    for category, indices in category_map.items():
        cat_pass = sum(1 for i in indices if verdicts[i])
        cat_total = len(indices)
        category_scores[category] = {
            "total": cat_total,
            "passed": cat_pass,
            "rate": round(cat_pass / cat_total * 100, 1) if cat_total else 0,
        }

    if verbose:
        for category, indices in category_map.items():
            print(f"【{category}】")
            for i in indices:
                d = all_details[i]
                status = "OK" if verdicts[i] else "FAIL"
                print(f"  [{status}] {d['input']}  ({d['elapsed_ms']}ms)")
                if not verdicts[i]:
                    print(f"         期望: {d['criterion'][:80]}")
                    print(f"         直觉: {d['output'][:150] or '(空)'}")
            print(f"  ── {category}: {category_scores[category]['passed']}/{category_scores[category]['total']} "
                  f"({category_scores[category]['rate']}%)\n")

    # ── 快速通道 ──
    fast_total = 0
    fast_pass = 0
    if verbose:
        print("【快速通道验证】")
    for case in FAST_PATH_CASES:
        fast_total += 1
        try:
            result = get_social_intuition(case["input"], case["context"], query_vec=encode_text(case["input"]))
        except Exception as e:
            result = f"异常: {e}"
        has_output = bool(result and result != "异常")
        # 简易判断：非夜间时段快速通道返回空，LLM 不会触发
        if not result:
            if verbose:
                print(f"  [SKIP] {case['description']}")
        elif has_output:
            fast_pass += 1
            if verbose:
                print(f"  [OK] {case['description']}: {result[:80]}")
        else:
            if verbose:
                print(f"  [FAIL] {case['description']}")

    # ── 总报告 ──
    overall = round(passed / total * 100, 1) if total else 0
    avg_ms = round(total_ms / total, 0) if total else 0
    fast_note = f" | 快速通道: {fast_pass}/{fast_total}" if fast_total else ""
    print(f"\n{'='*50}")
    print(f"  评判方式: LLM 语义评判")
    print(f"  总评分: {passed}/{total} ({overall}%){fast_note}")
    print(f"  平均耗时: {avg_ms:.0f}ms/条")
    print(f"  API 状态: {'在线' if has_api else '离线'}")
    print(f"  已积累经验: {exp_count} 条")
    print(f"{'='*50}")

    print("\n  分类评分:")
    for cat, score in sorted(category_scores.items(), key=lambda x: -x[1]["rate"]):
        bar = "#" * int(score["rate"] / 10) + "-" * (10 - int(score["rate"] / 10))
        print(f"  {bar}  {cat}: {score['rate']}%")

    return {
        "judge_method": "llm",
        "overall_rate": overall,
        "passed": passed,
        "total": total,
        "avg_ms": avg_ms,
        "category_scores": category_scores,
        "fast_path": {"total": fast_total, "passed": fast_pass},
        "has_api": has_api,
        "exp_count": exp_count,
        "details": [{
            **{k: v for k, v in d.items() if k != "criterion"},
            "passed": verdicts[i],
        } for i, d in enumerate(all_details)],
    }


def save_report(report: dict, path: str = ""):
    if not path:
        from datetime import datetime
        path = f"eval_intuition_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告已保存: {path}")


if __name__ == "__main__":
    verbose = "--quiet" not in sys.argv
    report = evaluate(verbose=verbose)
    if "--save" in sys.argv:
        save_report(report)
