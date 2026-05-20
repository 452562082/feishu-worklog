"""把当日消息送给 Claude，按工作主题重组成 markdown，并增量沉淀主题字典。

通过 `claude` CLI 子进程调用，复用 Claude Code 的 OAuth 登录态，
不需要单独申请 Anthropic API key。
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .config import Config
from .topics import TopicDict

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是用户的私人工作日志助理。用户会把自己一天里在飞书上的聊天记录给你，
你的任务：
1. 识别"用户发的消息"（标注 【我】 的，或 sender 等于用户名的）
2. 结合上下文理解每条消息背后的工作内容
3. 按"工作主题"重新组织（不要按会话/时间），输出一份简洁、专业的工作日志 markdown
4. 同时返回主题字典的增量更新

写日志的风格要点：
- 用中文，简体
- 标题用 `# YYYY-MM-DD 周X` 格式
- 三个固定章节：`## 📋 今日工作概览`、`## 🔧 工作事项`、`## 📌 待跟进`
- 工作事项每条用 `### N. 主题名` 起头，下面用无序列表写要点
- 概览段两到三句话，提炼今天主要做的事
- 待跟进列尚未完成 / 需要明天/后续推进的事项
- 不要把消息原文复制进去；要做提炼和归纳
- 闲聊、纯表情、单字回复（"好"/"收到"）不算工作内容，过滤掉

主题字典：
- 我会给你现有的主题列表，你尽量复用，避免无谓新建
- 同一件事用最精炼的名字（如"Truss部署"而非"今天的Truss部署工作"）
- **used 列出今天涉及到的所有主题**（包括新主题和已有主题）
- **如果 used 里某个主题名不在现有字典里，必须同时出现在 added 数组里，并写明 description 和 aliases**
  这是硬性要求；首次运行字典为空时，used 里的每个名字都要在 added 里有对应记录
- 如果发现现有主题命名不好或想合并，用 renamed: [{"from": "...", "to": "..."}]

**严格的输出格式**（用分隔符切分，不要套代码块，不要任何额外说明）：

===MARKDOWN===
# 2026-05-19 周二

## 📋 今日工作概览
...（这里可以自由用引号、代码块、任何字符）

===TOPICS===
{"used": ["主题A", "主题B"], "added": [{"name": "...", "description": "...", "aliases": []}], "renamed": [{"from": "...", "to": "..."}]}
===END===

要求：
- TOPICS 段必须是合法 JSON，写在一行内，键值都用 ASCII 双引号
- MARKDOWN 段不限格式，任意字符都行
- 三个分隔符（===MARKDOWN===, ===TOPICS===, ===END===）必须独占一行，前后不要空格
"""


_DISALLOWED_TOOLS = (
    "Bash", "Read", "Edit", "Write", "Glob", "Grep",
    "WebFetch", "WebSearch", "Task", "TodoWrite",
    "NotebookEdit", "Skill", "Agent",
)


def build_user_prompt(
    target_date: str,
    weekday_cn: str,
    my_name: str,
    messages_by_chat: dict[str, list[dict]],
    topics_block: str,
) -> str:
    parts: list[str] = []
    parts.append(f"日期：{target_date}（{weekday_cn}）")
    parts.append(f"我的名字：{my_name}")
    parts.append("")
    parts.append("现有主题字典：")
    parts.append(topics_block)
    parts.append("")
    parts.append("=== 今日聊天记录（按会话分组）===")
    for chat_name, msgs in messages_by_chat.items():
        if not msgs:
            continue
        parts.append("")
        parts.append(f"--- 会话：{chat_name} ({msgs[0]['chat_type']}) ---")
        for m in msgs:
            mark = "【我】" if m["is_self"] else ""
            ts = datetime.fromtimestamp(m["ts"]).strftime("%H:%M")
            parts.append(f"[{ts}] {mark}{m['sender']}: {m['content']}")
    parts.append("")
    parts.append("请按 system prompt 要求，用 ===MARKDOWN=== / ===TOPICS=== / ===END=== 分隔符输出。")
    return "\n".join(parts)


def _weekday_cn(date_str: str) -> str:
    wd = datetime.strptime(date_str, "%Y-%m-%d").weekday()
    return "周" + "一二三四五六日"[wd]


_RETRYABLE_HINTS = (
    "ECONNRESET", "ETIMEDOUT", "ECONNREFUSED", "EAI_AGAIN",
    "socket hang up", "Unable to connect", "fetch failed",
    "503", "502", "504", "overloaded", "rate_limit",
)


def _is_retryable_envelope(envelope: dict) -> bool:
    """根据 envelope 判断是不是值得重试的临时故障。"""
    if not envelope.get("is_error"):
        return False
    msg = (envelope.get("result") or "") + " " + str(envelope.get("errors") or "")
    return any(hint in msg for hint in _RETRYABLE_HINTS)


def _call_claude(
    prompt: str,
    system: str,
    model: str,
    max_budget_usd: float,
    timeout: int = 240,
    max_attempts: int = 3,
) -> dict:
    """调 `claude -p`，返回 envelope JSON。临时网络抖动会自动指数退避重试。"""
    args = [
        "claude", "-p",
        "--output-format", "json",
        "--no-session-persistence",
        "--model", model,
        "--system-prompt", system,
        "--max-budget-usd", str(max_budget_usd),
        "--disallowedTools", *_DISALLOWED_TOOLS,
    ]
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        log.info(
            "调用 claude CLI（model=%s, timeout=%ds, 第 %d/%d 次）…",
            model, timeout, attempt, max_attempts,
        )
        try:
            res = subprocess.run(
                args, input=prompt, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            last_err = RuntimeError(f"claude CLI 超时（{timeout}s）")
            log.warning("claude CLI 超时，准备重试（%d/%d）", attempt, max_attempts)
        else:
            try:
                envelope = json.loads(res.stdout) if res.stdout else None
            except json.JSONDecodeError:
                envelope = None

            # 成功 + 非临时错误 → 直接返回/抛出，不重试
            if res.returncode == 0 and envelope and not envelope.get("is_error"):
                return envelope
            if envelope and envelope.get("is_error") and not _is_retryable_envelope(envelope):
                raise RuntimeError(
                    f"claude 报错: {envelope.get('result')}\n详情: {envelope.get('errors')}"
                )

            # 临时错误：进入重试
            if envelope and _is_retryable_envelope(envelope):
                last_err = RuntimeError(f"claude 临时故障: {envelope.get('result')}")
                log.warning(
                    "claude API 临时故障，准备重试（%d/%d）: %s",
                    attempt, max_attempts, envelope.get("result"),
                )
            else:
                last_err = RuntimeError(
                    f"claude CLI 退出码 {res.returncode}\n"
                    f"STDOUT:\n{res.stdout[:2000]}\n"
                    f"STDERR:\n{res.stderr[:2000]}"
                )
                # 非 envelope 类的错误也试一次重试
                log.warning("claude CLI 非预期退出，准备重试（%d/%d）", attempt, max_attempts)

        if attempt < max_attempts:
            backoff = 2 ** (attempt - 1) * 5  # 5s, 10s, 20s
            log.info("等待 %ds 后重试…", backoff)
            time.sleep(backoff)

    assert last_err is not None
    raise last_err


def summarize(
    cfg: Config,
    target_date: str,
    messages: list[dict],
    topics: TopicDict,
    raw_prefix: str = "",
) -> tuple[str, dict]:
    """返回 (markdown, topics_update_dict)。

    raw_prefix：raw 留底文件名前缀，smoke 测试传 "smoke-" 避免覆盖真跑数据。
    """
    by_chat: dict[str, list[dict]] = {}
    for m in messages:
        by_chat.setdefault(m["chat_name"], []).append(m)

    user_prompt = build_user_prompt(
        target_date=target_date,
        weekday_cn=_weekday_cn(target_date),
        my_name=cfg.my_name,
        messages_by_chat=by_chat,
        topics_block=topics.as_prompt_block(),
    )

    # 留底 prompt 输入（出错可重跑）
    (cfg.raw_dir / f"{raw_prefix}{target_date}-prompt.txt").write_text(user_prompt, encoding="utf-8")

    envelope = _call_claude(
        prompt=user_prompt,
        system=SYSTEM_PROMPT,
        model=cfg.model,
        max_budget_usd=cfg.max_budget_usd,
        timeout=cfg.llm_timeout_seconds,
    )
    text = (envelope.get("result") or "").strip()
    (cfg.raw_dir / f"{raw_prefix}{target_date}-response.txt").write_text(text, encoding="utf-8")

    usage = envelope.get("usage", {})
    log.info(
        "tokens in=%s out=%s cache_create=%s 成本=$%.4f",
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("cache_creation_input_tokens"),
        envelope.get("total_cost_usd", 0.0),
    )

    markdown, topics_update = _split_delimited(text)
    return markdown, topics_update


def _split_delimited(s: str) -> tuple[str, dict]:
    """按 ===MARKDOWN=== / ===TOPICS=== / ===END=== 切分。"""
    s = s.strip()
    # 容错：万一 LLM 给套了 ``` 代码块
    if s.startswith("```"):
        lines = s.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)

    md_marker = "===MARKDOWN==="
    tp_marker = "===TOPICS==="
    end_marker = "===END==="
    i_md = s.find(md_marker)
    i_tp = s.find(tp_marker)
    if i_md < 0 or i_tp < 0 or i_tp <= i_md:
        raise RuntimeError(
            f"分隔符缺失：md={i_md} tp={i_tp}。前 300 字：\n{s[:300]}"
        )
    markdown = s[i_md + len(md_marker):i_tp].strip()

    i_end = s.find(end_marker, i_tp)
    topics_raw = s[i_tp + len(tp_marker):(i_end if i_end > 0 else len(s))].strip()
    try:
        topics_update = json.loads(topics_raw)
    except json.JSONDecodeError as e:
        log.warning("TOPICS JSON 解析失败，跳过主题更新：%s\n原始：%s", e, topics_raw[:300])
        topics_update = {}
    return markdown, topics_update
