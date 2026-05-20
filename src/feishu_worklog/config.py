from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Config:
    my_name: str
    obsidian_path: Path
    data_dir: Path
    model: str
    max_budget_usd: float
    feishu_url: str
    max_chats: int
    context_messages_before: int
    context_messages_after: int
    scroll_pause_ms: int
    headless: bool
    slow_mo_ms: int
    retention_days: int
    llm_timeout_seconds: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "messages.db"

    @property
    def topics_path(self) -> Path:
        return self.data_dir / "topics.json"

    @property
    def browser_state_dir(self) -> Path:
        return self.data_dir / "browser_state"

    @property
    def screenshots_dir(self) -> Path:
        return self.data_dir / "screenshots"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def archive_dir(self) -> Path:
        return self.obsidian_path / "归档"

    @property
    def long_term_dir(self) -> Path:
        return self.obsidian_path / "长期记忆"


def load_config(path: str | Path = "config.yaml") -> Config:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"{p} 不存在，先 cp config.example.yaml config.yaml 并填好"
        )
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))

    if not shutil.which("claude"):
        raise RuntimeError(
            "找不到 `claude` 命令。本项目通过 Claude Code CLI 调用 LLM（复用 OAuth），"
            "需要先安装 Claude Code。"
        )

    data_dir = Path(raw["data_dir"]).expanduser().resolve()
    obsidian_path = Path(raw["obsidian_path"]).expanduser()

    cfg = Config(
        my_name=raw["my_name"],
        obsidian_path=obsidian_path,
        data_dir=data_dir,
        model=raw.get("model", "claude-haiku-4-5"),
        max_budget_usd=float(raw.get("max_budget_usd", 1.0)),
        feishu_url=raw.get("feishu_url", "https://www.feishu.cn/messenger/"),
        max_chats=int(raw.get("max_chats", 30)),
        context_messages_before=int(raw.get("context_messages_before", 3)),
        context_messages_after=int(raw.get("context_messages_after", 1)),
        scroll_pause_ms=int(raw.get("scroll_pause_ms", 600)),
        headless=bool(raw.get("headless", False)),
        slow_mo_ms=int(raw.get("slow_mo_ms", 0)),
        retention_days=int(raw.get("retention_days", 14)),
        llm_timeout_seconds=int(raw.get("llm_timeout_seconds", 240)),
    )

    for d in (cfg.data_dir, cfg.browser_state_dir, cfg.screenshots_dir, cfg.raw_dir):
        d.mkdir(parents=True, exist_ok=True)

    return cfg
