"""好友模块：使用 auth code 登录网关并拉取好友与菜地情况。"""

from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType
from typing import Any


WS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/132.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13)"
)

SERVER_URL = "wss://gate-obt.nqf.qq.com/prod/ws"
CLIENT_VERSION = "1.6.0.14_20251224"


class ProtoBundle:
    def __init__(self, temp_dir: Path, modules: dict[str, ModuleType]) -> None:
        self.temp_dir = temp_dir
        self.modules = modules

    @property
    def gate(self) -> ModuleType:
        return self.modules["game_pb2"]

    @property
    def user(self) -> ModuleType:
        return self.modules["userpb_pb2"]

    @property
    def friend(self) -> ModuleType:
        return self.modules["friendpb_pb2"]

    @property
    def visit(self) -> ModuleType:
        return self.modules["visitpb_pb2"]


def compile_proto_modules(proto_dir: Path) -> ProtoBundle:
    try:
        protoc_module = importlib.import_module("grpc_tools.protoc")
        protoc = getattr(protoc_module, "main")
    except Exception as exc:
        raise RuntimeError("缺少 grpcio-tools，请先安装: pip install grpcio-tools protobuf") from exc

    temp_dir = Path(tempfile.mkdtemp(prefix="qqfarm_proto_"))
    proto_files = sorted(p for p in proto_dir.glob("*.proto") if p.is_file())
    if not proto_files:
        raise FileNotFoundError(f"未找到 proto 目录: {proto_dir}")

    args = ["grpc_tools.protoc", f"-I{proto_dir}", f"--python_out={temp_dir}"] + [str(p) for p in proto_files]
    rc = protoc(args)
    if rc != 0:
        raise RuntimeError(f"proto 编译失败，退出码: {rc}")

    sys.path.insert(0, str(temp_dir))
    modules: dict[str, ModuleType] = {}
    for name in ["game_pb2", "userpb_pb2", "friendpb_pb2", "visitpb_pb2", "plantpb_pb2"]:
        modules[name] = importlib.import_module(name)
    return ProtoBundle(temp_dir=temp_dir, modules=modules)


class QQFarmWsClient:
    def __init__(self, auth_code: str, proto: ProtoBundle, timeout: float = 12.0) -> None:
        self.auth_code = auth_code
        self.proto = proto
        self.timeout = timeout
        self.client_seq = 1
        self.server_seq = 0
        self.ws: Any = None
        self.server_time_delta = 0.0

    def connect(self) -> None:
        try:
            websocket = importlib.import_module("websocket")
        except Exception as exc:
            raise RuntimeError("缺少 websocket-client，请先安装: pip install websocket-client") from exc

        url = (
            f"{SERVER_URL}?platform=qq&os=iOS&ver={CLIENT_VERSION}"
            f"&code={self.auth_code}&openid=&openID="
        )

        self.ws = websocket.create_connection(
            url,
            timeout=self.timeout,
            header=[
                f"User-Agent: {WS_UA}",
                "Origin: https://gate-obt.nqf.qq.com",
            ],
        )

    def close(self) -> None:
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None

    def _encode_gate_request(self, service_name: str, method_name: str, body: bytes) -> tuple[int, bytes]:
        msg = self.proto.gate.Message()
        msg.meta.service_name = service_name
        msg.meta.method_name = method_name
        msg.meta.message_type = 1
        msg.meta.client_seq = self.client_seq
        msg.meta.server_seq = self.server_seq
        msg.body = body

        seq = self.client_seq
        self.client_seq += 1
        return seq, msg.SerializeToString()

    def _recv_gate_message(self) -> Any:
        raw = self.ws.recv()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")

        msg = self.proto.gate.Message()
        msg.ParseFromString(raw)

        if msg.meta.server_seq > self.server_seq:
            self.server_seq = int(msg.meta.server_seq)
        return msg

    def send_request(self, service_name: str, method_name: str, body: bytes, timeout: float | None = None) -> bytes:
        if self.ws is None:
            raise RuntimeError("WebSocket 未连接")

        seq, payload = self._encode_gate_request(service_name, method_name, body)
        self.ws.send_binary(payload)

        deadline = time.time() + (timeout if timeout is not None else self.timeout)
        while time.time() < deadline:
            msg = self._recv_gate_message()
            meta = msg.meta

            if int(meta.message_type) != 2:
                continue
            if int(meta.client_seq) != seq:
                continue
            if int(meta.error_code) != 0:
                raise RuntimeError(
                    f"{meta.service_name}.{meta.method_name} 错误: "
                    f"code={meta.error_code} msg={meta.error_message}"
                )
            return bytes(msg.body)

        raise TimeoutError(f"请求超时: {service_name}.{method_name}")

    def login(self) -> dict[str, Any]:
        req = self.proto.user.LoginRequest()
        req.sharer_id = 0
        req.sharer_open_id = ""
        req.device_info.client_version = CLIENT_VERSION
        req.device_info.sys_software = "iOS 26.2.1"
        req.device_info.network = "wifi"
        req.device_info.memory = 7672
        req.device_info.device_id = "iPhone X<iPhone18,3>"
        req.share_cfg_id = 0
        req.scene_id = "1256"
        req.report_data.callback = ""
        req.report_data.cd_extend_info = ""
        req.report_data.click_id = ""
        req.report_data.clue_token = ""
        req.report_data.minigame_channel = "other"
        req.report_data.minigame_platid = 2
        req.report_data.req_id = ""
        req.report_data.trackid = ""

        body = self.send_request("gamepb.userpb.UserService", "Login", req.SerializeToString())
        reply = self.proto.user.LoginReply()
        reply.ParseFromString(body)

        if int(reply.time_now_millis or 0) > 0:
            self.server_time_delta = (int(reply.time_now_millis) / 1000.0) - time.time()

        return {
            "gid": int(reply.basic.gid) if reply.basic else 0,
            "name": reply.basic.name if reply.basic else "",
            "level": int(reply.basic.level) if reply.basic else 0,
            "gold": int(reply.basic.gold) if reply.basic else 0,
            "exp": int(reply.basic.exp) if reply.basic else 0,
            "server_time_millis": int(reply.time_now_millis or 0),
        }

    def get_all_friends(self) -> list[Any]:
        req = self.proto.friend.GetAllRequest()
        body = self.send_request("gamepb.friendpb.FriendService", "GetAll", req.SerializeToString())
        reply = self.proto.friend.GetAllReply()
        reply.ParseFromString(body)
        return list(reply.game_friends)

    def enter_friend_farm(self, host_gid: int) -> Any:
        req = self.proto.visit.EnterRequest()
        req.host_gid = int(host_gid)
        req.reason = 2
        body = self.send_request("gamepb.visitpb.VisitService", "Enter", req.SerializeToString())
        reply = self.proto.visit.EnterReply()
        reply.ParseFromString(body)
        return reply

    def leave_friend_farm(self, host_gid: int) -> None:
        req = self.proto.visit.LeaveRequest()
        req.host_gid = int(host_gid)
        try:
            self.send_request("gamepb.visitpb.VisitService", "Leave", req.SerializeToString(), timeout=6)
        except Exception:
            pass

    def now_sec(self) -> int:
        return int(time.time() + self.server_time_delta)


def _phase_name(phase: int) -> str:
    names = {
        0: "UNKNOWN",
        1: "SEED",
        2: "GERMINATION",
        3: "SMALL_LEAVES",
        4: "LARGE_LEAVES",
        5: "BLOOMING",
        6: "MATURE",
        7: "DEAD",
    }
    return names.get(int(phase), f"PHASE_{phase}")


def _current_phase(plant: Any, now_sec: int) -> Any | None:
    phases = list(getattr(plant, "phases", []))
    if not phases:
        return None

    for p in reversed(phases):
        begin = int(getattr(p, "begin_time", 0))
        if begin > 0 and begin <= now_sec:
            return p
    return phases[0]


def analyze_friend_lands(lands: list[Any], now_sec: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "total_lands": len(lands),
        "unlocked_lands": 0,
        "empty_lands": [],
        "growing_lands": [],
        "mature_lands": [],
        "dead_lands": [],
        "need_water": [],
        "need_weed": [],
        "need_bug": [],
        "stealable_lands": [],
    }

    for land in lands:
        land_id = int(getattr(land, "id", 0))
        if not bool(getattr(land, "unlocked", False)):
            continue
        result["unlocked_lands"] += 1

        plant = getattr(land, "plant", None)
        phases = list(getattr(plant, "phases", [])) if plant else []
        if (plant is None) or (not phases):
            result["empty_lands"].append(land_id)
            continue

        cur = _current_phase(plant, now_sec)
        if cur is None:
            result["empty_lands"].append(land_id)
            continue

        phase = int(getattr(cur, "phase", 0))
        item = {
            "land_id": land_id,
            "plant_id": int(getattr(plant, "id", 0)),
            "plant_name": str(getattr(plant, "name", "") or "未知作物"),
            "phase": phase,
            "phase_name": _phase_name(phase),
            "stealable": bool(getattr(plant, "stealable", False)),
            "left_fruit_num": int(getattr(plant, "left_fruit_num", 0)),
        }

        if phase == 6:
            result["mature_lands"].append(item)
        elif phase == 7:
            result["dead_lands"].append(item)
        else:
            result["growing_lands"].append(item)

        if int(getattr(plant, "dry_num", 0)) > 0:
            result["need_water"].append(land_id)
        if len(list(getattr(plant, "weed_owners", []))) > 0:
            result["need_weed"].append(land_id)
        if len(list(getattr(plant, "insect_owners", []))) > 0:
            result["need_bug"].append(land_id)
        if bool(getattr(plant, "stealable", False)):
            result["stealable_lands"].append(land_id)

    return result


def collect_friends_farm_status(auth_code: str, proto_dir: Path, limit: int = 0) -> dict[str, Any]:
    proto = compile_proto_modules(proto_dir)
    client = QQFarmWsClient(auth_code=auth_code, proto=proto)

    try:
        client.connect()
        me = client.login()
        friends = client.get_all_friends()
        if limit > 0:
            friends = friends[:limit]

        results: list[dict[str, Any]] = []
        for friend in friends:
            gid = int(getattr(friend, "gid", 0))
            item: dict[str, Any] = {
                "gid": gid,
                "name": str(getattr(friend, "name", "") or ""),
                "remark": str(getattr(friend, "remark", "") or ""),
                "level": int(getattr(friend, "level", 0)),
                "gold": int(getattr(friend, "gold", 0)),
                "avatar_url": str(getattr(friend, "avatar_url", "") or ""),
                "overview": {
                    "dry_num": int(getattr(getattr(friend, "plant", None), "dry_num", 0)),
                    "weed_num": int(getattr(getattr(friend, "plant", None), "weed_num", 0)),
                    "insect_num": int(getattr(getattr(friend, "plant", None), "insect_num", 0)),
                    "steal_plant_num": int(getattr(getattr(friend, "plant", None), "steal_plant_num", 0)),
                },
                "farm": {},
            }

            try:
                enter_reply = client.enter_friend_farm(gid)
                lands = list(getattr(enter_reply, "lands", []))
                item["farm"] = analyze_friend_lands(lands, now_sec=client.now_sec())
            except Exception as exc:
                item["farm_error"] = str(exc)
            finally:
                client.leave_friend_farm(gid)

            results.append(item)

        return {
            "me": me,
            "friend_count": len(results),
            "friends": results,
        }
    finally:
        client.close()
        if proto.temp_dir.exists():
            shutil.rmtree(proto.temp_dir, ignore_errors=True)


def collect_friends_mature_status(auth_code: str, proto_dir: Path, limit: int = 0) -> dict[str, Any]:
    """仅返回成熟作物巡查结果。

    - 始终返回是否存在成熟作物
    - 仅当好友存在成熟作物时，返回该好友的详细成熟地块信息
    """
    snapshot = collect_friends_farm_status(auth_code=auth_code, proto_dir=proto_dir, limit=limit)
    mature_friends: list[dict[str, Any]] = []

    for friend in list(snapshot.get("friends") or []):
        farm = friend.get("farm") if isinstance(friend, dict) else None
        if not isinstance(farm, dict):
            continue

        mature_lands = list(farm.get("mature_lands") or [])
        if not mature_lands:
            continue

        mature_friends.append(
            {
                "gid": int(friend.get("gid") or 0),
                "name": str(friend.get("name") or ""),
                "remark": str(friend.get("remark") or ""),
                "level": int(friend.get("level") or 0),
                "mature_land_count": len(mature_lands),
                "mature_lands": mature_lands,
                "stealable_lands": list(farm.get("stealable_lands") or []),
            }
        )

    return {
        "me": snapshot.get("me") or {},
        "checked_friend_count": int(snapshot.get("friend_count") or 0),
        "has_mature": len(mature_friends) > 0,
        "mature_friend_count": len(mature_friends),
        "mature_friends": mature_friends,
        "checked_at": int(time.time()),
    }
