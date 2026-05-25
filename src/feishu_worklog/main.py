"""端到端编排：抓 → 存 → 总结 → 写 Obsidian。"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from .archiver import archive_due
from .config import Config, load_config
from .crawler import crawl, open_login
from .notify import notify_failure
from .obsidian import write_daily
from .storage import Storage
from .summarizer import summarize
from .topics import TopicDict


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _pick_catchup_date(cfg: Config, lookback: int = 3) -> str | None:
    """找最近 lookback 天里"工作日记"目录缺失的**最近一天**。

    规则：
    - 今天（如果当前时间 < 18:00）跳过，认为还在进行中
    - 优先补最近的缺失日（一次跑一天，再次触发会接着补更早的）
    - 全都有了返回 None
    - lookback 默认 3 天 —— 飞书 web 翻太久的历史也不可靠
    """
    now = datetime.now()
    today = now.date()
    skip_today = now.hour < 18

    for i in range(0, lookback + 1):
        if skip_today and i == 0:
            continue
        d = today - timedelta(days=i)
        if not (cfg.obsidian_path / f"{d.isoformat()}.md").exists():
            return d.isoformat()
    return None


def run(
    date: str | None = None,
    skip_crawl: bool = False,
    catch_up: bool = False,
) -> None:
    _setup_logging()
    log = logging.getLogger("main")
    cfg = load_config(Path("config.yaml"))

    if catch_up and not date:
        picked = _pick_catchup_date(cfg)
        if picked is None:
            log.info("最近 3 天都有工作日记，无需补跑，退出")
            return
        target = picked
        log.info("catch-up 模式：自动选择日期 %s", target)
    else:
        target = date or _today()
        log.info("目标日期：%s", target)

    storage = Storage(cfg.db_path)
    storage.start_run(target)

    ok = False
    done = False
    msg_count = 0
    try:
        if not skip_crawl:
            msgs = asyncio.run(crawl(cfg, target))
            inserted = storage.insert_messages(m.to_row() for m in msgs)
            log.info("抓取 %d 条；新入库 %d 条", len(msgs), inserted)

        rows = storage.messages_for_date(target)
        msg_count = len(rows)
        if not rows:
            log.warning("当日没有抓到任何消息，跳过总结")
            storage.finish_run(target, 0, True)
            done = True
            return

        topics = TopicDict(cfg.topics_path)
        markdown, topics_update = summarize(cfg, target, rows, topics)
        topics.apply_update(topics_update, target)
        topics.save()

        out = write_daily(cfg.obsidian_path, target, markdown)
        log.info("完成：%s", out)
        ok = True
    except Exception:
        # launchd 跑挂了用户看不到 stderr，发条通知出去
        notify_failure(traceback.format_exc(), target_date=target)
        raise
    finally:
        if not done:
            storage.finish_run(target, msg_count, ok)

    # 顺手归档一天（limit=1，避免单次跑太久；多天积压会自然在多次跑中处理掉）
    try:
        archive_due(cfg, limit=1)
    except Exception as e:
        log.warning("归档过程出错（不影响今日产出）: %s", e)

    # 清理过期敏感数据（raw prompt/response、截图、原始消息）
    try:
        _cleanup_retention(cfg, storage)
    except Exception as e:
        log.warning("retention 清理出错（不影响今日产出）: %s", e)


def _cleanup_retention(cfg: Config, storage: Storage) -> None:
    """按 cfg 配置清理过期文件 + DB。"""
    log = logging.getLogger("main")

    def sweep_dir(d: Path, days: int, label: str) -> None:
        if not d.exists() or days <= 0:
            return
        cutoff = time.time() - days * 86400
        n = 0
        for fp in d.iterdir():
            if not fp.is_file():
                continue
            try:
                if fp.stat().st_mtime < cutoff:
                    fp.unlink()
                    n += 1
            except OSError as e:
                log.debug("删除 %s 失败：%s", fp, e)
        if n > 0:
            log.info("[retention] %s 删除 %d 个 > %d 天的文件", label, n, days)

    sweep_dir(cfg.raw_dir, cfg.raw_retention_days, "raw/")
    sweep_dir(cfg.screenshots_dir, cfg.screenshot_retention_days, "screenshots/")

    if cfg.db_retention_days > 0:
        cutoff_date = (datetime.now().date()
                       - timedelta(days=cfg.db_retention_days)).isoformat()
        deleted = storage.cleanup_old_messages(cutoff_date)
        if deleted > 0:
            log.info("[retention] messages.db 删除 %d 条 < %s 的消息",
                     deleted, cutoff_date)


def login() -> None:
    _setup_logging()
    cfg = load_config(Path("config.yaml"))
    asyncio.run(open_login(cfg))


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "login":
        login()
    else:
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
