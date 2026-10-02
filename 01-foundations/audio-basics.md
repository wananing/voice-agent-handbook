# 音频基础：采样率、帧、编码

> 状态：草稿
> 最后更新：2026-10-01

这一页只讲语音 agent 链路里真正会碰到的几个参数：采样率、帧长、PCM / Opus、声道数、重采样。目标是看懂后面各页里"16k 单声道 20 ms 帧""解码到 48k 再降采样"这类说法，以及它们为什么会影响延迟和正确性。

---

## 1. 核心概念

### 1.1 采样率

一条语音链路里通常同时存在好几个采样率，各段用各自的：

| 采样率 | 典型用途 | 例子（来自本手册分析的仓库） |
|---|---|---|
| 8 kHz | 电话网（PSTN），G.711 μ-law / A-law | Pipecat 的 Twilio serializer 默认 8000 Hz，收发时做 μ-law ↔ PCM 转换；openai-realtime-agents 的 demo 可以切到 PCMU/PCMA（8 kHz）听电话音质 |
| 16 kHz | ASR、VAD、判停模型的输入 | Silero VAD 只收 8k / 16k；TEN VAD 只收 16k；smart-turn 输入 16k；SenseVoice server 只收 16k mono PCM16；FunASR runtime 收 8k / 16k，其他采样率内部重采样 |
| 24 kHz | TTS 和 S2S 模型的输出 | CosyVoice2/3 输出 24k（CosyVoice1 是 22.05k）；Moshi 的 Mimi codec、unmute 都是 24k；小智服务端下行默认 24k；LiveKit room_io 输入默认 `sample_rate=24000` |
| 48 kHz | WebRTC 上的 Opus、部分降噪算法 | aiortc 的 Opus 编解码写死 48k；SDP 里协商的是 `opus/48000/2`；RNNoise 只吃 48k |

经验法则：**识别侧 16k 足够，合成侧 24k 起步，传输侧看协议。** fish-speech 这类输出 44.1k 的 TTS 也存在，但对语音 agent 的音质收益和带宽代价要另算。

### 1.2 帧（frame）

"帧"在不同层有不同含义，讨论时要说清是哪一层：

| 层 | 典型帧长 | 说明 |
|---|---|---|
| 编解码帧 | Opus 20 ms 最常见；可选 2.5 / 5 / 10 / 20 / 40 / 60 ms | aiortc 固定 960 样本 @48k = 20 ms；小智固件上行 16k、60 ms 一帧 |
| 传输块 | 20–60 ms | Pipecat 输出按 `10 ms × 4 = 40 ms` 切块后限速下发；LiveKit room_io 输入 `frame_size_ms=50` |
| 前处理帧 | 10 ms | RNNoise 每帧 480 样本 @48k = 10 ms；WebRTC APM 也是 10 ms 帧 |
| 模型窗口 | 各模型自定 | Silero VAD：16k 下 512 样本（32 ms），8k 下 256 样本；TEN VAD：16k 下 hop 160 或 256 样本（10 / 16 ms） |

帧长的取舍：

- **10 ms**：粒度最细，适合前处理和 VAD；包数多，在网络上开销大。
- **20 ms**：Opus 和 WebRTC 的默认，延迟和开销的折中。
- **40 / 60 ms**：包数少，省 CPU、省包头（对 ESP32 这类设备有意义），代价是每帧多 20–40 ms 的组帧延迟，判停粒度也更粗。

注意编解码帧和模型窗口往往**不对齐**：20 ms 的 Opus 帧解出来 320 样本，Silero 要 512 样本一窗，中间必须有缓冲拼接。

### 1.3 PCM 与 Opus

**PCM** 是未压缩的采样序列。agent 链路里几乎都是 **int16 小端、单声道**（常写作 PCM16 / s16le）。算一下量级：

- 16k × 2 字节 = 32 KB/s（256 kbps）；20 ms 一帧 = 320 样本 = 640 字节。
- 24k 下 20 ms = 480 样本；48k 下 10 ms = 480 样本。

例外要当心：sherpa-onnx 的 C++ websocket 服务端收的是 **float32** 采样，不是 int16。

**Opus** 是语音 agent 网络传输的事实标准：

- 内部采样率只有 8 / 12 / 16 / 24 / 48 kHz 五档（Pipecat MoQ transport 注释里有说明），解码时可以直接指定输出采样率。
- **有状态**：编码器和解码器都带历史，一条流一个实例，不能多条流共用，也不能在流中间换实例。小智服务端踩过的坑：音乐文件和 TTS 流共用一个 Opus 编码器，触发 SILK 断言，后来改成独立编码器。
- 几个开关：
  - **DTX**：静音时不发包。省带宽，但接收端看到的是时间轴上的空洞。
  - **带内 FEC**：给 UDP 丢包准备的冗余，TCP 上只增加码率。
  - **PLC**：解码器在缺包时"猜"一段音频，填补短缺口。
- 小智固件的上行参数：16 kHz、单声道、60 ms、complexity 0、VBR、DTX 开、FEC 关。aiortc 编码器：48k、stereo、20 ms、96 kbps、`application=voip`，FEC 和 DTX 都没开。

ASR 服务一般**不直接收 Opus**（FunASR、SenseVoice server、sherpa-onnx 都要 PCM），网关要先解码。

### 1.4 单声道

语音 agent 全链路按单声道处理。双声道一般只出现在两个地方：

- WebRTC 协商成 `opus/48000/2`，解出来是 48k stereo，要 downmix 成 mono（Pipecat SmallWebRTC 拿到 aiortc 的输出后用 PyAV 转成单声道和管线采样率）。
- 素材文件（提示音、音乐）本身是双声道。

### 1.5 重采样的代价

每次重采样都有三种代价：

1. **CPU**：高质量滤波器不便宜。Pipecat 输出端默认 soxr VHQ；RNNoise 过滤器做 16↔48 转换用最快的 QQ 档；LiveKit Silero 插件只在 VAD 推理支路降到 16k，用 `QUICK` 档（注释写"VAD doesn't need high quality"）。三档的 CPU 开销仓库里没有数字（待确认）。
2. **延迟**：滤波器需要前后样本，会引入几毫秒级的延迟（具体值取决于滤波器，待确认）。
3. **块边界**：按块重采样时，如果每块独立处理，块边界会出现爆音。流式重采样器要**跨块保留滤波器状态**，在流结束时 flush 尾巴，否则会吞掉最后几毫秒。Pipecat 的 soxr 流式重采样器在停顿超过 0.2 s（`clear_after_secs=0.2`）时清历史。

降低重采样次数的两个做法：

- libopus 直接解码到 16k，**不要先解到 48k 再降**。aiortc 的"48k 解码 + 重采样"是 WebRTC 协商带来的额外开销，自有设备可以避免。
- 必须在 48k 上跑的处理（比如 RNNoise）放在降采样之前：**解码到 48k → 降噪 → 降到 16k → VAD / ASR**，全程只降一次。

---

## 2. 对 agent 设计的影响

| 决策 | 跟本页哪个参数有关 | 去哪看 |
|---|---|---|
| 上行用多大的帧 | 帧长直接加在"松键 → 定稿"之前；包数影响设备 CPU 和流量 | [transport](transport.md)、[latency-budget](latency-budget.md) |
| 前处理链怎么排 | 降噪、VAD 各自要求的采样率决定了顺序 | [音频前处理](../03-mechanisms/audio-preprocessing.md) |
| 判停粒度 | VAD 窗口 10–32 ms；传输帧 60 ms 时判停只能按 60 ms 走 | [vad](vad.md)、[判停](../03-mechanisms/turn-detection.md) |
| 按已播放位置截断上下文 | 要用采样数（而不是墙钟）计算"播到哪了" | [打断与截断](../03-mechanisms/interruption.md) |
| 本地合成的话和模型的声音拼在一起 | 两边采样率、响度要一致，否则听得出接缝 | [话筒归属](../02-architectures/floor-control.md) |
| S2S 上游的输入输出格式 | 上游定死采样率和编码，网关负责转换 | [S2S](../02-architectures/s2s.md) |

---

## 3. 常见坑

- **采样率标错**。最常见也最隐蔽：44.1k 或 24k 的文件当 16k 喂给 ASR，语速、音高全变，识别率大跌但不报错。小智服务端仓库里的 ASR 测试音频就有这个问题（提示音是 44.1k 双声道 / 24k 单声道）。
- **int16 / float32 混用**。同一段字节按另一种格式解读，结果是噪声或接近静音。
- **按字节算时长时忘了声道数和位宽**。双声道或 float32 下，"字节数 / 采样率 / 2"就错了，限速和播放位置估计跟着错。
- **WAV 头当成音频**。44 字节的 RIFF 头被当成 PCM 播出来，是一声"咔"。
- **共用 Opus 编解码器实例**。多条流（TTS、音乐、提示音）共用一个，状态互相污染。
- **为了"保持实时"在卡顿期间插静音**。上游服务端 VAD 会把插进去的静音当成用户停顿而切段。
- **重采样不保留状态**。每块独立重采样，块边界有爆音；流结束不 flush，尾音被吞。
- **帧长和 VAD 窗口不对齐**，直接把传输帧喂给 VAD，要么报错（Silero 对样本数有严格检查），要么悄悄丢掉尾部样本。

---

## 4. 相关页面

- 同层：[vad](vad.md)、[transport](transport.md)、[latency-budget](latency-budget.md)
- 机制：[音频前处理](../03-mechanisms/audio-preprocessing.md)、[判停](../03-mechanisms/turn-detection.md)、[打断与截断](../03-mechanisms/interruption.md)
- 架构：[S2S](../02-architectures/s2s.md)、[话筒归属](../02-architectures/floor-control.md)
