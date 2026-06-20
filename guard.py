"""
v2 轻量守护 —— 仅检查 v2_ops.py 是否存活，挂了就拉起。
由 Cron 每 10 分钟触发。不做巡检循环（v2_ops 自己做）。
"""
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parent


def _find_v2_ops():
    """检查 v2_ops.py 进程是否存活。"""
    try:
        result = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "commandline"],
            capture_output=True, text=True, timeout=10
        )
        return "v2_ops.py" in result.stdout
    except Exception:
        return False


def main():
    if _find_v2_ops():
        return  # 存活，静默退出

    # 挂了，拉起
    subprocess.Popen(
        [sys.executable, str(_ROOT / "v2_ops.py")],
        cwd=str(_ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


if __name__ == "__main__":
    main()
