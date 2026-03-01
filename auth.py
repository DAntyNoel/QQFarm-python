"""认证模块：扫码获取 QQ 农场 auth code。"""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass
from pathlib import Path

import requests


CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_APPID = "1112386029"


@dataclass
class LoginCodeResult:
    code: str
    url: str


@dataclass
class ScanStatusResult:
    status: str  # Wait | OK | Used | Error
    ticket: str = ""
    uin: str = ""
    nickname: str = ""
    msg: str = ""


@dataclass
class LoginSuccessResult:
    auth_code: str
    uin: str
    nickname: str


class MiniProgramLoginSession:
    QUA = "V1_HT5_QDT_0.70.2209190_x64_0_DEV_D"

    def __init__(self, timeout: float = 10.0) -> None:
        self.http = requests.Session()
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

    def request_login_code(self) -> LoginCodeResult:
        resp = self.http.get(
            "https://q.qq.com/ide/devtoolAuth/GetLoginCode",
            headers=self.get_headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        payload = resp.json()

        if int(payload.get("code", -1)) != 0:
            raise RuntimeError(f"获取登录码失败: {payload}")

        login_code = str((payload.get("data") or {}).get("code") or "").strip()
        if not login_code:
            raise RuntimeError(f"响应缺少登录码: {payload}")

        login_url = f"https://h5.qzone.qq.com/qqq/code/{login_code}?_proxy=1&from=ide"
        return LoginCodeResult(code=login_code, url=login_url)

    def query_status(self, code: str) -> ScanStatusResult:
        resp = self.http.get(
            f"https://q.qq.com/ide/devtoolAuth/syncScanSateGetTicket?code={code}",
            headers=self.get_headers(),
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            return ScanStatusResult(status="Error", msg=f"HTTP {resp.status_code}")

        payload = resp.json()
        res_code = int(payload.get("code", -99999))
        data = payload.get("data") or {}

        if res_code == 0:
            if int(data.get("ok", 0)) != 1:
                return ScanStatusResult(status="Wait")
            return ScanStatusResult(
                status="OK",
                ticket=str(data.get("ticket") or ""),
                uin=str(data.get("uin") or ""),
                nickname=str(data.get("nick") or ""),
            )

        if res_code == -10003:
            return ScanStatusResult(status="Used")

        return ScanStatusResult(status="Error", msg=f"Code: {res_code}")

    def get_auth_code(self, ticket: str, appid: str = DEFAULT_APPID) -> str:
        resp = self.http.post(
            "https://q.qq.com/ide/login",
            json={"appid": appid, "ticket": ticket},
            headers=self.get_headers(),
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            return ""
        payload = resp.json() or {}
        return str(payload.get("code") or "")

    def login_by_qr(
        self,
        *,
        appid: str = DEFAULT_APPID,
        poll_interval: float = 1.0,
        max_wait_seconds: int = 180,
        save_qr_image: bool = True,
    ) -> LoginSuccessResult:
        qr = self.request_login_code()
        print("请使用手机 QQ 扫码登录：")
        print(qr.url)
        if save_qr_image:
            self._try_save_qr_image(qr.url)

        deadline = time.time() + max_wait_seconds
        while time.time() < deadline:
            status = self.query_status(qr.code)
            if status.status == "Wait":
                time.sleep(poll_interval)
                continue
            if status.status == "Used":
                raise RuntimeError("二维码已失效，请重新获取")
            if status.status == "Error":
                raise RuntimeError(f"扫码状态异常: {status.msg}")

            auth_code = self.get_auth_code(status.ticket, appid=appid)
            if not auth_code:
                raise RuntimeError("已扫码成功，但换取 auth code 失败")

            return LoginSuccessResult(
                auth_code=auth_code,
                uin=status.uin,
                nickname=status.nickname,
            )

        raise TimeoutError("扫码登录超时")

    @staticmethod
    def _try_save_qr_image(url: str) -> None:
        try:
            qrcode = importlib.import_module("qrcode")
        except Exception:
            return

        img = qrcode.make(url)
        output = Path(__file__).resolve().parent / "qq_login_qr.png"
        img.save(output)
        print(f"已生成二维码图片: {output}")
