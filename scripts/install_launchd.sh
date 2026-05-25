#!/bin/bash
# 安装 / 卸载 LaunchAgent，让 feishu-worklog 每天自动跑（含开盖唤醒补跑）
#
# 用法：
#   ./scripts/install_launchd.sh install     # 构建 .app + 装好并立即加载
#   ./scripts/install_launchd.sh uninstall   # 停掉并卸载
#   ./scripts/install_launchd.sh status      # 看状态
#   ./scripts/install_launchd.sh reload      # 改完 plist 后重新加载
#   ./scripts/install_launchd.sh logs        # tail 看运行日志

set -euo pipefail

LABEL="com.xiaolong.feishu-worklog"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC_PLIST="$PROJECT_DIR/launchd/$LABEL.plist"
DEST_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

cmd="${1:-status}"

_check_env_token() {
  # launchd 是非交互环境，读不了 macOS Keychain。必须先用 .env 里的
  # CLAUDE_CODE_OAUTH_TOKEN 把 claude CLI 的 auth 走 env 传过去，否则装好
  # 第一次跑就会撞 403。
  local env_file="$PROJECT_DIR/.env"
  if [ ! -f "$env_file" ] \
     || ! grep -qE '^CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-' "$env_file"; then
    echo "✗ .env 里没看到有效的 CLAUDE_CODE_OAUTH_TOKEN" >&2
    echo "  launchd 是非交互环境读不了 keychain，必须先配 OAuth token。" >&2
    echo "  跑一下：./scripts/setup_oauth_token.sh" >&2
    echo "  然后再来 install。" >&2
    exit 1
  fi
}

case "$cmd" in
  install)
    _check_env_token
    # 先构建 FeishuWorklog.app（plist 指向它的启动器）
    "$PROJECT_DIR/launchd/build_app.sh"
    mkdir -p "$HOME/Library/LaunchAgents"
    cp "$SRC_PLIST" "$DEST_PLIST"
    # 用 launchctl bootstrap（新写法），失败兜底用 load
    if launchctl bootstrap "gui/$(id -u)" "$DEST_PLIST" 2>/dev/null; then
      echo "✓ bootstrap 成功"
    else
      launchctl load "$DEST_PLIST"
      echo "✓ load 成功"
    fi
    echo "已安装：$DEST_PLIST"
    echo "触发时机：用户登录 / 开盖唤醒 / 手动 kickstart"
    echo
    echo "⚠️  还需一步：系统设置 → 隐私与安全性 → 完全磁盘访问权限，"
    echo "    把 $PROJECT_DIR/FeishuWorklog.app 加进去并打开开关。"
    ;;
  uninstall)
    if [ -f "$DEST_PLIST" ]; then
      launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null \
        || launchctl unload "$DEST_PLIST" 2>/dev/null || true
      rm -f "$DEST_PLIST"
      echo "✓ 已卸载"
    else
      echo "未安装"
    fi
    ;;
  reload)
    # build_app.sh 是幂等的：.app 没过期就跳过，不会动 cdhash、不影响已授的 FDA
    "$PROJECT_DIR/launchd/build_app.sh"
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null \
      || launchctl unload "$DEST_PLIST" 2>/dev/null || true
    cp "$SRC_PLIST" "$DEST_PLIST"
    launchctl bootstrap "gui/$(id -u)" "$DEST_PLIST" \
      || launchctl load "$DEST_PLIST"
    echo "✓ 已重新加载"
    ;;
  status)
    if [ -f "$DEST_PLIST" ]; then
      echo "plist: $DEST_PLIST ✓"
    else
      echo "plist: 未安装 ✗"
    fi
    echo
    launchctl list | grep -E "^.+\s+.+\s+$LABEL" || echo "launchctl 里没找到 $LABEL"
    ;;
  logs)
    tail -F "$PROJECT_DIR/data/launchd.out.log" "$PROJECT_DIR/data/launchd.err.log"
    ;;
  *)
    echo "用法: $0 {install|uninstall|reload|status|logs}"
    exit 1
    ;;
esac
