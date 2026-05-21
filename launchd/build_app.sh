#!/bin/bash
# 把项目包成 FeishuWorklog.app —— 给 launchd 一个稳定、可在"完全磁盘访问"里
# 直接选中的入口。详见 launcher.c 顶部注释。
#
# 幂等：.app 已是最新（不比 launcher.c / app-Info.plist 旧）就跳过重编译，
# 避免 cdhash 变动导致已授的 FDA 失效。
#
#   ./launchd/build_app.sh          # 按需构建
#   ./launchd/build_app.sh --force  # 强制重建

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$HERE/.." && pwd)"
APP="$PROJECT_DIR/FeishuWorklog.app"
EXE="$APP/Contents/MacOS/FeishuWorklog"
SRC="$HERE/launcher.c"
PLIST="$HERE/app-Info.plist"

force=0
[ "${1:-}" = "--force" ] && force=1

# 幂等检查：.app 比两个源文件都新 → 跳过
if [ "$force" -eq 0 ] && [ -f "$EXE" ] \
   && [ "$EXE" -nt "$SRC" ] && [ "$EXE" -nt "$PLIST" ]; then
  echo "FeishuWorklog.app 已是最新，跳过构建（--force 可强制重建）"
  exit 0
fi

echo "构建 FeishuWorklog.app …"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

# launcher.c 是 generic 的（运行时从自身位置推项目路径），无需任何编译期宏
cc -O2 -Wall -o "$EXE" "$SRC"
cp "$PLIST" "$APP/Contents/Info.plist"

# ad-hoc 签名：让 TCC 用 cdhash 认这个 bundle，授权更稳
codesign --force --sign - "$APP"

echo "✓ 已生成 $APP"
echo "  下一步：系统设置 → 隐私与安全性 → 完全磁盘访问权限，把这个 .app 加进去"
