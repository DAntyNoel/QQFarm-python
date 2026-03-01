# QQFarm-python

Python 版 QQ 农场脚本，当前包含：

- 扫码获取 `auth_code`
- 使用 `auth_code` 登录网关
- 获取好友列表
- 进入好友农场并返回菜地状态快照
- 事件主循环（自动维护 `auth_code`，处理 `login`/`friend` 事件）

> 项目正在完善中……

## 目录结构

- `main.py`：事件主循环入口
- `auth.py`：扫码登录与 `auth_code` 获取
- `friend.py`：好友列表、进入好友农场、菜地分析
- `proto/`：内置 protobuf 协议文件（仓库内自包含）
- `login.py`：兼容导出入口（已清理，建议新代码直接用 `main.py` / `auth.py` / `friend.py`）

## 环境要求

- Python 3.10+
- 依赖：
  - `requests`
  - `websocket-client`
  - `protobuf`
  - `grpcio-tools`
  - `qrcode[pil]`（可选，用于本地二维码图片）

## 安装依赖

```bash
pip install requests websocket-client protobuf grpcio-tools qrcode[pil]
```

## 快速开始

### 1) 单次执行（已有 auth code）

```bash
python main.py --auth-code <你的auth_code> --once
```

### 2) 自动扫码并执行一轮

```bash
python main.py --scan --once
```

### 3) 循环执行（每 60 秒拉取一次）

```bash
python main.py --scan --loop-interval 60
```

### 4) 限制处理好友数量并写入 JSON 文件

```bash
python main.py --scan --once --limit 30 --output ./friends_snapshot.json
```

## 常用参数

- `--auth-code`：直接传入已获取的 `auth_code`
- `--scan`：无 `auth_code` 时启用扫码登录
- `--scan-timeout`：扫码等待超时秒数（默认 `180`）
- `--scan-interval`：扫码状态轮询间隔秒（默认 `1.0`）
- `--loop-interval`：主循环间隔秒（默认 `60`）
- `--once`：仅执行一轮
- `--limit`：仅处理前 N 个好友（`0` 表示全部）
- `--output`：输出结果 JSON 文件路径
- `--proto-dir`：proto 文件目录（默认指向仓库内 `./proto`）

## 输出说明

输出为 JSON，包含：

- `me`：当前账号基础信息
- `friend_count`：好友数量
- `friends[]`：好友详情
  - `overview`：摘要（缺水/有草/有虫/可偷数量）
  - `farm`：菜地明细（成熟地块、可偷地块、缺水地块等）

## 免责声明

本项目仅供学习与研究用途。请遵守相关平台服务条款与当地法律法规。

## Credits

本目录实现基于上游项目协议与思路改写，感谢以下项目：

- 核心功能参考：<https://github.com/linguo2625469/qq-farm-bot>
- 部分功能参考：<https://github.com/QianChenJun/qq-farm-bot>
- 扫码登录思路参考：<https://github.com/lkeme/QRLib>
