"""有头模式打开飞书，扫码登录后关闭窗口即可。登录态存在 data/browser_state/。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from feishu_worklog.main import login

if __name__ == "__main__":
    login()
