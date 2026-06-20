"""
v2 运维守护：端口监控 + HTTP 健康检查 + 自动重启 + 自动备份。
合并原系统 ops_daemon.py、guard.py、backup.py 三个文件。

铁律：每函数 ≤ 20 行，先处理异常，零硬编码，零外部依赖（仅标准库）。
"""

import json
import os
import traceback
import sys
import time
import socket
import shutil
import logging
import sqlite3
import hashlib
import subprocess
from datetime import datetime
from pathlib import Path

# 项目根
_ROOT = Path(__file__).parent

# 日志
LOG_DIR = _ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logger = logging.getLogger("v2_ops")
logger.setLevel(logging.INFO)
_handler = logging.FileHandler(LOG_DIR / "ops.log", encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_handler)

# ===== 无全局缓存，每次巡检独立发起 HTTP 请求 =====


# ---------- 配置（纯标准库，零外部依赖） ----------
def _parse_value(raw: str):
    """字符串转 Python 类型。'true'→True, '123'→123, '3.0'→3.0。"""
    v = raw.strip().strip("\"'")
    if v == "true":
        return True
    if v == "false":
        return False
    if v.isdigit():
        return int(v)
    if v.replace(".", "", 1).isdigit():
        return float(v)
    return v


def _parse_config(path: Path) -> dict:
    """解析简化 YAML 配置。只支持 section: + key: value 两层嵌套。"""
    result = {}
    current_section = None
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                stripped = line.rstrip()
                if not stripped or stripped.lstrip().startswith("#"):
                    continue
                indent = len(line) - len(line.lstrip())
                if indent == 0 and stripped.endswith(":"):
                    current_section = stripped[:-1].strip()
                    result[current_section] = {}
                elif indent >= 2 and current_section and ":" in stripped:
                    k, v = stripped.split(":", 1)
                    # 去掉行内注释（# 之后的内容）
                    if "#" in v:
                        v = v.split("#", 1)[0]
                    result[current_section][k.strip()] = _parse_value(v)
        return result
    except Exception:
        return {}

# 启动时一次性加载配置
_CONFIG = _parse_config(_ROOT / "config" / "config.yaml")


def _read_config(section: str, key: str, default):
    """从已加载的配置字典读取。纯内存操作，永不报错。"""
    try:
        return _CONFIG.get(section, {}).get(key, default)
    except Exception:
        return default


# ---------- 端口检查 ----------
def _check_port(host: str, port: int) -> bool:
    """检查端口是否在监听。纯标准库，不依赖 psutil。"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception as e:
        logger.error(f"端口检查异常 [{host}:{port}]: {e}")
        return False


# ---------- HTTP 健康检查 ----------
def _check_health(host: str, port: int) -> tuple:
    """通过 HTTP /health 端点检查服务健康。返回 (alive: bool, detail: str)。"""
    try:
        import urllib.request
        url = f"http://{host}:{port}/health"
        with urllib.request.urlopen(url, timeout=5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode())
                return (data.get("status") == "ok", str(data))
            return (False, f"HTTP {resp.status}")
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}")


# ---------- 备份 ----------
_LAST_BACKUP = 0.0


def _prune_old_backups(backup_root: Path, keep: int):
    """删除旧备份，只保留最近 keep 个。"""
    try:
        backups = sorted(backup_root.iterdir(), key=lambda x: x.stat().st_mtime)
        for old in backups[:-keep]:
            shutil.rmtree(old)
    except Exception as e:
        logger.error(f"清理旧备份失败: {type(e).__name__}: {e}")


_CHUNK_SIZE = 4 * 1024 * 1024  # MD5 分块校验大小


def _verify_backup(src: Path, dest: Path) -> bool:
    """MD5 分块校验备份文件完整性，支持大文件。"""

    def _md5(p: Path) -> str | None:
        try:
            h = hashlib.md5()
            with open(p, "rb") as f:
                while chunk := f.read(_CHUNK_SIZE):
                    h.update(chunk)
            return h.hexdigest()
        except Exception as e:
            logger.error(f"MD5 计算失败: {p} ({e})")
            return None

    sm = _md5(src)
    dm = _md5(dest)
    if sm is None or dm is None:
        return False
    if sm != dm:
        logger.error(f"备份校验失败: {dest} (MD5 不匹配)")
        return False
    return True


def _backup_sqlite_db(src: Path, dest: Path) -> bool:
    """SQLite 在线备份 API，避免复制正在写入的文件。"""
    try:
        with sqlite3.connect(str(src)) as sc, sqlite3.connect(str(dest)) as dc:
            sc.backup(dc)
        return True
    except Exception as e:
        logger.error(f"SQLite 备份失败: {src} → {dest} ({e})")
        return False


def _backup_data():
    """备份 data/config/memory.db 到本地 + D:\\beifen。校验失败回滚。"""
    global _LAST_BACKUP
    now = time.time()
    if now - _LAST_BACKUP < _read_config("ops", "backup_interval", 10800):
        return

    start_time = time.time()
    db_src = _ROOT / "memory.db"
    data_src = _ROOT / "data"
    config_src = _ROOT / "config"
    tag = datetime.now().strftime("%Y%m%d_%H%M%S")

    def _do_backup(dest_root: Path, keep: int, label: str) -> bool:
        dest = dest_root / tag
        dest.mkdir(parents=True, exist_ok=True)
        try:
            if db_src.exists():
                db_dest = dest / "memory.db"
                if not _backup_sqlite_db(db_src, db_dest):
                    shutil.rmtree(dest, ignore_errors=True)
                    logger.error(f"{label}备份失败: DB 备份异常，已清理临时文件")
                    return False
                if not _verify_backup(db_src, db_dest):
                    shutil.rmtree(dest, ignore_errors=True)
                    logger.error(f"{label}备份失败: MD5 校验不通过，已清理临时文件")
                    return False
            if data_src.exists():
                shutil.copytree(data_src, dest / "data", dirs_exist_ok=True)
            if config_src.exists():
                shutil.copytree(config_src, dest / "config", dirs_exist_ok=True)
            _prune_old_backups(dest_root, keep)
            return True
        except Exception as e:
            shutil.rmtree(dest, ignore_errors=True)
            logger.error(f"{label}备份异常: {type(e).__name__}: {e}")
            return False

    remote_path = os.environ.get("BAIJIN_REMOTE_BACKUP") or _read_config("ops", "remote_backup", "")
    if remote_path:
        remote_path = Path(remote_path)
    local_ok = _do_backup(_ROOT / "backup", _read_config("ops", "backup_keep", 7), "本地")
    remote_ok = _do_backup(remote_path, _read_config("ops", "backup_keep", 5), "异地") if remote_path else False

    elapsed = time.time() - start_time
    if local_ok and (not remote_path or remote_ok):
        _LAST_BACKUP = now
        (_ROOT / "backup_failed.flag").unlink(missing_ok=True)
        remote_label = str(remote_path) if remote_path else "无"
        logger.info(f"备份完成: 本地 + {remote_label} ({tag})，耗时 {elapsed:.1f}s")
    else:
        with open(_ROOT / "backup_failed.flag", "w", encoding="utf-8") as f:
            f.write(f"备份失败: {datetime.now().isoformat()}\n")
            f.write(f"本地: {'成功' if local_ok else '失败'}\n")
            f.write(f"异地: {'成功' if remote_ok else '失败'}\n")
        logger.error(f"备份部分失败（耗时 {elapsed:.1f}s），详情见 backup_failed.flag")

    # 备份时顺带清理过期情绪（独立短连接，with 自动关闭）
    try:
        from memory_engine.config import DB_PATH
        from memory_engine.emotion import cleanup_expired

        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row

            class _LightDB:
                """轻量 DB 包装。"""
                def query(self, sql, params=()):
                    return [dict(r) for r in conn.execute(sql, params).fetchall()]
            db = _LightDB()
            db.conn = conn
            n = cleanup_expired(db)
            if n:
                logger.info(f"过期情绪清理: {n} 条")
    except Exception as e:
        logger.debug(f"过期情绪清理跳过: {e}")


# ---------- 重启 ----------
def _kill_port(port: int):
    """杀掉所有占用指定端口的进程。"""
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=5,
        )
        killed = set()
        for line in result.stdout.split("\n"):
            if f":{port}" not in line or "LISTENING" not in line:
                continue
            pid = line.strip().split()[-1]
            if not pid.isdigit() or pid in killed:
                continue
            killed.add(pid)
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=5)
            logger.info(f"终止进程 PID={pid}")
    except subprocess.TimeoutExpired:
        logger.error("netstat 超时")
    except Exception as e:
        logger.error(f"终止进程异常: {type(e).__name__}: {e}")


def _start_service():
    """启动 v2 主服务。"""
    try:
        subprocess.Popen(
            [sys.executable, str(_ROOT / "main.py")],
            cwd=str(_ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        logger.info("服务已重启")
    except Exception as e:
        logger.error(f"启动失败: {type(e).__name__}: {e}")


def _restart_service(port: int):
    """重启 v2 主服务：先杀端口再启动。"""
    _kill_port(port)
    _start_service()


# ---------- 主循环 ----------
def _tick(host: str, port: int):
    """单次巡检：端口 + HTTP 健康检查 + 备份。"""
    port_ok = _check_port(host, port)
    if port_ok:
        alive, detail = _check_health(host, port)
    else:
        alive, detail = False, "端口不通"

    if not alive:
        logger.warning(f"异常: 端口={port_ok} 健康={detail}")
        _restart_service(port)

    _backup_data()


def run():
    """主循环：按配置间隔巡检。"""
    host = _read_config("server", "host", "127.0.0.1")
    port = _read_config("server", "port", 8000)
    logger.info("v2 运维守护已启动")

    while True:
        try:
            _tick(host, port)
        except Exception as e:
            logger.error(f"巡检异常: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        time.sleep(_read_config("ops", "check_interval", 30))


# ---------- 入口 ----------
if __name__ == "__main__":
    host = _read_config("server", "host", "127.0.0.1")
    port = _read_config("server", "port", 8000)
    if not _check_port(host, port):
        logger.info("主服务未运行，尝试启动...")
        _restart_service(port)
    run()
