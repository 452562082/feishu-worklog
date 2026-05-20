from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)


def write_daily(obsidian_path: Path, date: str, markdown: str) -> Path:
    """写入 mybrain/工作日记/YYYY-MM-DD.md。已存在则备份成 .bak。"""
    obsidian_path.mkdir(parents=True, exist_ok=True)
    target = obsidian_path / f"{date}.md"
    if target.exists():
        bak = target.with_suffix(".md.bak")
        shutil.copy2(target, bak)
        log.info("已有 %s，备份为 %s", target.name, bak.name)
    target.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    log.info("已写入 %s", target)
    return target
