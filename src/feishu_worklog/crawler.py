"""飞书 web 抓取。

DOM 结构（已用 probe 实测确认）：
  会话列表项     .a11y_feed_card_item
  消息项         .js-message-item.message-item
  我发的         .message-self
  别人发的       .message-not-self
  私聊/群聊       .message-is-p2p / .message-is-group
  时间          .message-timestamp（文本如 "16:21"）
  内容          .message-content（innerText）
  消息 id        元素的 id 属性（雪花 ID，全局唯一）
  日期分隔条     .date-divider（隔天才出现）
  侧边栏         .chatSidebar_sidebar

is_self 用 classList 判定，准确率 100%。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from playwright.async_api import (
    BrowserContext,
    Locator,
    Page,
    async_playwright,
)

from .config import Config

log = logging.getLogger(__name__)


# === Selectors（基于真实 probe）===
# 登录态检测：会话列表里至少出现一个 item 即认为已登录
SEL_LOGGED_IN_PROBE = ".a11y_feed_card_item, .feed-main-list, .a11y_feed_main_list"
SEL_SESSION_ITEM = ".a11y_feed_card_item"
SEL_MESSAGE_ITEM = ".js-message-item"


@dataclass
class RawMessage:
    id: str
    chat_id: str
    chat_name: str
    chat_type: str          # 'p2p' | 'group'
    sender: str
    is_self: bool
    content: str
    ts: int                 # unix seconds
    seq: int
    date: str               # YYYY-MM-DD

    def to_row(self) -> dict:
        return asdict(self)


# ---------- 公共入口 ----------

async def crawl(cfg: Config, target_date: str) -> list[RawMessage]:
    """抓取 target_date（YYYY-MM-DD）当天的消息。"""
    out: list[RawMessage] = []
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(cfg.browser_state_dir),
            headless=cfg.headless,
            slow_mo=cfg.slow_mo_ms,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = await ctx.new_page()
        try:
            await _open_feishu(page, cfg)
            await _ensure_logged_in(page, cfg)
            chats = await _list_recent_chats(page, cfg)
            log.info("识别到 %d 个会话", len(chats))

            for idx, chat in enumerate(chats):
                try:
                    msgs = await _scrape_one_chat(page, chat, target_date, cfg)
                    log.info(
                        "[%d/%d] %s -> %d 条今日消息（含我 %d）",
                        idx + 1, len(chats), chat["name"],
                        len(msgs), sum(1 for m in msgs if m.is_self),
                    )
                    out.extend(msgs)
                except Exception as e:
                    log.warning("抓取会话失败 %s: %s", chat.get("name"), e)
                    await _snapshot(page, cfg, f"err-chat-{idx}")
        finally:
            await ctx.close()
    return out


async def open_login(cfg: Config) -> None:
    """有头模式打开飞书，让用户扫码登录；登录态保存到 user_data_dir。"""
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(cfg.browser_state_dir),
            headless=False,
            viewport={"width": 1280, "height": 800},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = await ctx.new_page()
        await page.goto(cfg.feishu_url, wait_until="domcontentloaded")
        print("已打开飞书 web。扫码登录后，关闭浏览器窗口或按 Ctrl-C 结束。")
        try:
            while len(ctx.pages) > 0:
                await asyncio.sleep(2)
        except KeyboardInterrupt:
            pass
        finally:
            await ctx.close()


# ---------- 内部步骤 ----------

async def _open_feishu(page: Page, cfg: Config) -> None:
    await page.goto(cfg.feishu_url, wait_until="domcontentloaded", timeout=30000)
    # 飞书 web 加载较慢，sidebar 出现即可
    await asyncio.sleep(2)


async def _ensure_logged_in(page: Page, cfg: Config) -> None:
    # 飞书会从 www.feishu.cn 跳到 ${tenant}.feishu.cn/next/messenger/，可能要几秒
    try:
        await page.wait_for_selector(SEL_LOGGED_IN_PROBE, timeout=30000, state="attached")
        # 再等列表里真出现 item
        await page.wait_for_selector(SEL_SESSION_ITEM, timeout=15000)
    except Exception:
        await _snapshot(page, cfg, "not-logged-in")
        try:
            cur_url = page.url
        except Exception:
            cur_url = "?"
        raise RuntimeError(
            f"等不到会话列表。当前 URL={cur_url}。\n"
            "可能原因：1) 登录态过期 → 跑 `python -m scripts.login` 重新扫码；"
            "2) 网络慢 → 调大 timeout；3) 飞书 DOM 变了 → 看 data/screenshots/"
        )


# 从一个会话条提取名字 + 稳定 id 的核心逻辑（list/click 两处共用）。
# 名字过滤：跳过纯数字（未读 badge）、HH:MM 时间戳、群标签（"机器人"/"外部"等）
_SESSION_INFO_FN_JS = r"""
function extractSessionInfo(el) {
    const SKIP_TAGS = new Set(["机器人","外部","普通群","超级群","外部群","内部","已置顶"]);
    const main = el.querySelector('.a11y_feed_card_main') || el;
    const lines = (main.innerText || '').split(/\n/).map(s => s.trim()).filter(Boolean);
    let name = '';
    for (const line of lines) {
        if (/^\d+$/.test(line)) continue;                  // 未读数字
        if (/^\d{1,2}:\d{1,2}$/.test(line)) continue;      // 时间 HH:MM
        if (/^(昨天|前天|星期[一二三四五六日天])$/.test(line)) continue;
        if (/^\d{1,2}\/\d{1,2}$/.test(line)) continue;     // 日期 5/19
        if (SKIP_TAGS.has(line)) continue;
        name = line.slice(0, 60);
        break;
    }
    if (!name) return null;
    const img = el.querySelector('img');
    const avatar = img ? (img.getAttribute('src') || '') : '';
    let h = 0;
    const seed = avatar + '||' + name;
    for (let i = 0; i < seed.length; i++) {
        h = ((h << 5) - h + seed.charCodeAt(i)) | 0;
    }
    return {name, key: String(h)};
}
"""

_EXTRACT_SESSIONS_JS = "(sel) => {" + _SESSION_INFO_FN_JS + r"""
    const out = [];
    for (const el of document.querySelectorAll(sel)) {
        const info = extractSessionInfo(el);
        if (info) out.push(info);
    }
    return out;
}"""

_CLICK_SESSION_BY_KEY_JS = "([sel, want_key]) => {" + _SESSION_INFO_FN_JS + r"""
    for (const el of document.querySelectorAll(sel)) {
        const info = extractSessionInfo(el);
        if (info && info.key === want_key) {
            el.click();
            return true;
        }
    }
    return false;
}"""


async def _scroll_chat_list(page: Page, dy: int = 500) -> None:
    """在会话列表内滚动（用鼠标滚轮，绕开 simplebar 的怪脾气）。"""
    box = await page.locator(SEL_SESSION_ITEM).first.bounding_box()
    if not box:
        return
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    await page.mouse.move(cx, cy)
    await page.mouse.wheel(0, dy)


async def _list_recent_chats(page: Page, cfg: Config) -> list[dict[str, Any]]:
    # 等会话列表至少出现一些 item
    await page.wait_for_selector(SEL_SESSION_ITEM, timeout=15000)
    for _ in range(20):
        n = await page.locator(SEL_SESSION_ITEM).count()
        if n >= 5:
            break
        await asyncio.sleep(0.25)

    # 先滚到顶（最新会话）
    await page.evaluate(
        """() => {
            const c = document.querySelector('.simplebar-content-wrapper');
            if (c) c.scrollTop = 0;
        }"""
    )
    await asyncio.sleep(0.5)

    seen: dict[str, dict] = {}
    no_new_count = 0
    for batch in range(15):
        items = await page.evaluate(_EXTRACT_SESSIONS_JS, SEL_SESSION_ITEM)
        before = len(seen)
        for it in items:
            if it["key"] not in seen and it["name"]:
                seen[it["key"]] = {"name": it["name"], "key": it["key"]}
        log.debug("batch %d: dom=%d, seen=%d", batch, len(items), len(seen))
        if len(seen) >= cfg.max_chats:
            break
        if len(seen) == before:
            no_new_count += 1
            if no_new_count >= 2:
                break
        else:
            no_new_count = 0
        # 鼠标滚轮往下
        try:
            await _scroll_chat_list(page, 500)
        except Exception as e:
            log.debug("scroll 失败: %s", e)
        await asyncio.sleep(cfg.scroll_pause_ms / 1000)

    log.info("收集到 %d 个唯一会话", len(seen))
    return [{"name": v["name"], "chat_id": "h:" + k, "key": v["key"]}
            for k, v in seen.items()]


async def _scrape_one_chat(
    page: Page, chat: dict, target_date: str, cfg: Config
) -> list[RawMessage]:
    # 找到 key 匹配的 session item 并点击（避免重名问题）
    clicked = await page.evaluate(
        _CLICK_SESSION_BY_KEY_JS,
        [SEL_SESSION_ITEM, chat["key"]],
    )
    if not clicked:
        log.debug("跳过：%s（不在当前视口）", chat["name"])
        return []

    # 等消息区出现
    try:
        await page.wait_for_selector(SEL_MESSAGE_ITEM, timeout=6000)
    except Exception:
        log.debug("%s: 没等到 .js-message-item", chat["name"])
        return []
    await asyncio.sleep(0.8)

    # 滚动加载到能看到 target_date 的范围
    today_iso = datetime.now().strftime("%Y-%m-%d")
    await _scroll_to_load_date(page, target_date, today_iso, cfg)

    # 一次性提取所有消息，按 .date-divider 推断每条消息的归属日期。
    # 关键：当首个 divider 不在 DOM（虚拟列表回收）时，推断"首 divider 之前的消息"
    # 是"首 divider 日期 - 1 天"，这样 catch-up 翻历史时不会丢消息。
    raw_list: list[dict] = await page.evaluate(
        r"""([today_iso]) => {
            const today = new Date(today_iso + 'T12:00:00');
            const ymd = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
            const daysAgo = n => {
                const d = new Date(today.getTime());
                d.setDate(d.getDate() - n);
                return ymd(d);
            };
            function parseDividerDate(text) {
                if (!text) return null;
                text = text.trim();
                if (text === '今天') return today_iso;
                if (text === '昨天') return daysAgo(1);
                if (text === '前天') return daysAgo(2);
                let m = text.match(/^(\d{4})年(\d{1,2})月(\d{1,2})日/);
                if (m) return `${m[1]}-${String(m[2]).padStart(2,'0')}-${String(m[3]).padStart(2,'0')}`;
                m = text.match(/^(\d{1,2})月(\d{1,2})日/);
                if (m) return `${today.getFullYear()}-${String(m[1]).padStart(2,'0')}-${String(m[2]).padStart(2,'0')}`;
                m = text.match(/^(\d{1,2})\/(\d{1,2})/);
                if (m) return `${today.getFullYear()}-${String(m[1]).padStart(2,'0')}-${String(m[2]).padStart(2,'0')}`;
                return null;  // 纯 HH:MM 间隔，不是日期
            }
            const all = Array.from(document.querySelectorAll('.date-divider, .js-message-item'));

            // 先扫一遍找出第一个有效的 date-divider，用它来推断"在它之前的消息"的日期
            let firstDivIdx = -1;
            let firstDivDate = null;
            for (let i = 0; i < all.length; i++) {
                const cls = all[i].className || '';
                if (cls.includes('date-divider') && !cls.includes('js-message-item')) {
                    const d = parseDividerDate((all[i].textContent || '').trim());
                    if (d) { firstDivIdx = i; firstDivDate = d; break; }
                }
            }
            // pre-first 默认：今天前一天（启发式；适合 catch-up yesterday 的常见场景）
            let preFirstDate = daysAgo(1);
            if (firstDivDate) {
                const fd = new Date(firstDivDate + 'T12:00:00');
                fd.setDate(fd.getDate() - 1);
                preFirstDate = ymd(fd);
            }

            let currentDate = preFirstDate;
            const out = [];
            for (let i = 0; i < all.length; i++) {
                const el = all[i];
                const cls = el.className || '';
                if (cls.includes('date-divider') && !cls.includes('js-message-item')) {
                    const txt = (el.textContent || '').trim();
                    const d = parseDividerDate(txt);
                    if (d) currentDate = d;
                    continue;
                }
                const is_self = cls.includes('message-self');
                const is_p2p  = cls.includes('message-is-p2p');
                const tnode = el.querySelector('.message-timestamp');
                const time_text = tnode ? (tnode.textContent || '').trim() : '';
                const cnode = el.querySelector('.message-content');
                let text = cnode ? (cnode.textContent || '').trim() : (el.textContent || '').trim();
                const uname = el.querySelector('.user-name, .message-username, .larkc-username, [class*="username"]');
                const sender = uname ? (uname.textContent || '').trim() : '';
                out.push({
                    id: el.id || '',
                    is_self, is_p2p, time_text, text, sender,
                    msg_date: currentDate,
                });
            }
            return out;
        }""",
        [today_iso],
    )

    # 判定本会话整体 chat_type（看第一条非空消息的 class hint）
    chat_type = "p2p"
    for r in raw_list:
        if r["time_text"]:
            chat_type = "p2p" if r["is_p2p"] else "group"
            break

    out: list[RawMessage] = []
    skipped_no_text = skipped_no_ts = skipped_wrong_date = 0
    seq = 0
    for r in raw_list:
        text = (r.get("text") or "").strip()
        if not text:
            skipped_no_text += 1
            continue
        # 飞书的 .message-content innerText 一般不带 timestamp；保险起见再清一遍
        tt = r.get("time_text", "")
        if tt and text.startswith(tt):
            text = text[len(tt):].lstrip(" :：\n")
        if not text:
            continue

        msg_date = r.get("msg_date") or target_date
        ts = _parse_ts(tt, msg_date)
        if ts is None:
            # 飞书对连续同一发送者只在第一条显示 .message-timestamp，其它是空的。
            # 既然消息所在区间的 divider 已经定位了 msg_date，按当天中午 12:00 顶个 ts 保留。
            try:
                ts = int(datetime.strptime(msg_date + " 12:00", "%Y-%m-%d %H:%M").timestamp())
            except Exception:
                skipped_no_ts += 1
                continue
        if msg_date != target_date:
            skipped_wrong_date += 1
            continue

        is_self = bool(r["is_self"])
        sender = r["sender"] or (cfg.my_name if is_self else (
            chat["name"] if chat_type == "p2p" else "群成员"
        ))

        msg_id = r["id"] or hashlib.md5(
            f"{chat['chat_id']}|{tt}|{text[:60]}".encode()
        ).hexdigest()[:16]
        seq += 1
        out.append(RawMessage(
            id=f"{chat['chat_id']}::{msg_id}",
            chat_id=chat["chat_id"],
            chat_name=chat["name"],
            chat_type=chat_type,
            sender=sender,
            is_self=is_self,
            content=text,
            ts=ts,
            seq=seq,
            date=msg_date,
        ))
    if len(raw_list) > 0 and len(out) == 0:
        # 抓到了消息但全被过滤，提到 INFO 方便排查
        log.info(
            "  └ %s: 视口里 %d 条 message-item, 全部丢弃 (no_text=%d no_ts=%d not_today=%d)",
            chat["name"], len(raw_list), skipped_no_text, skipped_no_ts,
            skipped_wrong_date,
        )
    return out


def _parse_divider_text(text: str, today_iso: str) -> str | None:
    """飞书日期分隔条文本 → YYYY-MM-DD。HH:MM 时间间隔返回 None。"""
    import re
    from datetime import date, timedelta
    text = text.strip()
    if not text:
        return None
    if text == "今天":
        return today_iso
    today_d = date.fromisoformat(today_iso)
    if text == "昨天":
        return (today_d - timedelta(days=1)).isoformat()
    if text == "前天":
        return (today_d - timedelta(days=2)).isoformat()
    m = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
    m = re.match(r"^(\d{1,2})月(\d{1,2})日", text)
    if m:
        return f"{today_d.year}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}"
    m = re.match(r"^(\d{1,2})/(\d{1,2})", text)
    if m:
        return f"{today_d.year}-{m.group(1).zfill(2)}-{m.group(2).zfill(2)}"
    return None


async def _scroll_to_load_date(
    page: Page, target_date: str, today_iso: str, cfg: Config
) -> None:
    """滚动加载历史，直到已加载到 target_date 之前的某天。

    关键技巧：飞书消息列表用自定义滚动容器（simplebar），scrollIntoView 不可靠。
    实测有效做法是：找到 .js-message-item 沿父链向上第一个 scrollHeight > clientHeight
    的祖先元素，直接 .scrollTop = 0 翻到顶。每次设 0 后 simplebar 会触发懒加载，
    新消息插进 DOM 顶部后 scrollTop 自动变成 ~ 加载内容的高度，再设 0 继续翻。
    """
    target_d = datetime.strptime(target_date, "%Y-%m-%d").date()
    is_catchup = target_d < datetime.now().date()
    max_attempts = 80 if is_catchup else 25

    # 注入一个"找滚动容器"的函数到 window，后续重复用
    await page.evaluate(
        r"""() => {
            window.__findScroller = (item) => {
                let el = item ? item.parentElement : null;
                while (el) {
                    if (el.scrollHeight > el.clientHeight + 5) return el;
                    el = el.parentElement;
                }
                return null;
            };
        }"""
    )

    # 1) 先到底（让虚拟列表把当前位置 anchor 到最新消息）
    await page.evaluate(
        r"""(sel) => {
            const items = document.querySelectorAll(sel);
            if (!items.length) return;
            const sc = window.__findScroller(items[0]);
            if (sc) sc.scrollTop = sc.scrollHeight;
        }""",
        SEL_MESSAGE_ITEM,
    )
    await asyncio.sleep(cfg.scroll_pause_ms / 1000)

    # 2) 反复滚到顶，直到任一 divider 的日期 < target_date 或者无新内容
    last_count = -1
    last_top_id = ""
    no_change = 0
    found_target = False

    for attempt in range(max_attempts):
        info = await page.evaluate(
            r"""(sel) => {
                const items = document.querySelectorAll(sel);
                const dividers = Array.from(document.querySelectorAll('.date-divider'))
                    .map(d => (d.textContent || '').trim());
                let scTop = -1, scHeight = -1;
                if (items.length) {
                    const sc = window.__findScroller(items[0]);
                    if (sc) { scTop = sc.scrollTop; scHeight = sc.scrollHeight; }
                }
                const topId = items.length
                    ? (items[0].id || (items[0].textContent || '').slice(0, 30))
                    : '';
                return {count: items.length, dividers, topId, scTop, scHeight};
            }""",
            SEL_MESSAGE_ITEM,
        )

        # 见到 target 当天或更早的 divider 都算 OK，更早就可以停了
        for d_text in info["dividers"]:
            d = _parse_divider_text(d_text, today_iso)
            if not d:
                continue
            try:
                divider_d = datetime.strptime(d, "%Y-%m-%d").date()
            except ValueError:
                continue
            if divider_d <= target_d:
                found_target = True
            if divider_d < target_d:
                log.debug(
                    "scroll: 见到 %s < %s, 停 (attempt=%d, count=%d)",
                    d, target_date, attempt, info["count"],
                )
                return

        # 顶部 anchor + 总数 都不变 → 真到顶了
        if info["count"] == last_count and info["topId"] == last_top_id:
            no_change += 1
            if no_change >= 4:
                log.debug(
                    "scroll: 顶部不变 4 轮，停 (attempt=%d, count=%d, found_target=%s)",
                    attempt, info["count"], found_target,
                )
                break
        else:
            no_change = 0
        last_count = info["count"]
        last_top_id = info["topId"]

        # 触发懒加载：scrollTop=0
        await page.evaluate(
            r"""(sel) => {
                const items = document.querySelectorAll(sel);
                if (!items.length) return;
                const sc = window.__findScroller(items[0]);
                if (sc) sc.scrollTop = 0;
            }""",
            SEL_MESSAGE_ITEM,
        )
        # 翻历史时给懒加载更多时间
        wait_ms = cfg.scroll_pause_ms * (1.5 if is_catchup else 1.0)
        await asyncio.sleep(wait_ms / 1000)


def _parse_ts(s: str, target_date: str) -> int | None:
    """飞书时间格式都是 'HH:MM'；其它格式宽松匹配。"""
    if not s:
        return None
    s = s.strip()
    fmts = ["%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"]
    for f in fmts:
        try:
            dt = datetime.strptime(s, f)
            if f == "%H:%M":
                y, m, d = map(int, target_date.split("-"))
                dt = dt.replace(year=y, month=m, day=d)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None


async def _snapshot(page: Page, cfg: Config, tag: str) -> None:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    png = cfg.screenshots_dir / f"{ts}-{tag}.png"
    html = cfg.screenshots_dir / f"{ts}-{tag}.html"
    try:
        # 仅 viewport：登录/会话失败的诊断信息基本都在首屏，full_page 在连续失败时
        # 累计 IO 抖动明显
        await page.screenshot(path=str(png), full_page=False)
        html.write_text(await page.content(), encoding="utf-8")
        log.warning("已 dump 截图/HTML 到 %s", png)
    except Exception as e:
        log.error("dump 截图失败: %s", e)
