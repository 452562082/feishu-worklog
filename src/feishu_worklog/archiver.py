"""把过期工作日记浓缩到主题长期记忆文件。

流程：
  1. 找到 ≥ retention_days 天前的 .md
  2. 解析它的"### N. 主题名"标题，确定涉及主题
  3. 调 Claude 把每个主题的内容浓缩成 1-3 行时间线条目
  4. 追加到 长期记忆/{主题}.md（按日期倒序，最新在上）
  5. 原日记移到 归档/

设计取舍：只让 Claude 生成"今日新增条目"，不重写整个长期文件 —— 省 token，
也避免历史被覆盖。新条目以 `- YYYY-MM-DD ...` 开头，append 即可。
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from ._atomic import write_text_atomic
from .config import Config
from .summarizer import _call_claude  # 复用 claude CLI 调用

log = logging.getLogger(__name__)


ARCHIVE_SYSTEM_PROMPT = """你是用户工作日志的归档助理。用户给你一份某天的工作日记 markdown，
你的任务：把它按"长期记忆主题"浓缩，每个主题输出 1-3 条最关键的条目。

要点：
- 只保留对未来回溯有价值的：关键决策、里程碑、配置参数、问题/解决方案、人名/系统名、API 路径、版本号
- 闲聊、纯过程性的临时状态、重复信息过滤掉
- 同一主题在这天的多条相关内容尽量合并成 1-2 行
- 每条最多一句话，简洁

**严格输出格式**（不要套代码块，不要额外说明）：

===TOPIC: 主题名===
- 关键事项1
- 关键事项2

===TOPIC: 另一个主题名===
- 关键事项

===END===

要求：
- 主题名严格使用工作日记里 "### N. 主题名" 用的名字（去掉 N. 序号）
- 不要新建主题
- 同一主题的多条条目共用一个 ===TOPIC=== 块
- 条目以 - 开头，不要加日期前缀（系统会自动加）
"""


def parse_topics_from_markdown(md: str) -> list[str]:
    """从 markdown 里抽取 '### N. 主题名' 的主题名列表。"""
    out: list[str] = []
    for line in md.splitlines():
        m = re.match(r"^###\s+\d+\.\s+(.+?)\s*$", line)
        if m:
            out.append(m.group(1).strip())
    return out


def _parse_archive_output(text: str) -> dict[str, list[str]]:
    """把 ===TOPIC: X=== / ===END=== 切分输出解析成 {主题: [行...]}。"""
    text = text.strip()
    # 容错：剥掉代码块
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)

    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^===TOPIC:\s*(.+?)\s*===$", s)
        if m:
            current = m.group(1).strip()
            out.setdefault(current, [])
            continue
        if s == "===END===":
            current = None
            continue
        if current and s.startswith("-"):
            # 去掉 leading "- " 留正文
            out[current].append(s[1:].strip())
    return {k: v for k, v in out.items() if v}


def _append_to_long_term(
    long_term_dir: Path, topic: str, date_iso: str, items: list[str]
) -> Path | None:
    """把 items 追加到 长期记忆/{topic}.md 的时间线，最新在上。

    幂等：如果文件里已经存在以 `- {date_iso} ` 开头的行，认为这天已经归档过了，
    直接 return None，避免双归档（步骤 2 成功步骤 3 失败时下次跑会再触发）。
    """
    long_term_dir.mkdir(parents=True, exist_ok=True)
    # 主题名做文件名安全处理：过滤特殊字符 + 限长（macOS/Linux 文件名 ≤ 255 字节）
    safe_name = re.sub(r'[/\\:<>"|?*]', "_", topic).strip() or "未命名"
    # UTF-8 一个中文 3 字节，80 字符 ≈ 240 字节，留点余量给 .md 后缀
    safe_name = safe_name[:80]
    fp = long_term_dir / f"{safe_name}.md"

    existing = fp.read_text(encoding="utf-8") if fp.exists() else ""

    # 幂等检查：这一天的条目是不是已经在了
    date_marker = f"- {date_iso} "
    if date_marker in existing:
        log.info("[archive]   ↳ %s 已有 %s 的条目，跳过重复归档", topic, date_iso)
        return None

    new_block = "\n".join(f"- {date_iso} {it}" for it in items)

    if not existing:
        content = f"# {topic}\n\n## 时间线\n\n{new_block}\n"
    elif "## 时间线" in existing:
        # 在 ## 时间线 之后立即插入（保持最新在上）
        head, sep, body = existing.partition("## 时间线")
        body = body.lstrip("\n")
        content = f"{head}{sep}\n\n{new_block}\n\n{body}".rstrip() + "\n"
    else:
        content = existing.rstrip() + f"\n\n## 时间线\n\n{new_block}\n"

    write_text_atomic(fp, content)
    return fp


def find_expired(cfg: Config) -> list[Path]:
    """找出 obsidian_path 下名为 YYYY-MM-DD.md 且超期的文件。"""
    if not cfg.obsidian_path.exists():
        return []
    today = datetime.now().date()
    cutoff = today - timedelta(days=cfg.retention_days)
    expired: list[Path] = []
    for fp in cfg.obsidian_path.glob("*.md"):
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})\.md$", fp.name)
        if not m:
            continue
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            continue
        if d < cutoff:
            expired.append(fp)
    return sorted(expired, key=lambda p: p.name)


def archive_one_day(cfg: Config, md_path: Path) -> dict:
    """归档一天的工作日记。返回 {topic: 条目数} 的统计。"""
    date_iso = md_path.stem  # YYYY-MM-DD
    md = md_path.read_text(encoding="utf-8")
    topics = parse_topics_from_markdown(md)
    if not topics:
        log.info("[archive] %s 里没识别到主题，跳过浓缩，仅移动到归档", date_iso)
        _move_to_archive(cfg, md_path)
        return {}

    user_prompt = (
        f"日期：{date_iso}\n"
        f"这天涉及的主题（必须严格使用以下名字）：{', '.join(topics)}\n\n"
        f"工作日记原文：\n{md}\n\n"
        f"请按 system prompt 要求输出 ===TOPIC=== 分隔的浓缩条目。"
    )

    log.info("[archive] 浓缩 %s（%d 个主题）", date_iso, len(topics))
    envelope = _call_claude(
        prompt=user_prompt,
        system=ARCHIVE_SYSTEM_PROMPT,
        model=cfg.model,
        max_budget_usd=cfg.max_budget_usd,
        timeout=cfg.llm_timeout_seconds,
    )
    text = (envelope.get("result") or "").strip()
    log.info(
        "[archive] tokens in=%s out=%s 成本=$%.4f",
        envelope.get("usage", {}).get("input_tokens"),
        envelope.get("usage", {}).get("output_tokens"),
        envelope.get("total_cost_usd", 0.0),
    )

    by_topic = _parse_archive_output(text)
    by_topic = _reconcile_topic_names(by_topic, topics)
    stats: dict[str, int] = {}
    for topic, items in by_topic.items():
        if not items:
            continue
        fp = _append_to_long_term(cfg.long_term_dir, topic, date_iso, items)
        if fp is None:
            continue  # 幂等：这天已归档过，跳过
        stats[topic] = len(items)
        log.info("[archive]   ↳ %s += %d 条 → %s", topic, len(items), fp.name)

    _move_to_archive(cfg, md_path)
    return stats


def _normalize_topic(s: str) -> str:
    """主题名规范化用于匹配：去空白 + lower。"""
    return re.sub(r"\s+", "", s).lower()


def _reconcile_topic_names(
    by_topic: dict[str, list[str]], expected: list[str]
) -> dict[str, list[str]]:
    """LLM 偶尔会把主题名拼错或改写（多/少空格、换字符），
    导致归档时新建一个跟"## 时间线"已有的不同文件。
    这里做精确 + 规范化匹配把它们对齐到 expected 里的 canonical name。
    完全找不到对应的 → warn + 丢弃，不写新文件。
    """
    expected_set = set(expected)
    norm_to_canonical = {_normalize_topic(t): t for t in expected}
    out: dict[str, list[str]] = {}
    for name, items in by_topic.items():
        if name in expected_set:
            canonical = name
        else:
            canonical = norm_to_canonical.get(_normalize_topic(name))
            if canonical is None:
                log.warning(
                    "[archive] LLM 输出未知主题 %r（不在工作日记的 ### 列表里），跳过",
                    name,
                )
                continue
            log.info("[archive] 主题名规范化：%r → %r", name, canonical)
        out.setdefault(canonical, []).extend(items)
    return out


def _move_to_archive(cfg: Config, md_path: Path) -> None:
    cfg.archive_dir.mkdir(parents=True, exist_ok=True)
    dst = cfg.archive_dir / md_path.name
    # 如果归档目录已有同名，加 .N 后缀避免覆盖
    if dst.exists():
        for i in range(1, 100):
            candidate = cfg.archive_dir / f"{md_path.stem}.{i}{md_path.suffix}"
            if not candidate.exists():
                dst = candidate
                break
    shutil.move(str(md_path), str(dst))
    log.info("[archive] 原日记 → %s", dst)


def archive_due(cfg: Config, limit: int | None = None) -> int:
    """归档所有过期日记。limit 限制一次跑最多几天（None = 全跑）。返回处理数。"""
    expired = find_expired(cfg)
    if limit is not None:
        expired = expired[:limit]
    if not expired:
        log.info("[archive] 没有过期日记需要处理")
        return 0
    log.info("[archive] 待归档 %d 天: %s",
             len(expired), [p.stem for p in expired])
    done = 0
    for fp in expired:
        try:
            archive_one_day(cfg, fp)
            done += 1
        except Exception as e:
            log.warning("[archive] %s 归档失败: %s", fp.name, e)
    return done
