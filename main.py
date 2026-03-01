"""事件主循环：维护 auth code，并处理 login/friend 事件。"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from auth import DEFAULT_APPID, MiniProgramLoginSession
from friend import collect_friends_farm_status


class QQFarmEventLoop:
    def __init__(
        self,
        *,
        auth_code: str,
        scan_enabled: bool,
        appid: str,
        scan_timeout: int,
        scan_interval: float,
        proto_dir: Path,
        friend_limit: int,
        output_file: Path | None,
        auth_cache_file: Path,
        loop_interval: int,
        once: bool,
    ) -> None:
        self.auth_code = auth_code.strip()
        self.scan_enabled = scan_enabled
        self.appid = appid
        self.scan_timeout = scan_timeout
        self.scan_interval = scan_interval
        self.proto_dir = proto_dir
        self.friend_limit = max(0, friend_limit)
        self.output_file = output_file
        self.auth_cache_file = auth_cache_file
        self.loop_interval = max(1, loop_interval)
        self.once = once

        if not self.auth_code:
            self.auth_code = self._load_auth_code_cache()

    def _load_auth_code_cache(self) -> str:
        if not self.auth_cache_file.exists():
            return ""
        try:
            data = json.loads(self.auth_cache_file.read_text(encoding="utf-8"))
            code = str((data or {}).get("auth_code") or "").strip()
            if code:
                print(f"[login] 已从缓存加载 auth code: {self.auth_cache_file}")
            return code
        except Exception:
            return ""

    def _save_auth_code_cache(self, auth_code: str) -> None:
        self.auth_cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "auth_code": auth_code,
            "appid": self.appid,
            "updated_at": int(time.time()),
        }
        self.auth_cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[login] 已缓存 auth code: {self.auth_cache_file}")

    def _clear_auth_code_cache(self) -> None:
        try:
            if self.auth_cache_file.exists():
                self.auth_cache_file.unlink()
        except Exception:
            pass

    def on_login(self, auth_code: str) -> None:
        print(f"[login] 已获得 auth code: {auth_code[:10]}...")

    def on_friend_snapshot(self, payload: dict[str, Any]) -> None:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        print(text)
        if self.output_file is not None:
            self.output_file.write_text(text, encoding="utf-8")
            print(f"[friend] 已写入结果: {self.output_file}")

    def on_error(self, err: Exception) -> None:
        print(f"[error] {err}")

    def ensure_auth_code(self) -> None:
        if self.auth_code:
            return
        if not self.scan_enabled:
            raise RuntimeError("当前无 auth code，且未启用 --scan")

        session = MiniProgramLoginSession()
        result = session.login_by_qr(
            appid=self.appid,
            poll_interval=self.scan_interval,
            max_wait_seconds=self.scan_timeout,
        )
        self.auth_code = result.auth_code
        self._save_auth_code_cache(self.auth_code)
        self.on_login(self.auth_code)

    def run(self) -> int:
        while True:
            try:
                self.ensure_auth_code()

                payload = collect_friends_farm_status(
                    auth_code=self.auth_code,
                    proto_dir=self.proto_dir,
                    limit=self.friend_limit,
                )
                self.on_friend_snapshot(payload)

                if self.once:
                    return 0

                time.sleep(self.loop_interval)
            except KeyboardInterrupt:
                print("\n[system] 已停止")
                return 0
            except Exception as exc:
                self.on_error(exc)
                # auth code 可能失效，清空后让下一轮自动重登
                self.auth_code = ""
                self._clear_auth_code_cache()
                if not self.scan_enabled:
                    return 1
                time.sleep(3)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QQ 农场事件主循环")
    parser.add_argument("--auth-code", default="", help="已有 auth code")
    parser.add_argument("--scan", action="store_true", help="无 auth code 时启用扫码登录")
    parser.add_argument("--appid", default=DEFAULT_APPID, help="扫码换取 auth code 的 appid")
    parser.add_argument("--scan-timeout", type=int, default=180, help="扫码最大等待秒数")
    parser.add_argument("--scan-interval", type=float, default=1.0, help="扫码轮询间隔秒")
    parser.add_argument("--loop-interval", type=int, default=60, help="主循环间隔秒")
    parser.add_argument("--once", action="store_true", help="只执行一轮")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 个好友（0 表示全部）")
    parser.add_argument(
        "--proto-dir",
        default=str(Path(__file__).resolve().parent / "proto"),
        help="proto 文件目录",
    )
    parser.add_argument("--output", default="", help="输出 JSON 文件路径")
    parser.add_argument(
        "--auth-cache-file",
        default=str(Path(__file__).resolve().parent / ".private" / "auth_code.json"),
        help="auth code 缓存文件路径",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    loop = QQFarmEventLoop(
        auth_code=str(args.auth_code or ""),
        scan_enabled=bool(args.scan),
        appid=str(args.appid),
        scan_timeout=int(args.scan_timeout),
        scan_interval=float(args.scan_interval),
        proto_dir=Path(args.proto_dir).resolve(),
        friend_limit=int(args.limit),
        output_file=Path(args.output).resolve() if args.output else None,
        auth_cache_file=Path(args.auth_cache_file).resolve(),
        loop_interval=int(args.loop_interval),
        once=bool(args.once),
    )
    return loop.run()


if __name__ == "__main__":
    raise SystemExit(main())
