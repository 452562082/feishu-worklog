#!/bin/bash
# 安装 / 卸载 LaunchAgent，让 feishu-worklog 每天自动跑（含开盖唤醒补跑）
#
# 用法：
#   ./scripts/install_launchd.sh install     # 装好并立即加载
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

case "$cmd" in
  install)
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
    echo "下次触发：每天 8:00（错过会在系统唤醒后补跑）+ 每次重新登录"
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
