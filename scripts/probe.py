"""DOM 探测工具 —— 在你登录飞书 web 并打开一个会话后，dump 真实的 DOM 结构。

用法：
    python -m scripts.probe

流程：
    1. 启动有头浏览器，加载已有登录态（没登过的话先扫码）
    2. 等你手动操作：
       - 确保左侧能看到会话列表
       - 点开一个**今天有过聊天的**会话（最好是有你自己发的消息的）
       - 在浏览器里**等消息全部加载出来**
    3. 回到终端按 Enter
    4. 脚本自动 dump：
       - 整页 HTML
       - 整页截图
       - 结构化探测结果 JSON（sidebar / 会话项 / 消息项的 class 列表 + 样本 outerHTML）

最后把 data/raw/probe-*.json 内容贴给我，我据此改 crawler 的 selector。
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from playwright.async_api import async_playwright

from feishu_worklog.config import load_config


PROBE_JS = r"""
() => {
    // ---- 工具 ----
    const visible = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };
    const pickClass = (el) => (el && el.className && typeof el.className === 'string') ? el.className : '';
    const trim = (s, n=400) => (s || '').replace(/\s+/g,' ').trim().slice(0, n);
    const desc = (el) => ({
        tag: el.tagName.toLowerCase(),
        cls: pickClass(el),
        role: el.getAttribute('role') || '',
        testid: el.getAttribute('data-testid') || '',
        text: trim(el.innerText || '', 120),
    });

    // ---- 1. 找候选 sidebar：左侧最高、最长的列表容器 ----
    const allDivs = Array.from(document.querySelectorAll('div, aside, section'));
    const sidebarCandidates = allDivs
        .filter(visible)
        .filter(el => {
            const r = el.getBoundingClientRect();
            return r.left < 50 && r.width > 200 && r.width < 500 && r.height > 400;
        })
        .map(el => ({...desc(el), rect: el.getBoundingClientRect().toJSON(), kidCount: el.children.length}))
        .slice(0, 6);

    // ---- 2. 找候选会话列表项：含多个相似结构的子节点 ----
    const sessionItemCandidates = [];
    for (const c of allDivs) {
        if (!visible(c)) continue;
        const kids = Array.from(c.children).filter(visible);
        if (kids.length < 5) continue;
        // 相似度：所有 kid 的 class 名一致或高度相似
        const firstCls = pickClass(kids[0]);
        if (!firstCls) continue;
        const same = kids.filter(k => pickClass(k) === firstCls).length;
        if (same >= 5 && same / kids.length > 0.7) {
            const r = c.getBoundingClientRect();
            if (r.left < 50 && r.width < 500) {
                sessionItemCandidates.push({
                    container: desc(c),
                    sampleItem: desc(kids[0]),
                    kidCount: kids.length,
                    sampleItemOuterHTML: kids[0].outerHTML.slice(0, 1500),
                });
            }
        }
    }

    // ---- 3. 找当前打开的会话的消息列表：右侧大区域里的相似子节点 ----
    const msgContainerCandidates = [];
    for (const c of allDivs) {
        if (!visible(c)) continue;
        const r = c.getBoundingClientRect();
        if (r.left < 300) continue;       // 排除 sidebar
        if (r.width < 500) continue;       // 主对话区一般够宽
        const kids = Array.from(c.children).filter(visible);
        if (kids.length < 3) continue;
        const sampleCls = pickClass(kids[0]);
        if (!sampleCls) continue;
        const same = kids.filter(k => pickClass(k) === sampleCls).length;
        if (same >= 3 && same / kids.length > 0.5) {
            msgContainerCandidates.push({
                container: desc(c),
                kidCount: kids.length,
                samples: kids.slice(0, 5).map(k => ({
                    cls: pickClass(k),
                    text: trim(k.innerText, 200),
                    outerHTML: k.outerHTML.slice(0, 2000),
                })),
            });
        }
    }

    // ---- 4. 找显式带 data-message-id 的元素（如果飞书 web 有的话）----
    const withMsgId = Array.from(document.querySelectorAll('[data-message-id]')).slice(0, 5)
        .map(el => ({...desc(el), outerHTML: el.outerHTML.slice(0, 2000)}));

    // ---- 5. 当前 URL ----
    return {
        url: location.href,
        title: document.title,
        viewport: {w: window.innerWidth, h: window.innerHeight},
        sidebarCandidates,
        sessionItemCandidates: sessionItemCandidates.slice(0, 5),
        msgContainerCandidates: msgContainerCandidates.slice(0, 5),
        withMsgId,
    };
}
"""


async def main() -> None:
    cfg = load_config(Path("config.yaml"))
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(cfg.browser_state_dir),
            headless=False,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = await ctx.new_page()
        await page.goto(cfg.feishu_url, wait_until="domcontentloaded")

        print()
        print("=" * 60)
        print("飞书 web 已打开。请按下列步骤操作：")
        print(" 1. 如未登录，扫码登录")
        print(" 2. 等左侧会话列表加载出来")
        print(" 3. 点开一个**今天有聊天的**会话")
        print(" 4. 滚动一下让今天的消息都加载出来")
        print(" 5. 回到这里，按 Enter 开始探测")
        print("=" * 60)
        await asyncio.to_thread(input, "准备好后按 Enter > ")

        print("\n[probe] 抓取 DOM 结构…")
        data = await page.evaluate(PROBE_JS)

        # 拍照 + 存 HTML
        png = cfg.screenshots_dir / f"probe-{ts}.png"
        html = cfg.screenshots_dir / f"probe-{ts}.html"
        json_path = cfg.raw_dir / f"probe-{ts}.json"

        await page.screenshot(path=str(png), full_page=True)
        html.write_text(await page.content(), encoding="utf-8")
        json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        print(f"[probe] 截图       : {png}")
        print(f"[probe] HTML       : {html}")
        print(f"[probe] 结构化探测 : {json_path}")
        print()
        print("把 probe-*.json 内容贴给 Claude 来调 selector。")
        print("（如果 JSON 很大，至少贴 sessionItemCandidates / msgContainerCandidates 这两段）")
        print()
        print("浏览器保持打开。检查完关闭窗口或 Ctrl-C 退出。")

        try:
            while len(ctx.pages) > 0:
                await asyncio.sleep(2)
        except KeyboardInterrupt:
            pass
        finally:
            await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
