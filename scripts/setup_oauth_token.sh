#!/bin/bash
# 跑 `claude setup-token`，把吐出来的 token 自动写到 .env。
# 直接在终端跑（需要 TTY 交互，不能在 launchd / cron / pipe 里跑）：
#   ./scripts/setup_oauth_token.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

if ! command -v claude >/dev/null 2>&1; then
  echo "✗ 找不到 claude 命令，先装 Claude Code CLI" >&2
  exit 1
fi

# 没有 .env 就从模板拷一份
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
  else
    echo "CLAUDE_CODE_OAUTH_TOKEN=" > .env
  fi
  chmod 600 .env
fi

# 录制 setup-token 全部输入输出到 typescript（macOS 的 script 用法）
# script 维持 TTY 交互，所以浏览器跳转/粘贴 code 都不受影响
LOG="$(mktemp -t claude-setup-token)"
trap 'rm -f "$LOG"' EXIT

echo "═══ 即将跑 claude setup-token ═══"
echo "按它的提示：会给一个授权 URL，浏览器登录后复制 code 粘回终端"
echo

script -q "$LOG" claude setup-token

# 抓 sk-ant-oat01-... 格式的 token；多行/有 ANSI 颜色码也能命中
TOKEN="$(grep -oE 'sk-ant-oat01-[A-Za-z0-9_-]+' "$LOG" | tail -1 || true)"

if [ -z "$TOKEN" ]; then
  echo
  echo "✗ 没从 setup-token 输出里找到 sk-ant-oat01-... 的 token" >&2
  echo "   可能输出格式变了。临时日志：$LOG" >&2
  echo "   自己看一眼里面的 token 粘到 .env 后删掉日志：rm '$LOG'" >&2
  trap - EXIT  # 出错时保留日志让用户排查
  exit 1
fi

# 替换 .env 里已有行，没有的话追加
if grep -q '^CLAUDE_CODE_OAUTH_TOKEN=' .env; then
  # macOS sed 的 -i 必须跟空串参数
  sed -i '' "s|^CLAUDE_CODE_OAUTH_TOKEN=.*|CLAUDE_CODE_OAUTH_TOKEN=$TOKEN|" .env
else
  echo "CLAUDE_CODE_OAUTH_TOKEN=$TOKEN" >> .env
fi
chmod 600 .env

echo
echo "✓ token 写入 .env：$(grep '^CLAUDE_CODE_OAUTH_TOKEN=' .env | head -c 35)..."
echo
echo "下一步验证 launchd 跑能不能用："
echo "  launchctl kickstart -k gui/\$(id -u)/com.xiaolong.feishu-worklog"
echo "  sleep 5 && tail -30 data/launchd.err.log"
