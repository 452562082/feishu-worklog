from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
from pathlib import Path

from ._atomic import write_text_atomic

# 超过这个天数没 used 的主题标 archived，不再注入 LLM prompt
_ARCHIVE_AFTER_DAYS = 90


@dataclass
class Topic:
    name: str
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    first_seen: str = ""
    last_used: str = ""
    count: int = 0
    archived: bool = False


class TopicDict:
    """主题字典 — 由 LLM 增量维护，落地到 JSON。"""

    def __init__(self, path: Path):
        self.path = path
        self.topics: list[Topic] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        # 容忍老 JSON 没有新字段（如 archived），也忽略未知字段
        valid_keys = {f.name for f in fields(Topic)}
        self.topics = [
            Topic(**{k: v for k, v in t.items() if k in valid_keys})
            for t in data.get("topics", [])
        ]

    def save(self) -> None:
        write_text_atomic(
            self.path,
            json.dumps(
                {"topics": [asdict(t) for t in self.topics]},
                ensure_ascii=False,
                indent=2,
            ),
        )

    def as_prompt_block(self) -> str:
        active = [t for t in self.topics if not t.archived]
        if not active:
            return "（暂无积累的主题，请你自行归纳并新建）"
        lines = []
        for t in active:
            aliases = f"（别名：{', '.join(t.aliases)}）" if t.aliases else ""
            lines.append(f"- **{t.name}**{aliases}：{t.description}")
        return "\n".join(lines)

    def apply_update(self, update: dict, today: str) -> None:
        """update 形如：
        {
          "used":   ["主题A", "主题B"],      # 今日实际命中的（含新主题）
          "added":  [                          # 新建的主题
            {"name": "...", "description": "...", "aliases": [...]}
          ],
          "renamed":[                          # 改名/合并的
            {"from": "...", "to": "..."}
          ]
        }
        """
        by_name = {t.name: t for t in self.topics}

        for r in update.get("renamed", []):
            old, new = r.get("from"), r.get("to")
            if old and old in by_name:
                t = by_name.pop(old)
                t.aliases = list({*t.aliases, old})
                t.name = new
                by_name[new] = t

        for n in update.get("added", []):
            name = n.get("name")
            if not name or name in by_name:
                continue
            t = Topic(
                name=name,
                description=n.get("description", ""),
                aliases=list(n.get("aliases", [])),
                first_seen=today,
                last_used=today,
                count=1,
            )
            by_name[name] = t

        # 保序去重 used（LLM 偶尔会重复列同一主题导致 count +2）
        for name in dict.fromkeys(update.get("used", [])):
            t = by_name.get(name)
            if t is None:
                # 保底：Claude 应该把新主题写进 added，但偶尔会漏；自动建条目
                t = Topic(
                    name=name,
                    description="",
                    aliases=[],
                    first_seen=today,
                    last_used=today,
                    count=1,
                )
                by_name[name] = t
                continue
            # 同一天已经计过（重跑 --skip-crawl / 手动多次触发）→ 不再 +1
            # last_used == today 既覆盖刚 added 的（count=1），也覆盖前面跑过的
            if t.last_used == today:
                t.archived = False
                continue
            t.last_used = today
            t.archived = False  # 复活：archived 主题如果今天又出现，恢复活跃
            t.count += 1
            if not t.first_seen:
                t.first_seen = today

        # 把超过 _ARCHIVE_AFTER_DAYS 没 used 的标 archived，
        # 避免主题字典越长越大塞爆 prompt
        try:
            today_d = datetime.strptime(today, "%Y-%m-%d").date()
            cutoff = (today_d - timedelta(days=_ARCHIVE_AFTER_DAYS)).isoformat()
            newly_archived = 0
            for t in by_name.values():
                if not t.archived and t.last_used and t.last_used < cutoff:
                    t.archived = True
                    newly_archived += 1
            if newly_archived > 0:
                import logging
                logging.getLogger(__name__).info(
                    "[topics] %d 个 > %d 天未用的主题已 archived",
                    newly_archived, _ARCHIVE_AFTER_DAYS,
                )
        except ValueError:
            pass

        self.topics = list(by_name.values())
