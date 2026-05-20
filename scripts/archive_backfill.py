"""一次性把所有 > retention_days 的工作日记归档到长期记忆。

用法：
    python -m scripts.archive_backfill          # 跑全部过期
    python -m scripts.archive_backfill 5        # 只跑最多 5 天
    python -m scripts.archive_backfill --dry    # 只打印待处理列表，不动文件
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from feishu_worklog.archiver import archive_due, find_expired
from feishu_worklog.config import load_config
from feishu_worklog.main import _setup_logging


def main() -> None:
    _setup_logging()
    cfg = load_config(Path("config.yaml"))

    args = sys.argv[1:]
    dry = any(a == "--dry" for a in args)
    limit = None
    for a in args:
        if a.isdigit():
            limit = int(a)
            break

    expired = find_expired(cfg)
    if not expired:
        print("没有过期日记需要处理")
        return
    print(f"待归档 {len(expired)} 天:")
    for fp in expired:
        print(f"  - {fp.name}")

    if dry:
        print("\n--dry 模式，不执行")
        return

    print()
    n = archive_due(cfg, limit=limit)
    print(f"\n完成：归档了 {n} 天")


if __name__ == "__main__":
    main()
