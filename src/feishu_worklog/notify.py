"""失败时通知用户。

launchd 跑挂了用户看不到——日志只能事后翻 data/launchd.err.log。
按优先级走两条通道，命中一条即返回；通知本身失败不向上抛：

  1. 飞书自定义机器人 webhook（FEISHU_WEBHOOK_URL，需在飞书群里建机器人）
  2. macOS osascript 通知（兜底）
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.request

log = logging.getLogger(__name__)


def notify_failure(message: str, target_date: str | None = None) -> None:
    """通知失败信息。message 通常是 traceback 文本。"""
    title = f"飞书工作日记跑挂了（{target_date}）" if target_date else "飞书工作日记跑挂了"
    # 取最后一行非空内容当摘要——traceback 的最末行通常是真正的异常类型+原因
    summary = ""
    for line in reversed(message.strip().splitlines()):
        if line.strip():
            summary = line.strip()[:300]
            break

    if _try_feishu_webhook(title, summary):
        return
    _try_macos_notify(title, summary)


def _try_feishu_webhook(title: str, summary: str) -> bool:
    url = os.environ.get("FEISHU_WEBHOOK_URL")
    if not url:
        return False
    payload = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": [
                        [{"tag": "text", "text": summary}],
                        [{"tag": "text", "text": "完整堆栈见 data/launchd.err.log"}],
                    ],
                }
            }
        },
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("飞书通知已发送")
            resp.read()
        return True
    except Exception as e:
        log.warning("飞书 webhook 发送失败：%s", e)
        return False


def _try_macos_notify(title: str, summary: str) -> bool:
    safe_title = title.replace('"', "'").replace("\\", "")
    safe_body = summary.replace('"', "'").replace("\\", "")
    try:
        subprocess.run(
            [
                "osascript", "-e",
                f'display notification "{safe_body}" with title "{safe_title}"',
            ],
            timeout=5,
            check=False,
        )
        return True
    except Exception as e:
        log.warning("macOS 通知发送失败：%s", e)
        return False
