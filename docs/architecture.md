# 架构与接口边界

## 分层

```text
应用：目标状态、用户授权、定时调度、响应关联、超时、数据新鲜度
                    │
      Python 命令构造 / Frame / 增量 Decoder
                    │
        serial 或透明 TCP 字节传输适配器
                    │
           RS485 适配硬件 / 主机链路

Lua 参考实现：独立提供相同帧格式和控制载荷算法，不依赖上述传输层
```

协议核心不打开串口、不建立网络连接、不读取时钟、不调度轮询，也不保存家庭设备配置。
它不包含特定屏幕的 SDK、升级包、UI 资源、调试信封或云服务。
本项目目前**没有 ESPHome 组件或 C++ 实现**。

## Python 核心

公共导出位于 `shiteng_rs485`：

| API | 输入 / 返回 | 责任 |
| --- | --- | --- |
| `Frame(kind, payload, raw)` | 类型整数、不可变字节载荷和原始帧 | 已解码帧记录；不意味着类型语义已知 |
| `crc16(data)` | 字节 → CRC 整数 | CRC 算术，不自动决定线上字节序 |
| `encode_frame(kind, payload)` | 类型、载荷 → 完整字节帧 | 添加长度、CRC 和结束字节 |
| `Decoder()` | `feed(bytes)` → `list[Frame]` | 增量分帧；`buffered_bytes` 为当前缓存长度，`reset()` 丢弃缓存 |
| `make_control(*, power, auto, fan, mode)` | 明确的四个状态 → 四字节载荷 | 构造整体目标，不发送 |
| `encode_control(*, power, auto, fan, mode)` | 同上 → 完整 `0x81` 帧 | 构造并封帧，不发送 |
| `patch_control(payload, *, power=None, auto=None, fan=None, mode=None)` | 四字节基态、要改变的字段 → 新四字节载荷 | 保留未修改字段和不透明位；原载荷不变 |
| `encode_humidity(value)` | 30..90 严格整数 → `0xC4` 帧 | 范围是库策略 |
| `heartbeat()` | 无参数 → `0x20` 帧 | 只生成字节，不周期发送 |
| `decode_experimental(frame)` | 完整帧 → 候选解释字典或 `None` | 显式启用未确认的遥测映射 |

`Fan.LOW/MID/HIGH` 的值是 1/2/3。
`Mode.FRESH/INNER_VENT/INNER_DEHUMIDIFY/FRESH_DEHUMIDIFY` 的值是 0/1/2/3。
电源和自动/手动必须是明确的 `bool`，不能用真值转换暗中接受字符串或整数。

实验性结果包含 `name`、`value`、`unit`、`provisional=True` 和 `source_type`。
它不是经过校准、位置确认或跨机型验证的传感器。应用应保留原帧，并自行处理
独立字段的过期和不可用状态；返回 `None` 不意味着传感器值为零。

以下例子只在内存中修改已知载荷，不连接任何设备：

```python
from shiteng_rs485 import Decoder, Fan, encode_frame, patch_control

baseline = bytes.fromhex("81 40 14 00")
payload = patch_control(baseline, fan=Fan.LOW)
wire = encode_frame(0x81, payload)
assert wire.hex(" ") == "7e 81 00 0a 81 20 14 00 5c e9 0d 0a"

decoder = Decoder()
assert decoder.feed(wire[:5]) == []
frame = decoder.feed(wire[5:])[0]
assert frame.kind == 0x81
assert frame.payload == payload
```

创建 `Frame` 记录不应被调用者当作字节校验的替代品；来自不可信流的原始字节先交给
`Decoder`。跨重连、方向或已知丢字节边界，不拼接旧的半帧。

## 传输适配器

`shiteng_rs485.transports` 提供同步字节适配器：

- `SerialTransport(port, *, baudrate=9600, timeout=1)`：可选 `pyserial` 依赖。
- `TCPTransport(host, port, *, timeout=1)`：使用 Python 标准库。

二者显式 `open()` / `close()`，并提供 `read(max_bytes)` / `write(bytes)`。
上下文管理器进入时打开连接、退出时关闭。构造和连接不发送协议控制命令，
也不隐含心跳、重新下发目标、离线队列或自动重放。调用 `write()` 才是明确的发送动作。

TCP 适配器要求对端是**透明的 RS485 字节流**。带有产品专属报头、RPC、MQTT、
调试封装或其他私有网关命令的端口不是透明 TCP，必须由上层另行适配；
不能因为它使用 TCP 就把本协议帧直接写入。

部分写入后自动重试可能重复操作，适配器不把这种情况伪装为安全成功。
传输成功仅说明本地字节写入结果，不说明主机响应或机械执行。
串口驱动打开端口时的线路行为受硬件和操作系统影响；“不自动发送协议命令”
不等于对所有适配器作电气行为保证。

## Lua 参考实现

[`lua/shiteng.lua`](../lua/shiteng.lua) 是 Lua 5.1 兼容模块，使用标准 Lua 运算，
无串口、网络、OS、屏幕 SDK 或原生位运算库依赖。每个字节数组必须是从 1 开始、
连续的整数表，元素范围 0..255。

```lua
local Shiteng = dofile("lua/shiteng.lua")
local baseline = {0x81, 0x40, 0x14, 0x00}
local payload = Shiteng.patch_control(baseline, {fan = 1})
local wire = Shiteng.encode(0x81, payload)

local decoder = Shiteng.decoder_new()
local frames = decoder:feed(wire)
assert(frames[1].type == 0x81)
assert(frames[1].payload[2] == 0x20)
```

Lua 方法为 `encode`、`decoder_new`、`patch_control`、`make_control`、
`encode_humidity`、`heartbeat`。解码结果字段是 `type/payload/raw`；
解码器使用 `:feed()`、`:reset()`、`:buffered_bytes()`。
Lua `make_control` 接受 `{power=bool, auto=bool, fan=1..3, mode=0..3}`，返回载荷。
与 Python 不同，Lua 参考实现没有遥测解释和传输适配器；它不是完整的固件或刷机包。

## 应用仍需负责什么

应用应明确谁是总线上的主动发送者，不同时运行两个未协调的控制器。
它还负责取得可信的当前目标、维护不透明位、选择是否发送、记录方向、关联响应、
检测丢字节和断连，以及展示未确认状态。状态持久化、自动恢复运行、重发策略、
Home Assistant 实体、MQTT 发现和图形界面均不属于本库。

自动控制、除湿策略和风速之间可能有设备自己的联动。
帧编码能表达某个组合，不代表这个组合在每种状态下有效。
参见 [协议文档](protocol.md) 的证据边界，避免把字节正确性升级为整机行为保证。
