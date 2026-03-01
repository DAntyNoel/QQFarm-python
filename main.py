"""事件主循环：维护 auth code，并处理 login/friend 事件。"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import aiohttp

from auth import CHROME_UA, DEFAULT_APPID, LoginSuccessResult
from friend import collect_friends_mature_status


class AsyncMiniProgramLoginSession:
    QUA = "V1_HT5_QDT_0.70.2209190_x64_0_DEV_D"

    def __init__(self, http: aiohttp.ClientSession, timeout: float = 10.0) -> None:
        self.http = http
        self.timeout = timeout

    @classmethod
    def get_headers(cls) -> dict[str, str]:
        return {
            "qua": cls.QUA,
            "host": "q.qq.com",
            "accept": "application/json",
            "content-type": "application/json",
            "user-agent": CHROME_UA,
        }

    async def request_login_code(self) -> str:
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with self.http.get(
            "https://q.qq.com/ide/devtoolAuth/GetLoginCode",
            headers=self.get_headers(),
            timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json()

        if int(payload.get("code", -1)) != 0:
            raise RuntimeError(f"获取登录码失败: {payload}")

        login_code = str((payload.get("data") or {}).get("code") or "").strip()
        if not login_code:
            raise RuntimeError(f"响应缺少登录码: {payload}")
        return login_code

    async def query_status(self, code: str) -> dict[str, str]:
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with self.http.get(
            f"https://q.qq.com/ide/devtoolAuth/syncScanSateGetTicket?code={code}",
            headers=self.get_headers(),
            timeout=timeout,
        ) as resp:
            if resp.status != 200:
                return {"status": "Error", "msg": f"HTTP {resp.status}"}
            payload = await resp.json()

        res_code = int(payload.get("code", -99999))
        data = payload.get("data") or {}

        if res_code == 0:
            if int(data.get("ok", 0)) != 1:
                return {"status": "Wait"}
            return {
                "status": "OK",
                "ticket": str(data.get("ticket") or ""),
                "uin": str(data.get("uin") or ""),
                "nickname": str(data.get("nick") or ""),
            }

        if res_code == -10003:
            return {"status": "Used"}

        return {"status": "Error", "msg": f"Code: {res_code}"}

    async def get_auth_code(self, ticket: str, appid: str = DEFAULT_APPID) -> str:
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with self.http.post(
            "https://q.qq.com/ide/login",
            json={"appid": appid, "ticket": ticket},
            headers=self.get_headers(),
            timeout=timeout,
        ) as resp:
            if resp.status != 200:
                return ""
            payload = await resp.json()

        return str((payload or {}).get("code") or "")

    async def login_by_qr(
        self,
        *,
        appid: str = DEFAULT_APPID,
        poll_interval: float = 1.0,
        max_wait_seconds: int = 180,
    ) -> LoginSuccessResult:
        login_code = await self.request_login_code()
        login_url = f"https://h5.qzone.qq.com/qqq/code/{login_code}?_proxy=1&from=ide"
        print("请使用手机 QQ 扫码登录：")
        print(login_url)

        deadline = time.time() + max_wait_seconds
        while time.time() < deadline:
            status = await self.query_status(login_code)
            if status.get("status") == "Wait":
                await asyncio.sleep(poll_interval)
                continue
            if status.get("status") == "Used":
                raise RuntimeError("二维码已失效，请重新获取")
            if status.get("status") == "Error":
                raise RuntimeError(f"扫码状态异常: {status.get('msg', '')}")

            ticket = str(status.get("ticket") or "")
            auth_code = await self.get_auth_code(ticket, appid=appid)
            if not auth_code:
                raise RuntimeError("已扫码成功，但换取 auth code 失败")

            return LoginSuccessResult(
                auth_code=auth_code,
                uin=str(status.get("uin") or ""),
                nickname=str(status.get("nickname") or ""),
            )

        raise TimeoutError("扫码登录超时")


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

    @staticmethod
    def _is_auth_error(err: Exception) -> bool:
        text = str(err or "").lower()
        keys = [
            "code=400",
            "error: 400",
            "400 bad request",
            "handshake status 400",
            "invalid code",
            "auth code",
            "登录失败",
            "连接被拒绝",
        ]
        return any(k in text for k in keys)

    async def ensure_auth_code(self, http: aiohttp.ClientSession) -> None:
        if self.auth_code:
            return

        # 尝试从缓存加载 auth code
        cached_code = self._load_auth_code_cache()
        if cached_code:
            self.auth_code = cached_code
            return

        # 缓存中也没有，检查是否启用扫码
        if not self.scan_enabled:
            raise RuntimeError("当前无 auth code，且未启用 --scan")

        session = AsyncMiniProgramLoginSession(http=http)
        result = await session.login_by_qr(
            appid=self.appid,
            poll_interval=self.scan_interval,
            max_wait_seconds=self.scan_timeout,
        )
        self.auth_code = result.auth_code
        self._save_auth_code_cache(self.auth_code)
        self.on_login(self.auth_code)

    async def run(self) -> int:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            while True:
                try:
                    await self.ensure_auth_code(http)

                    payload = await asyncio.to_thread(
                        collect_friends_mature_status,
                        self.auth_code,
                        self.proto_dir,
                        self.friend_limit,
                    )
                    self.on_friend_snapshot(payload)

                    if self.once:
                        return 0

                    await asyncio.sleep(self.loop_interval)
                except KeyboardInterrupt:
                    print("\n[system] 已停止")
                    return 0
                except Exception as exc:
                    self.on_error(exc)
                    # 仅在认证错误时清空 auth code 并触发重登；普通网络/业务错误保留 code
                    if self._is_auth_error(exc):
                        print("[login] 检测到登录态失效，准备重新获取 auth code")
                        self.auth_code = ""
                        self._clear_auth_code_cache()
                        if not self.scan_enabled:
                            return 1
                    await asyncio.sleep(3)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QQ 农场事件主循环")
    parser.add_argument("--auth-code", default="", help="已有 auth code")
    parser.add_argument("--scan", action="store_true", help="无 auth code 时启用扫码登录")
    parser.add_argument("--appid", default=DEFAULT_APPID, help="扫码换取 auth code 的 appid")
    parser.add_argument("--scan-timeout", type=int, default=180, help="扫码最大等待秒数")
    parser.add_argument("--scan-interval", type=float, default=1.0, help="扫码轮询间隔秒")
    parser.add_argument("--loop-interval", type=int, default=30, help="主循环间隔秒（默认 30）")
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


async def main() -> int:
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
    return await loop.run()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
