# xiaozhi-esp32

> 仓库：https://github.com/78/xiaozhi-esp32
> 许可：MIT（见仓库 LICENSE 文件）
> 分析基于：commit `8ce50d2`（2026-09-26）
> 状态：草稿
> 最后更新：2026-10-02

## 定位

小智（xiaozhi）是跑在乐鑫 ESP32 系列 MCU 上的**语音助手固件**：麦克风采集、唤醒词、（可选）回声消除、Opus 编解码、联网收发音频、屏幕与灯效显示，以及把设备能力以 MCP 工具的形式暴露给服务端。ASR、判停、LLM、TTS 都不在设备上，由服务端完成（官方云服务或开源的 [xiaozhi-esp32-server](xiaozhi-esp32-server.md)）。

面向：做低成本 AI 语音硬件（玩具、桌面助手、开发板）的开发者。仓库支持大量现成板型（`main/boards/` 下按厂商分目录），芯片覆盖 ESP32、ESP32-S3、ESP32-C3/C5/C6、ESP32-P4 等。

和同类的区别：

- 它是"**哑终端 + 厚服务端**"：端侧只做声学前端和状态机，所有智能在云端，适合 MCU 级算力。
- 协议是公开的、轻量的（WebSocket JSON + 裸 Opus，或 MQTT + 加密 UDP），已经形成一个服务端生态（官方服务、多个开源服务端实现）。
- 设备自己是 MCP server，服务端可以列出并调用设备工具（音量、亮度、拍照等）。

## 整体架构

```
麦克风 ─► AudioCodec ─► AudioInputTask ─► AudioEngine（二选一）
                                          ├─ AfeAudioEngine（S3 / P4 / S31 + PSRAM）：ESP-SR AFE = AEC + VADNet + WakeNet
                                          └─ LiteAudioEngine（ESP32 / C3 / C5 / C6）：原始 PCM + 独立 WakeNet
                                                  │ 16 kHz mono PCM
                                                  ▼
                                   encode 队列 ─► OpusCodecTask（编码 16 kHz / 60 ms）─► send 队列（≤2.4 s）
                                                                                          │
                                                       Application（主循环 + 状态机）◄────┘
                                                                │ Protocol（WebSocket 或 MQTT+UDP）
                                                                ▼
                                                             服务端
                                                                │ 下行 Opus（默认 24 kHz / 60 ms）
                                                                ▼
            只在 Speaking 态入队 ─► decode 队列（≤1.2 s，满了丢）─► OpusCodecTask 解码 ─► playback 队列（2 帧）─► 喇叭
```

- **线程模型**：FreeRTOS 任务。`AudioService` 有独立的输入、输出、Opus 编解码任务（`main/audio/audio_service.h:28-37` 的注释）；`Application::Run` 是主事件循环，其他任务通过 `Schedule()` 把回调投递到主循环执行。
- **状态机**：`main/device_state_machine.cc`，核心状态 Idle / Connecting / Listening / Speaking / Notifying，加上配网、激活、升级等。Idle 可以直接转 Speaking（`device_state_machine.cc:71-79`），这让"松键后回 Idle、服务端回复时再进 Speaking"成立。
- **音频通道按需建立**：WebSocket 只在需要说话时连接（`websocket_protocol.cc:20-21`），会话 id 来自服务端 hello。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 主循环与状态处理 | `main/application.cc:Application::Run` / `HandleStateChangedEvent` | 各状态下开关语音处理、唤醒词、清解码器 |
| 协议选择 | `main/application.cc:InitializeProtocol` | OTA 配置里有 `mqtt` 用 MQTT，否则有 `websocket` 用 WebSocket，都没有默认 MQTT（`:538-545`） |
| 协议基类 | `main/protocols/protocol.h:Protocol`、`protocol.cc` | `SendStartListening` / `SendStopListening` / `SendWakeWordDetected` / `SendAbortSpeaking` / `SendMcpMessage`；120 s 无下行判超时 |
| WebSocket | `main/protocols/websocket_protocol.cc:WebsocketProtocol` | 请求头、hello、二进制帧 v1/v2/v3 |
| MQTT + UDP | `main/protocols/mqtt_protocol.cc:MqttProtocol` | MQTT 传 JSON，UDP 传 AES-CTR 加密的 Opus |
| 下行 JSON 处理 | `main/application.cc:InitializeProtocol` 中 `OnIncomingJson` 回调 | `tts` / `stt` / `llm` / `mcp` / `system` / `alert` / `custom` / `notify` |
| 下行音频门控 | `main/application.cc` 中 `OnIncomingAudio` 回调 | 只有 Speaking 态才把包推进解码队列 |
| 按键说话 | `Application::HandleStartListeningEvent` / `HandleStopListeningEvent` | 按下：Idle → Listening(manual)，Speaking → abort + Listening(manual)；松开：发 `listen stop` 并立即回 Idle |
| 点击切换 | `Application::HandleToggleChatEvent` | Idle → 默认模式开始听；Speaking → 只发 abort；Listening → 关闭音频通道 |
| 唤醒词 | `Application::HandleWakeWordDetectedEvent` / `ContinueWakeWordInvoke` | 可上传唤醒前 2 s 音频，再发 `listen detect` |
| 音频服务 | `main/audio/audio_service.cc:AudioService` | 队列、Opus 编解码、`playback_generation_` 代际、`ResetDecoder` |
| AFE 配置 | `main/audio/engines/afe_audio_engine.cc` | AEC / VAD / WakeNet 参数（`:143-179`） |
| 设备 MCP | `main/mcp_server.cc:McpServer` | 设备工具：`self.get_device_status`、`self.audio_speaker.set_volume`、`self.screen.*`、`self.camera.take_photo` 等 |
| 协议文档 | `docs/websocket_zh.md`、`docs/mqtt-udp_zh.md`、`docs/mcp-protocol_zh.md` | 官方协议说明 |

## 设备到服务端的协议

### 传输与握手

| 项 | WebSocket | MQTT + UDP |
|---|---|---|
| 控制消息 | WebSocket 文本帧，JSON | MQTT，JSON |
| 音频 | WebSocket 二进制帧 | UDP，AES-128-CTR 加密 |
| 鉴权 / 标识 | 请求头 `Authorization`、`Protocol-Version`、`Device-Id`（MAC）、`Client-Id`（UUID）（`websocket_protocol.cc:102-106`） | MQTT 账号；UDP 的 key / nonce 在服务端 hello 里下发（`docs/mqtt-udp_zh.md`） |
| 握手 | 设备发 `hello`，10 s 内收不到服务端 `hello` 判失败（`websocket_protocol.cc:183-190`） | 设备发 `hello`（`transport: "udp"`），服务端回 UDP 地址和密钥 |
| 结束 | 关闭 WebSocket | `goodbye` 消息 |

设备 hello（`websocket_protocol.cc:199-223`）：

```json
{"type":"hello","version":1,"features":{"mcp":true,"aec":true},
 "transport":"websocket",
 "audio_params":{"format":"opus","sample_rate":16000,"channels":1,"frame_duration":60}}
```

`features.aec` 只在编译了服务端 AEC 时出现。服务端 hello 带 `session_id` 和下行 `audio_params`（固件默认假设 24 kHz / 60 ms，`protocol.h:79-80`）。

### 二进制帧

| 版本 | 格式 | 用途 |
|---|---|---|
| v1（默认） | 裸 Opus 包 | — |
| v2 | 16 字节头：`version(2) type(2) reserved(4) timestamp(4) payload_size(4)` + payload | `timestamp` 给服务端 AEC 对齐用（`protocol.h:19-26`） |
| v3 | 4 字节头：`type(1) reserved(1) payload_size(2)` + payload | 更短的头（`protocol.h:28-33`） |
| UDP | `type(1) flags(1) payload_len(2) ssrc(4) timestamp(4) sequence(4)` + 加密 payload；这 16 字节同时作 AES-CTR nonce | 接收端丢弃 `seq <= last`（`mqtt_protocol.cc:300-363`） |

### JSON 消息

| 方向 | 消息 | 说明 |
|---|---|---|
| 设备→服务端 | `{"type":"listen","state":"start","mode":"auto|manual|realtime"}` | 开始收音，判停模式随每次 start 声明（`protocol.cc:82-94`） |
| 设备→服务端 | `{"type":"listen","state":"stop"}` | 只在 manual 模式松键时发（`protocol.cc:96-100`） |
| 设备→服务端 | `{"type":"listen","state":"detect","text":"<唤醒词>"}` | 唤醒词命中，也可携带任意文本（`protocol.cc:75-80`） |
| 设备→服务端 | `{"type":"abort","reason":"wake_word_detected"}` | 打断；reason 只有唤醒词一种或省略（`protocol.cc:66-73`） |
| 双向 | `{"type":"mcp","payload":{JSON-RPC 2.0}}` | 设备是 MCP server，服务端是 client |
| 服务端→设备 | `{"type":"stt","text":"..."}` | 识别结果，用于屏幕显示 |
| 服务端→设备 | `{"type":"llm","emotion":"happy","text":"😀"}` | 表情 |
| 服务端→设备 | `{"type":"tts","state":"start"}` / `"sentence_start","text"` / `"stop"` | 进入 Speaking / 显示当前句 / 播放结束（`application.cc:619-654`） |
| 服务端→设备 | `system`（如 reboot）、`alert`、`custom`、`notify`（`audio_url` + 字幕，设备自行拉取播放） | 见 `application.cc:586-716` |

所有消息都带 `session_id`。

### 有没有 turn id

**没有。** 协议里只有连接级的 `session_id`，没有回合 id、句子 id 或序号。防止旧回合的音频串到新回合，靠的是：

1. **状态门控**：下行音频只在 Speaking 态入队，其他状态直接丢（`application.cc:554-558`）。
2. **进入 Speaking / Listening 时清解码器**：`HandleStateChangedEvent` 进 Speaking 时调 `ResetDecoder()`（`application.cc:1054`）；进 Listening 时，如果音频处理还没在运行，会走 `StartListeningAudio` → `EnableVoiceProcessing(true)`，内部也调 `ResetDecoder()`（`application.cc:1030-1045`、`audio_service.cc:709-716`）。
3. **设备内部代际**：`playback_generation_` 在 `ResetDecoder` 时递增，正在等队列空位的旧包被拒绝（`audio_service.cc:182`、`:609-626`）。这个代际不上协议。
4. 服务端那边另有一个 `sentence_id`（实际是回合 id），只在服务端内部过滤，不下发（见 [xiaozhi-esp32-server](xiaozhi-esp32-server.md)）。

## 端侧音频链路

| 环节 | 实现 | 默认 | 依据 |
|---|---|---|---|
| 上行编码 | Opus 16 kHz 单声道，60 ms 帧，complexity 0，VBR，**DTX 开**，FEC 关，application=AUDIO | — | `audio_service.h:40`、`:66-80` |
| 下行解码 | Opus，按服务端 hello 的采样率；和本机输出采样率不同时重采样并打警告 | 24 kHz / 60 ms | `application.cc:560-567` |
| 队列 | 解码队列 ≤1.2 s（20 包），满了网络入口直接丢；发送队列 ≤2.4 s；播放队列 2 帧 | — | `audio_service.h:41-44`、`audio_service.cc:609-626` |
| AEC | 三选一，编译期决定：关 / 设备端 / 服务端 | **关**（`kAecOff`） | `application.cc:26-34` |
| 设备端 AEC | ESP-SR AFE，`AEC_MODE_FD_LOW_COST` + `AEC_NLP_LEVEL_VERYAGGR`；只对白名单板型开放，需要硬件回采参考和声学隔离 | n | `afe_audio_engine.cc:150-152`、`Kconfig.projbuild:961-973` |
| 服务端 AEC | 设备把最近播放的下行帧时间戳塞进上行 v2 帧，服务端做对齐和抵消。这是服务端近似方案，效果依赖设备回报的播放时间戳精度，不等同于端侧 AEC。 | n，Kconfig 标"Unstable" | `Kconfig.projbuild:975-980`、`audio_service.cc:358-367`、`:580-582` |
| 降噪 | 关：项目不带 NSNet 模型 | 关 | `afe_audio_engine.cc:153`、`main/audio/README.md:22-24` |
| AGC | 关 | 关 | `afe_audio_engine.cc:178` |
| 端侧 VAD | AFE 的 VADNet（`VAD_MODE_0`，`vad_min_noise_ms=100`），**只用来刷新 LED**，不参与判停 | — | `afe_audio_engine.cc:154-160`、`application.cc:258-263` |
| 唤醒词 | S3 / P4 / S31 + PSRAM 默认 AFE 内的 WakeNet；可选 MultiNet 自定义命令词；小芯片用独立 WakeNet、上行无 AEC | — | `Kconfig.projbuild:881-910`、`main/audio/README.md:12-15` |
| 唤醒前音频上传 | 唤醒时把最近 2 s PCM（64 KB PSRAM 环形缓冲）编码上传，给服务端做声纹等 | y | `Kconfig.projbuild:935-940`、`application.cc:978-984` |

AEC 在唤醒词检测期间常开，这样设备播放时也能被唤醒词打断（`main/audio/README.md:46-49`）。部分板型可以在运行时用按键或 MCP 切换 AEC 开关（`Application::SetAecMode`，如 `boards/lckfb/szpi-esp32s3/lichuang_dev_board.cc:132`），切换后会关闭音频通道。

## 三种收音模式

| | manual（按住说话） | auto（点一下说话） | realtime（全双工） |
|---|---|---|---|
| 触发 | 按下开始、松开结束 | 点击或唤醒词 | 同 auto |
| 判停 | 松键发 `listen stop` | 服务端 VAD | 服务端 VAD |
| 播放时麦克风 | 关 | 关 | **开**，持续上行 |
| 何时用 | 板子配了按键 | AEC 关时的默认模式 | AEC 开时的默认模式（`application.cc:1188-1190`） |
| `tts stop` 后 | 回 Idle | 回 Listening，继续下一轮 | 回 Listening |

auto 模式下进入 Listening 时，如果本地播放队列还没放完，会等播放排空再开始收音，避免网络抖动导致 `tts stop` 早到时把尾音截掉（`application.cc:1033-1043`）。

## 打断在端侧怎么处理

| 路径 | 触发 | 设备动作 |
|---|---|---|
| 按住说话 | Speaking 态按下 | `AbortSpeaking()` 发 `abort` → 切到 Listening(manual)；进 Listening 时清解码器（`application.cc:873-876`） |
| 点击 | Speaking 态点击 | **只发 `abort`**，不切状态（`application.cc:815-816`）。服务端回 `tts stop` 后转 Listening(auto)，但 auto 模式会等本地播放队列排空才开始收音（`application.cc:1033-1043`），所以已缓冲的 ≤1.2 s 音频很可能会播完（按代码推断，待实机确认） |
| 唤醒词 | Speaking 或 Listening 态检测到唤醒词（需 AFE） | 发 `abort(reason=wake_word_detected)`，清空上行发送队列，重新进入 Listening（`application.cc:897-930`） |
| 说话即打断 | realtime 模式，靠 AEC | 设备不做判断，只持续上行；由服务端决定打断，再下发 `tts stop` |

注意：`AbortSpeaking()` 里置的 `aborted_` 标志在全仓库没有读取点（只在 `application.cc:626`、`:1177` 写），是死代码；真正停止播放靠状态切换。设备也**不回传播放进度**：没有 playback started / ended 回执，服务端只能估算用户听到了哪里。

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：端侧不判停。manual 模式由松键决定（`Application::HandleStopListeningEvent` 发 `listen stop`）；auto / realtime 交给服务端，设备通过 `listen start` 的 `mode` 字段声明。端侧 VADNet 只用于 LED。
- [打断与截断](../../03-mechanisms/interruption.md)：按键、点击、唤醒词三条本地触发路径都发 `abort`（`Application::AbortSpeaking`），本地缓冲只有在随后发生状态切换时才由 `ResetDecoder` 和 `playback_generation_` 清掉：按键打断会切到 Listening(manual) 并清解码器（`application.cc:873-876`），唤醒词打断会重新进入 Listening；点击打断只发 `abort`、不切状态（`HandleToggleChatEvent`，`application.cc:815-816`），等服务端回 `tts stop` 转 Listening(auto) 后还要等播放队列排空（`application.cc:1033-1043`），已缓冲的音频可能播完（推断，待实机确认）；realtime 模式的"说话即打断"由服务端决定。没有回合 id，也没有播放位置回执，截断无从谈起。
- [首音优化](../../03-mechanisms/first-audio.md)：端侧主要靠浅缓冲（解码队列 ≤1.2 s、播放 2 帧）和唤醒前 2 s 音频预上传；首音的大头在服务端。60 ms 的 Opus 帧比 20 ms 帧多约 40 ms 的分帧延迟。
- [工具回合](../../03-mechanisms/tool-calls.md)：设备是 MCP server（`main/mcp_server.cc:McpServer`），通过 `mcp` 消息承载 JSON-RPC 2.0，服务端可 `tools/list`、`tools/call`；工具回合的编排在服务端。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：不支持恢复。会话 = 一次音频通道连接，`session_id` 由服务端 hello 下发；断线后重新 hello 得到新会话，没有断点续传或序号补齐。120 s 无下行判超时（`Protocol::IsTimeout`）。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：AEC 可选（设备端 ESP-SR AFE 或服务端，默认都关），NS 和 AGC 都关，见 `main/audio/engines/afe_audio_engine.cc`。小芯片走 `LiteAudioEngine`，上行是原始 PCM。
- [评测](../../03-mechanisms/evaluation.md)：不涉及。只有调试工具：`USE_AUDIO_DEBUGGER` 把音频通过 UDP 发到调试服务器（`main/audio/audio_debugger.cc`），以及配网状态下的音频回环测试。

## 取舍与局限

- **协议轻但弱**：无回合 id、无回合终态、无错误帧、无播放回执、事件无 ack。它能跑通，靠的是"松键即回 Idle + 只在 Speaking 态收下行 + 服务端按实时节奏下发"这三件事配合。迟到的 `tts`/`stt` 帧无法归属到正确的回合。
- **点击打断不立即停播**：只发 `abort` 不切状态，也不清本地解码队列；已缓冲的最多约 1.2 s 音频大概率会播完（推断，待实机确认）。按键打断因为会切到 Listening(manual) 并清解码器，停得更干脆。
- **AEC 依赖硬件**：设备端 AEC 只对有回采通道且做了声学隔离的白名单板子开放；服务端 AEC 标注 Unstable。没有 AEC 时只能半双工（播放时关麦）。
- **不做降噪和 AGC**：远场、小声、嘈杂环境下的识别率完全依赖服务端 ASR 的鲁棒性。
- **60 ms 帧、DTX 开**：省 CPU 和流量，适合 MCU；代价是延迟粒度粗，DTX 会让服务端看到不连续的时间轴。
- **下行队列满了直接丢包**：服务端如果下发快于实时，设备会丢音频而不是阻塞，所以服务端必须限速。

## 相关

- 机制页：[打断与截断](../../03-mechanisms/interruption.md)、[音频前处理](../../03-mechanisms/audio-preprocessing.md)、[会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)、[工具回合](../../03-mechanisms/tool-calls.md)
- 项目页：[xiaozhi-esp32-server](xiaozhi-esp32-server.md)、[webrtc-audio-processing](../audio-processing/webrtc-audio-processing.md)
- 对比页：待补
