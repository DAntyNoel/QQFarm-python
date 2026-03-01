"""兼容入口（已清理）：

- 扫码登录能力在 `auth.py`
- 好友/菜地拉取能力在 `friend.py`
- 事件主循环入口在 `main.py`

此文件只保留兼容导出，避免旧脚本引用报错。
"""

from auth import DEFAULT_APPID, LoginCodeResult, LoginSuccessResult, MiniProgramLoginSession, ScanStatusResult
from friend import analyze_friend_lands, collect_friends_farm_status

__all__ = [
    "DEFAULT_APPID",
    "LoginCodeResult",
    "ScanStatusResult",
    "LoginSuccessResult",
    "MiniProgramLoginSession",
    "analyze_friend_lands",
    "collect_friends_farm_status",
]

