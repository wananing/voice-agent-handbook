# aiortc

> 仓库：https://github.com/aiortc/aiortc
> 分析基于：commit `8a28646`（2026-07-17）；Pipecat 接入部分基于 pipecat `422ad13`
> 状态：草稿
> 最后更新：2026-10-02

## 定位

aiortc 是**纯 Python（asyncio）实现的 WebRTC / ORTC 协议栈**：SDP、ICE（基于 aioice）、DTLS、SRTP、SCTP 数据通道、RTP/RTCP、音视频编解码（通过 PyAV 调 libopus、libvpx、libx264）。API 风格模仿浏览器的 JavaScript API，Promise 换成协程（`README.rst:32-40`）。

README 对自己的定位很直白：浏览器和 libwebrtc 的实现久经考验但内部复杂、没有 Python 绑定、和媒体栈耦合紧；aiortc 的实现"简单可读"，适合理解 WebRTC、在 Python 里插入音视频处理算法（`README.rst:51-62`）。

在 voice agent 里的角色：**服务端的 WebRTC 传输层**，让浏览器或移动端用 WebRTC 直连 Python 进程，不需要 SFU。Pipecat 的 `SmallWebRTCTransport` 就是基于它；LiveKit 不用它（LiveKit 的 Python SDK 底层是 Rust + libwebrtc）。

许可：BSD-3-Clause（`pyproject.toml:10`），要求 Python ≥ 3.10。

## 整体架构

```
            信令（应用自己实现，aiortc 只给 SDP 对象）
                              │
RTCPeerConnection ── RTCIceTransport（aioice） ── RTCDtlsTransport（pyOpenSSL + pylibsrtp）
       │                                                    │
       ├─ RTCRtpTransceiver ─┬─ RTCRtpSender：track.recv() → 编码（executor）→ RTP 打包 → SRTP
       │                     └─ RTCRtpReceiver：SRTP → RTP → JitterBuffer → 解码线程 → RemoteStreamTrack 队列
       └─ RTCSctpTransport（纯 Python SCTP）── RTCDataChannel
```

- **全 asyncio**，单事件循环；编码放到默认线程池（`rtcrtpsender.py:318-319`），每个接收器起一个解码线程（`rtcrtpreceiver.py:386-395`）。
- **发送节奏由 track 决定**：`MediaStreamTrack.recv()` 自己 sleep 到下一帧时间点，aiortc 不做额外的发送队列或 pacing（`mediastreams.py:85-100`，`AUDIO_PTIME = 0.020`）。
- **接收端没有播放时钟**：`RemoteStreamTrack.recv()` 只是从 `asyncio.Queue` 里取解码好的帧（`rtcrtpreceiver.py:191-210`）。消费得慢，队列就会积压。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 会话 | `src/aiortc/rtcpeerconnection.py:RTCPeerConnection` | `createOffer` / `createAnswer` / `setLocalDescription` / `setRemoteDescription`、`addTrack`、`createDataChannel` |
| 音频编解码 | `src/aiortc/codecs/opus.py:OpusEncoder` / `OpusDecoder` | 固定 48 kHz、立体声、960 样本（20 ms）一帧、96 kbps、`application=voip`；解码固定输出 48 kHz 立体声 s16 |
| 支持的音频编解码 | `src/aiortc/codecs/__init__.py:CODECS` | Opus 48000/2、G722、PCMU、PCMA |
| 抖动缓冲 | `src/aiortc/jitterbuffer.py:JitterBuffer` | 固定容量的环形重排缓冲；音频 `capacity=16, prefetch=4`（`rtcrtpreceiver.py:276`） |
| 丢包恢复 | `src/aiortc/rtcrtpreceiver.py:NackGenerator` | **只有视频**用 NACK 和 PLI，音频的 `nack_generator=None`（`rtcrtpreceiver.py:276-281`） |
| 接收统计 | `src/aiortc/rtcrtpreceiver.py:StreamStatistics` | RFC 3550 抖动估计、丢包统计 |
| RTCP SR | `src/aiortc/rtcrtpsender.py` | 发送 NTP↔RTP 时间戳对应关系，接收端据此算 RTT |
| 媒体工具 | `src/aiortc/contrib/media.py:MediaPlayer` / `MediaRecorder` / `MediaRelay` / `MediaBlackhole` | 文件 / 设备读写、一路 track 分发给多个消费者 |
| 信令示例 | `src/aiortc/contrib/signaling.py` | 命令行 / TCP / Unix socket 信令，仅供示例 |
| Pipecat 接入：连接 | `pipecat/src/pipecat/transports/smallwebrtc/connection.py:SmallWebRTCConnection` | 包装 `RTCPeerConnection`，处理重协商、断线检测 |
| Pipecat 接入：输出 track | `pipecat/.../smallwebrtc/transport.py:RawAudioTrack` | 按 10 ms 切块，用"起始时间 + 已发样本数"算 sleep，和 aiortc `AudioStreamTrack` 同一写法 |
| Pipecat 接入：输入重采样 | `pipecat/.../smallwebrtc/transport.py` 中 `_audio_in_resampler` | 把 aiortc 解出的 48 kHz 立体声用 PyAV `AudioResampler` 转成管线采样率和声道 |

## 作为传输层的能力

| 能力 | 状态 | 说明 |
|---|---|---|
| 浏览器互通 | 有 | 定期和 Chrome、Firefox 测互通（`README.rst:70-71`） |
| ICE / STUN / TURN | 有 | 由 aioice 实现，支持 half-trickle 和 mDNS（`README.rst:75`） |
| 音频编解码 | Opus、G.722、PCMU、PCMA | Opus 参数写死，见上表 |
| 数据通道 | 有 | 纯 Python SCTP；可用于传控制消息、文字、打断信号 |
| RTCP 统计 | 有 | RR / SR、抖动、RTT |
| 音频抗丢包 | **弱** | README 列了"NACK / PLI"（`README.rst:84`），但代码里只用于视频；音频无 NACK、无 PLC、编码器未开 FEC / DTX（SDP 能解析 `useinbandfec`，`sdp.py:31`，但编码器没有对应设置） |
| 自适应抖动缓冲 | **无** | 固定 16 包重排缓冲 |
| 音频前处理 | **无** | 不做 AEC、NS、AGC；这些要在客户端（浏览器）或应用层做 |
| 多方 / SFU | 无 | 点对点；多人场景要另找 SFU |

## 限制（对 voice agent 的影响）

- **缺包时会卡住**：抖动缓冲从起点往后拼帧，遇到空位就停；只有新包序号超出容量才丢掉最老的空位（`jitterbuffer.py:50-54`、`:70-74`）。音频 20 ms 一包、容量 16，一个丢包可能导致后续音频滞留约 320 ms 后突发吐出（按代码推断，未实测）。没有 PLC，时间轴会被压缩。对 VAD 和 ASR 来说，这意味着弱网下会看到"卡顿再突发"的输入。
- **Opus 参数不可配**：编码固定 48 kHz 立体声 96 kbps，对语音来说码率偏高；解码固定输出 48 kHz 立体声，下游几乎总要再重采样到 16 kHz 单声道（Pipecat 就是这么做的）。
- **接收队列会积压**：消费方慢了，`RemoteStreamTrack._queue` 只会变长。Pipecat 为此直接访问私有属性 `_queue` 清空旧帧（`smallwebrtc/connection.py:SmallWebRTCTrack.discard_old_frames`），并在注释里说明这依赖 aiortc 内部实现。
- **没有 PMTU 发现**：aiortc 把 SCTP 数据块写死为 1200 字节，在 IPv6 / VPN 的 1280 MTU 路径上会超限，数据通道静默卡死。Pipecat 用猴子补丁把它改成 1100（`smallwebrtc/connection.py:41-62`）。
- **连接状态不完整**：Pipecat 多处注释指出 aiortc 没有"disconnected"状态、transceiver 方向不总按 SDP 处理（`smallwebrtc/connection.py:350`、`:367`、`:400`）。
- **性能**：纯 Python 加 GIL，单进程能承载的并发连接数有限，具体数字仓库里没有，待确认。
- **NAT 穿透**：需要自己配 STUN / TURN 服务器（`RTCConfiguration.iceServers`），否则对称 NAT 下连不上。

## 与 Pipecat / LiveKit 的关系

| | Pipecat SmallWebRTC | Pipecat Daily / LiveKit transport | livekit-agents |
|---|---|---|---|
| 底层 | aiortc | 厂商 SDK（libwebrtc） | LiveKit Rust SDK（libwebrtc） |
| 拓扑 | 浏览器 ↔ Python 进程，点对点 | 经过 SFU | 经过 LiveKit SFU |
| 抖动缓冲 / PLC | aiortc 的固定缓冲，无 PLC | NetEQ（libwebrtc） | NetEQ（libwebrtc） |
| 适合 | 本地开发、一对一、无需额外基础设施 | 生产、弱网 | 生产、弱网、多方 |

Pipecat 把 aiortc 作为可选依赖：`webrtc = ["aiortc>=1.14.0,<2", ...]`（`pipecat/pyproject.toml:158`）。livekit-agents 仓库里没有 aiortc 依赖。

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：不涉及。
- [打断与截断](../../03-mechanisms/interruption.md)：不直接涉及。打断时清下行音频要靠上层：Pipecat SmallWebRTC 的 `RawAudioTrack` 每次只交出 10 ms，服务端清掉自己的队列即可，客户端浏览器的缓冲很浅。数据通道可以用来传打断信号。
- [首音优化](../../03-mechanisms/first-audio.md)：间接相关。UDP + 20 ms 帧本身延迟低；发送节奏由 track 的 `recv()` 控制（`mediastreams.py:85-100`），没有额外缓冲。
- [工具回合](../../03-mechanisms/tool-calls.md)：不涉及。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：不涉及应用层会话恢复。连接断开后需要重新走 offer / answer；aiortc 源码里搜不到 ICE restart（`restartIce`），Pipecat 自己处理重协商（`smallwebrtc/connection.py:RenegotiateMessage`）。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：不涉及。aiortc 不处理音频内容；AEC / NS / AGC 依赖浏览器端的 getUserMedia 约束。NS 可在服务端用 `audio_in_filter` 接（Pipecat `audio/filters/` 下只有 RNNoise、Koala、Krisp VIVA、ai-coustics 这类降噪 / 增强实现，接口 `BaseAudioFilter.filter(audio)` 只吃上行音频、没有远端参考信号），AEC 只能在浏览器端。
- [评测](../../03-mechanisms/evaluation.md)：不涉及 voice agent 评测。仓库有完整的单元测试，`tests/test_jitterbuffer.py` 可用来理解缺包行为；RTCP 统计（`getStats`）可用于线上监控抖动和丢包。

## 取舍与局限

- 优点：纯 Python、可读、可以在帧级插入任意处理；不需要 SFU，部署最简单。
- 代价：音频抗弱网能力远不如 libwebrtc（无 NACK、无 PLC、无自适应抖动缓冲），Opus 参数写死，几个内部行为需要框架打补丁。
- 结论：适合开发、演示、一对一和网络条件可控的场景。生产环境、移动网络、多方通话更适合基于 libwebrtc 的方案（Daily、LiveKit）。"换成 WebRTC 就能解决弱网"只有在两端都是 libwebrtc 时才成立。

## 相关

- 机制页：[打断与截断](../../03-mechanisms/interruption.md)、[首音优化](../../03-mechanisms/first-audio.md)、[音频前处理](../../03-mechanisms/audio-preprocessing.md)
- 项目页：[pipecat](../frameworks/pipecat.md)、[livekit-agents](../frameworks/livekit-agents.md)、[webrtc-audio-processing](webrtc-audio-processing.md)
- 对比页：待补
