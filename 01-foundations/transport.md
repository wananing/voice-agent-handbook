# 传输：WebRTC、WebSocket、Opus

> 状态：草稿
> 最后更新：2026-10-01

这一页讲音频怎么在设备、服务端、上游模型之间流动：用什么协议、Opus 怎么封装、网络不好时会发生什么、端侧设备给传输加了哪些约束。

---

## 1. 核心概念

### 1.1 两种协议的本质差别

| | WebSocket | WebRTC |
|---|---|---|
| 底层 | TCP（通常加 TLS） | UDP 上的 RTP / RTCP（SRTP 加密） |
| 丢包 | 不丢、不乱序 | 会丢、会乱序 |
| 网络差时的表现 | **卡顿后突发**：队头阻塞，一段时间没数据，然后一口气到 | 缺包、抖动，由抖动缓冲和 PLC 处理 |
| 抖动缓冲 | 没有，应用自己做 | libwebrtc 的 NetEQ 自适应缓冲（通识，本手册分析的仓库里没有它的代码） |
| 时钟同步 | 没有，要自己在帧里带时间戳 | RTP 时间戳 + RTCP SR（NTP ↔ RTP 对应），可算 RTT |
| 信令 | 一条连接，文本帧走控制、二进制帧走音频 | 需要信令交换 SDP，NAT 穿透要 ICE / STUN / TURN |
| 实现复杂度 | 低 | 高；服务端通常借助 SFU（LiveKit、Daily）或 aiortc 一类的库 |

一个常见误解：**"换 WebRTC 就解决弱网"不一定成立。** WebRTC 的抖动缓冲、丢包隐藏、加减速播放是 libwebrtc 实现给的，不是协议白送的。纯 Python 的 aiortc 音频接收端只有一个 16 包的重排缓冲（`capacity=16, prefetch=4`），音频没有 NACK、没有 PLC：丢一个包后，后面的包卡在空位后面，推算要等序号推进 16 包（20 ms 帧下约 320 ms）才跳过，然后突发吐出（推断，未实测）。两端都用 libwebrtc 时才能拿到那套能力。

### 1.2 各自适合的场景

**WebRTC 适合：**

- 浏览器、手机 App 直接连 agent，走公网、弱网常见。浏览器自带的 WebRTC 栈同时给了抖动缓冲、PLC、回声消除（AEC 在客户端那台机器上做，这是浏览器栈的能力，具体开关待确认）。
- 已经用 SFU / 房间模型的产品（LiveKit、Daily），agent 作为房间里的一个参与者。
- 例子：openai-realtime-agents 的浏览器 demo 用 WebRTC 连 OpenAI Realtime；Pipecat 的 SmallWebRTC transport 基于 aiortc。

**WebSocket 适合：**

- **自有设备**连自有服务端（玩具、音箱、ESP32 一类），协议自己定，端侧实现简单。小智固件默认就是 WebSocket（JSON 文本帧 + 二进制 Opus 帧）。
- **服务端到服务端**：网关连上游的 ASR、TTS、S2S 模型 API，大多数模型服务都提供 WebSocket 接口。
- 电话接入：Pipecat 的 Twilio serializer 在 WebSocket 上收发 8 kHz μ-law。

用 WebSocket 时要自己补上 WebRTC 那边由协议提供的东西：

| 要补的 | 为什么 |
|---|---|
| 序号 | TCP 内不会缺，但断线重连会丢一段，要能发现缺口、能去重 |
| 按采样数计的时间戳 | 算上行延迟和抖动；和按键事件对齐；判断重连缺了多长 |
| `turn_id` | 打断后设备能丢弃过期回合的音频，迟到的结果能归到正确的回合 |
| 播放回执 | 服务端不知道设备播到哪了，按已播放位置截断上下文要靠设备回传 |

Pipecat 的 WebSocket 输入不带序号和时间戳，反序列化后直接进管线；这部分没有现成代码可抄。

小智还提供另一条路：**MQTT 走控制，UDP 走音频**。UDP 包头 16 字节（type、flags、长度、ssrc、timestamp、sequence），同时作为 AES-128-CTR 的 nonce；接收端丢弃 `seq <= last` 的包，跳号只告警。

### 1.3 Opus 在两种协议里的用法

| | WebRTC | WebSocket（自定协议） |
|---|---|---|
| 协商 | SDP 里声明 `opus/48000/2`，fmtp 里可带 `useinbandfec`、DTX 等参数 | 握手消息里自己声明。小智设备 `hello` 带 `audio_params{opus, 16k, 1ch, 60ms}`，服务端回下行参数（默认 24k） |
| 封装 | 一个 RTP 包装一帧 Opus | 一个二进制帧装一包 Opus（小智 v1 是裸 Opus；v2 带 16 字节头含 timestamp；v3 4 字节头）；或者用 Ogg 容器（Moshi 的协议发 Ogg 封装的 Opus，24 kHz 单声道） |
| 采样率 | 线上固定按 48k 协商；aiortc 编解码写死 48k stereo | 自己选，比如上行 16k、下行 24k |
| FEC | 有意义（UDP 丢包） | TCP 上只增加码率，应关掉 |
| DTX | 省带宽 | 服务端看到时间轴空洞，要按时间戳决定补不补静音；和本地 VAD 的停顿压缩可能打架 |
| PLC | NetEQ 处理（libwebrtc） | 只在重连缺口处调用解码器 PLC，并限制在几帧以内 |

自有设备上解码可以直接让 libopus 输出 16k，不必先解到 48k 再降采样，见 [audio-basics](audio-basics.md)。

### 1.4 丢包与抖动

- **丢包**：包没到。UDP 上真实存在；TCP 上表现为延迟（重传），只有断线重连时才真正缺一段。
- **抖动**：包到达间隔不均匀。接收端要用缓冲吸收：缓冲越深越抗抖，但延迟越大。
- **TCP 的卡顿后突发**：卡顿期间不要为了"保持实时"往上游插静音，上游服务端 VAD 会把插入的静音当成用户停顿而切段。突发送到上游（比实时快）上游能否正常处理，待确认。

### 1.5 下行节奏：服务端限速

下行（agent → 用户）要决定服务端提前多少把音频推给客户端。开源实现的选择都是**按实时限速，只超前一点**：

| 实现 | 超前量 |
|---|---|
| Pipecat WebSocket | 40 ms 一块，按 1× 实时下发，客户端大约只超前一块 |
| LiveKit room_io | `AudioSource` 队列上限 200 ms |
| unmute | 超前 0.32 s |
| 小智服务端 | 前 5 包（5 × 60 ms = 300 ms）直接发作预缓冲，之后按 60 ms 一包；设备解码队列上限 1.2 s，满了直接丢 |
| Pipecat MoQ（反例） | `audio_out_max_buffer_ms=25000`，一次可以推很多，所以必须有客户端 flush 原语 |

限速的好处是打断时服务端清自己的队列，设备只需要清一小段缓冲；服务端也能大致估计播放位置（LiveKit 用"已推入 − 仍在队列"）。代价是缓冲浅，TCP 卡顿会直接变成播放断续。WebSocket + 设备缓冲下，服务端估计的播放位置不准，要靠设备回执。

### 1.6 端侧（ESP32 一类设备）的约束

以小智固件为参照：

- **CPU**：Opus 编码用 complexity 0、60 ms 帧，省 CPU 也省包头；代价是组帧延迟和判停粒度都是 60 ms 级。
- **内存**：唤醒词、AEC 这类 AFE 功能要 S3 / P4 加 PSRAM 才默认开启；C3 / C5 / C6 等小芯片走精简引擎，**上行原始 PCM，没有 AEC**。播放缓冲、预录缓冲都受 PSRAM 限制（小智唤醒前 2 s 预录用 PSRAM 里 64 KB 的环形缓冲）。
- **AEC 只能在设备上做**：服务端拿不到扬声器参考信号。小智的服务端"AEC"只是谱减，固件里标为不稳定。没有 AEC 的设备，播放时开麦就会把自己的声音录进去。
- **协议要简单**：WebSocket 或 MQTT + UDP 都是端侧能实现的；WebRTC 在 ESP32 上的可行性和资源占用待确认。
- **连接管理**：小智设备 `hello` 后 10 s 收不到服务端 `hello` 判失败，通道 120 s 无下行判超时；网络切换、重连要按会话恢复处理。

---

## 2. 对 agent 设计的影响

| 决策 | 跟传输的哪个特性有关 | 去哪看 |
|---|---|---|
| 设备协议怎么设计 | 帧里带 `turn_id`、序号、时间戳；回合要有终态 | [回合模型](../02-architectures/turn-model.md) |
| 打断时冲刷哪些音频 | 服务端队列 + 设备缓冲两段都要清 | [话筒归属](../02-architectures/floor-control.md)、[打断与截断](../03-mechanisms/interruption.md) |
| 上下文按已播放位置截断 | WebRTC 下服务端估计基本可用；WebSocket 下要设备回执 | [打断与截断](../03-mechanisms/interruption.md) |
| 断线重连 | 序号发现缺口；会话跨连接恢复 | [会话恢复](../03-mechanisms/session-recovery.md) |
| AEC / 降噪放在哪 | AEC 只能在有扬声器参考的那台机器上 | [音频前处理](../03-mechanisms/audio-preprocessing.md) |
| 全双工是否可行 | 设备没有 AEC 时，边播边听基本不可行 | [全双工](../02-architectures/full-duplex.md) |

---

## 3. 常见坑

- **以为用了 WebRTC 就自动有抖动缓冲和 PLC**。用 aiortc 时没有。
- **TCP 上开 FEC**，只增加码率。
- **DTX 和本地 VAD 两套机制同时处理静音**，服务端看到的时间轴对不上。
- **卡顿期间插静音**，让上游切段。
- **一次性把整段 TTS 音频推给设备**，打断时设备还会继续播几秒。
- **服务端估计播放结束就发 stop**。小智服务端在发送队列空后再 sleep `(5+2) × 60 ms` 才发 `tts stop`，这是估算，不是回执。
- **打断帧不带回合标识**。小智的 `abort` 不带回合或句子 id，迟到的音频归不到正确回合。
- **多条流共用一个 Opus 编码器**，见 [audio-basics](audio-basics.md)。
- **按 48k stereo 解码再降采样**，多了一次没必要的重采样。

---

## 4. 相关页面

- 同层：[audio-basics](audio-basics.md)、[latency-budget](latency-budget.md)、[vad](vad.md)
- 架构：[回合模型](../02-architectures/turn-model.md)、[话筒归属](../02-architectures/floor-control.md)、[全双工](../02-architectures/full-duplex.md)、[S2S](../02-architectures/s2s.md)
- 机制：[打断与截断](../03-mechanisms/interruption.md)、[会话恢复](../03-mechanisms/session-recovery.md)、[音频前处理](../03-mechanisms/audio-preprocessing.md)
