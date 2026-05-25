# feishu-worklog

每天定时抓取飞书里你参与并发言的私聊/群聊，用 Claude 按"工作主题"重组成工作日志，写入 Obsidian。

LLM 通过 `claude` CLI 子进程调用，**复用 Claude Code 的 OAuth 登录态，无需单独申请 Anthropic API key**。

## 工作流

```
launchd 10:00（错过自动补跑 / 登录时也跑）
  └─ Playwright (持久化登录态)
       └─ 遍历最近会话 → 抓今日消息 → 标记"我发的" + 上下文
            └─ SQLite 落原始数据（可重跑）
                 └─ `claude -p` (默认 Haiku 4.5, ~$1/月)
                      └─ 写 mybrain/工作日记/YYYY-MM-DD.md
                           └─ Google Drive 自动同步
```

主题字典 (`data/topics.json`) 由 LLM 每天增量沉淀，第一周可能抖，越久越稳。

## 安装

> ⚠️ 前置：系统里已安装 `claude` (Claude Code CLI)，
> 终端跑 `claude --version` 能看到版本号即 OK。登录与否都行——
> 下面第 3 步的脚本会走完整的浏览器授权。

### 1. 装依赖

```bash
cd /Users/xiaolong/go/src/feishu-worklog
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. 配 config.yaml

```bash
cp config.example.yaml config.yaml
# 编辑 config.yaml，至少填 my_name 和 obsidian_path
```

### 3. 配 OAuth token（launchd 自动跑必需）

```bash
./scripts/setup_oauth_token.sh
```

脚本会跑 `claude setup-token`（浏览器授权 → 复制 code 粘回终端），
再用 `script(1)` 录输出、grep `sk-ant-oat01-...` 自动写入 `.env`。

为什么这步不能省：`claude` CLI 每天会自动升级一版，每个新版本一个新代码签名，
macOS Keychain ACL 不认 → 交互终端会弹「始终允许」框、点一下就更新 ACL；
但 launchd 非交互模式弹不出框 → keychain 拒访问 → 403。
长期 token 走 env 传给 claude 子进程，完全绕开 keychain。

`.env` 已 `.gitignore`，权限 600。也可顺手把可选的 `FEISHU_WEBHOOK_URL`
填上（跑挂时发飞书消息提醒，没配走 macOS 桌面通知兜底）。

### 4. 飞书扫码登录

```bash
python -m scripts.login
```

打开一个有头浏览器扫码登录飞书。登录态保存在 `data/browser_state/`，
后续无须再登。

### 5.（可选）装成自动任务

见下面 [部署成自动任务](#部署成自动任务-macos--launchd) 一节。

## 每日运行

```bash
python -m scripts.run_daily              # 跑今天
python -m scripts.run_daily 2026-05-18   # 跑指定日期
```

## 长期记忆 / 归档

工作日记保留 `retention_days` 天（默认 14）；超期的会自动：
- 浓缩成每主题一条时间线条目，追加到 `工作日记/长期记忆/{主题名}.md`
- 原日记移到 `工作日记/归档/YYYY-MM-DD.md` 留底

归档由 `run_daily` 顺手执行（每次最多 1 天，多天积压会渐进处理）。
也可一次性补：

```bash
python -m scripts.archive_backfill --dry    # 先看会处理哪些
python -m scripts.archive_backfill          # 实际跑
python -m scripts.archive_backfill 5        # 最多跑 5 天
```

## 部署成自动任务（macOS / launchd）

电脑关盖 cron 跑不了，用 launchd LaunchAgent：每天 10:00 触发，错过会在系统唤醒后补跑，
登录时也跑一次。脚本带 `--catch-up`，自动找最近 3 天里缺的最近一天补上。

```bash
./scripts/install_launchd.sh install     # 装好并启动
./scripts/install_launchd.sh status      # 看状态
./scripts/install_launchd.sh logs        # tail 运行日志
./scripts/install_launchd.sh reload      # 改完 plist 后重新加载
./scripts/install_launchd.sh uninstall   # 卸载
```

注意：macOS 可能弹一次"允许 LaunchAgent 运行后台任务"的窗，同意即可。

## 排错

- 抓不到消息：去 `data/screenshots/` 看抓取过程的截图
- DOM 变了：`src/feishu_worklog/crawler.py` 里的 selector 列表写了 fallback，加一个新候选即可
- LLM 输出格式坏：`data/raw/YYYY-MM-DD.json` 留有 prompt 输入，可重跑 `python -m scripts.run_daily --skip-crawl`
- launchd 跑报 `403 Failed to authenticate`：claude CLI 升级导致 keychain ACL 失效，
  按上面"配 OAuth token"做一次即可。也可手动测：`tail -F data/launchd.err.log` 看下一次跑
