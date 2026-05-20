"""跑一次：抓飞书 → 总结 → 写 Obsidian。

  python -m scripts.run_daily                # 跑今天
  python -m scripts.run_daily 2026-05-18     # 跑指定日期
  python -m scripts.run_daily --skip-crawl   # 跳过抓取，仅重新总结+写盘
  python -m scripts.run_daily --catch-up     # 自动找最近 3 天里缺的日期补跑（启动唤醒后跑）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from feishu_worklog.main import run

if __name__ == "__main__":
    args = sys.argv[1:]
    date = None
    skip = False
    catch = False
    for a in args:
        if a == "--skip-crawl":
            skip = True
        elif a == "--catch-up":
            catch = True
        elif a and not a.startswith("--"):
            date = a
    run(date=date, skip_crawl=skip, catch_up=catch)
