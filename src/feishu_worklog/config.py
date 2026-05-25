from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml


def _load_dotenv(env_file: Path) -> None:
    """把同目录 .env 里的 KEY=VALUE 注入 os.environ（已存在的不覆盖）。

    专为 CLAUDE_CODE_OAUTH_TOKEN 准备：claude CLI 每天会自动升级，新二进制的
    code identity 不在 macOS Keychain ACL 里，交互模式会弹「始终允许」框、
    launchd 非交互模式没法弹 → keychain 拒访问 → claude 拿不到 token → 403。
    用长期 token 透传 env，完全绕过 keychain。
    """
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


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
    raw_retention_days: int
    screenshot_retention_days: int
    db_retention_days: int

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

    # 加载同目录 .env（CLAUDE_CODE_OAUTH_TOKEN 等），让 claude CLI 子进程
    # 不再依赖 macOS Keychain，避开 claude 升级后 keychain ACL 失效的坑。
    _load_dotenv(p.with_name(".env"))

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
        raw_retention_days=int(raw.get("raw_retention_days", 30)),
        screenshot_retention_days=int(raw.get("screenshot_retention_days", 14)),
        db_retention_days=int(raw.get("db_retention_days", 60)),
    )

    for d in (cfg.data_dir, cfg.browser_state_dir, cfg.screenshots_dir, cfg.raw_dir):
        d.mkdir(parents=True, exist_ok=True)

    return cfg
