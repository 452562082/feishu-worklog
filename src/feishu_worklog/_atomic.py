"""共享小工具：原子写文件。"""
from __future__ import annotations

import os
from pathlib import Path


def write_text_atomic(target: Path, content: str, encoding: str = "utf-8") -> None:
    """先写 .tmp 再 os.replace 换名 —— 断电/kill 中途要么旧版要么新版，不会有半截文件。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(content, encoding=encoding)
    os.replace(tmp, target)
