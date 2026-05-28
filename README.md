# feishu-worklog

每天定时抓取飞书里你参与并发言的私聊/群聊，用 Claude 按"工作主题"重组成工作日志，写入 Obsidian。

LLM 通过 `claude` CLI 子进程调用，复用你的 Claude Pro/Max 订阅（终端跑 `claude auth login` 走 claude.ai 浏览器登录即可），**无需单独申请 Anthropic API key**。

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

### 3. 登录 claude

```bash
claude auth login    # 走 claude.ai 浏览器授权一次即可
```

登录态会写到 macOS Keychain。launchd 跑时通过 plist 里设的
`PATH/HOME/USER/LOGNAME` 让非交互上下文也能定位并读取这条凭据
——四个变量必须齐，少一个 claude 就报 `403 Failed to authenticate`
（详见 `launchd/com.xiaolong.feishu-worklog.plist` 顶部注释）。

可选：`cp .env.example .env && chmod 600 .env`，把 `FEISHU_WEBHOOK_URL`
填上，launchd 跑挂时会推消息到飞书群；不配走 macOS 桌面通知兜底。

### 4. 飞书扫码登录

```bash
python -m scripts.login
```

打开一个有头浏览器扫码登录飞书。登录态保存在 `data/browser_state/`，
后续无须再登。

### 5. 装成自动任务

见下面「部署成自动任务」一节。

## 手动跑（调试用）

装完 launchd 后日常不需要手动跑。调试 / 补跑某天用：

```bash
python -m scripts.run_daily              # 跑今天
python -m scripts.run_daily 2026-05-18   # 跑指定日期
python -m scripts.run_daily 2026-05-18 --skip-crawl   # 跳过抓取，仅重新总结
```

## 工作日记保留期

工作日记只留最近 `retention_days` 天（默认 30）；超期的 `YYYY-MM-DD.md` 由 `run_daily`
顺手直接删除（按文件名日期判断，不浓缩、不留底）。只动 vault 根目录下严格命名的日记，
其它笔记不碰。

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
- launchd 跑报 `403 Failed to authenticate`：先在终端跑 `claude auth status` 看是否
  `loggedIn: true`；不是就重新 `claude auth login`。已登录还报 403 的话，检查
  `~/Library/LaunchAgents/com.xiaolong.feishu-worklog.plist` 的 `EnvironmentVariables`
  里是否齐了 `PATH/HOME/USER/LOGNAME` 四个 key——缺 `USER/LOGNAME` 就会让非交互的
  launchd 定位不到 keychain 条目。实时看下一次跑：`tail -F data/launchd.err.log`
