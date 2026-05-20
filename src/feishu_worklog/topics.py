from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Topic:
    name: str
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    first_seen: str = ""
    last_used: str = ""
    count: int = 0


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
        self.topics = [Topic(**t) for t in data.get("topics", [])]

    def save(self) -> None:
        self.path.write_text(
            json.dumps(
                {"topics": [asdict(t) for t in self.topics]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def as_prompt_block(self) -> str:
        if not self.topics:
            return "（暂无积累的主题，请你自行归纳并新建）"
        lines = []
        for t in self.topics:
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

        just_added: set[str] = set()
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
            just_added.add(name)

        for name in update.get("used", []):
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
            t.last_used = today
            if name not in just_added:
                # 刚通过 added 建出来的已经 count=1，避免首日重复计数
                t.count += 1
            if not t.first_seen:
                t.first_seen = today

        self.topics = list(by_name.values())
