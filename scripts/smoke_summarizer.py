"""不依赖飞书的 summarizer smoke test。

喂一组 mock 消息给 Claude，验证：
  1. API key 可用、模型可调
  2. JSON 输出能被解析
  3. markdown 风格符合既有"工作日记"格式
  4. 主题字典更新逻辑跑通

用法：
    python -m scripts.smoke_summarizer

会写到 mybrain/工作日记/ 吗？—— **不会**。
这个脚本只把结果打印出来 + 存到 data/raw/smoke-*.md，不动你的 Obsidian。
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from feishu_worklog.config import load_config
from feishu_worklog.summarizer import summarize
from feishu_worklog.topics import TopicDict


def _mock_messages(date: str, my_name: str) -> list[dict]:
    """构造一组贴近真实场景的 mock 消息。"""
    base = int(datetime.strptime(date + " 09:00", "%Y-%m-%d %H:%M").timestamp())

    def m(seq, chat, ctype, sender, content, mins_offset, is_self=False):
        return {
            "id": f"mock-{chat}-{seq}",
            "date": date,
            "chat_id": f"mock::{chat}",
            "chat_name": chat,
            "chat_type": ctype,
            "sender": sender,
            "is_self": int(is_self),
            "content": content,
            "ts": base + mins_offset * 60,
            "seq": seq,
            "fetched_at": int(time.time()),
        }

    msgs: list[dict] = []
    # 群1：产研日常
    msgs += [
        m(1, "产研日常", "group", "Stephen", "Harbor 又挂了，CI push 镜像全失败", 0),
        m(2, "产研日常", "group", my_name, "我看下，可能要等运维重启", 2, True),
        m(3, "产研日常", "group", "Stephen", "已通知运维", 5),
        m(4, "产研日常", "group", my_name, "Harbor 恢复了，我在产研群同步一下", 25, True),
        m(5, "产研日常", "group", my_name, "顺手把今天卡住的 Jenkins 任务都 rerun 了", 27, True),
    ]
    # 私聊：和深康
    msgs += [
        m(1, "深康", "p2p", "深康", "Truss v1.3.1 想再发一次到 dev", 60),
        m(2, "深康", "p2p", my_name, "好，我来发", 61, True),
        m(3, "深康", "p2p", my_name, "deployed，回归一下", 95, True),
        m(4, "深康", "p2p", "深康", "ok 看到了", 96),
    ]
    # 群2：v1.4 讨论
    msgs += [
        m(1, "v1.4 测试群", "group", "Kevin", "v1.4 啥时候开始测", 180),
        m(2, "v1.4 测试群", "group", my_name, "需求还在收尾，本周内启动", 181, True),
        m(3, "v1.4 测试群", "group", "Kevin", "另外昨天那个权限 bug 麻烦帮看下", 182),
        m(4, "v1.4 测试群", "group", my_name, "记下了，今晚之前给反馈", 183, True),
    ]
    # 群3：闲聊（应该被过滤）
    msgs += [
        m(1, "午饭群", "group", "小张", "今天吃啥", 200),
        m(2, "午饭群", "group", my_name, "随便", 201, True),
        m(3, "午饭群", "group", "小张", "肯德基", 202),
    ]
    return msgs


def main() -> None:
    cfg = load_config(Path("config.yaml"))
    target = datetime.now().strftime("%Y-%m-%d")

    print(f"[smoke] target_date={target}, model={cfg.model}, my_name={cfg.my_name}")

    msgs = _mock_messages(target, cfg.my_name)
    print(f"[smoke] mock 消息 {len(msgs)} 条，{len({m['chat_name'] for m in msgs})} 个会话")

    topics = TopicDict(cfg.topics_path)
    print(f"[smoke] 现有主题 {len(topics.topics)} 个")

    print("[smoke] 调用 Claude…")
    markdown, update = summarize(cfg, target, msgs, topics)

    print()
    print("=" * 60)
    print("生成的 markdown：")
    print("=" * 60)
    print(markdown)
    print("=" * 60)
    print("topics 增量：")
    print(f"  used:    {update.get('used', [])}")
    print(f"  added:   {update.get('added', [])}")
    print(f"  renamed: {update.get('renamed', [])}")
    print("=" * 60)

    out = cfg.raw_dir / f"smoke-{target}-{datetime.now():%H%M%S}.md"
    out.write_text(markdown, encoding="utf-8")
    print(f"\n[smoke] 结果存档：{out}")
    print("（未写入 Obsidian。确认效果 OK 后再跑 run_daily.py）")


if __name__ == "__main__":
    main()
