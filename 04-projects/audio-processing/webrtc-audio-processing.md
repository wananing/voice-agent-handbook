# webrtc-audio-processing

> 仓库：https://gitlab.freedesktop.org/pulseaudio/webrtc-audio-processing
> 分析基于：commit `d0569cf`（2025-11-10，版本 2.1，代码同步自 WebRTC M131）
> 状态：草稿
> 最后更新：2026-10-01

## 定位

这是把 Google WebRTC 里的 **AudioProcessing Module（APM）** 单独拆出来、用 Meson 构建、方便 Linux 发行版打包的副本（`README.md:1-12`）。目标是尽量不改上游代码，方便跟进；目前只包含 APM，不含编解码和网络（2.0 起 iSAC 和 webrtc-audio-coding 都删了，`NEWS:13-24`）。

APM 是一个**采集侧前处理流水线**，一个实例里包含：AEC（回声消除，AEC3 和移动端 AECM 两种）、NS（噪声抑制）、AGC（自动增益，AGC1 和 AGC2 两代）、高通滤波、前置增益等。这是开源世界里最成熟、被浏览器和会议软件大规模验证过的一套 AEC / NS / AGC。

面向：要在自己的 C/C++ 程序（或通过绑定）里用上 WebRTC 级别前处理、又不想拉整个 libwebrtc 的人。和 [RNNoise](rnnoise.md) 的区别：RNNoise 只做降噪；APM 的核心价值是 **AEC**，降噪是传统的维纳滤波类方法。

许可：BSD 风格加专利授权（`webrtc/LICENSE`、`webrtc/PATENTS`）。

## 整体架构

```
          渲染（扬声器）方向                            采集（麦克风）方向
 far-end 帧 ──► ProcessReverseStream() ─┐       near-end 帧 ──► set_stream_delay_ms()
                                       │                     ──► set_stream_analog_level()
                                       ▼                     ──► ProcessStream()
                         AEC3 用作回声参考             ┌──────────────────────────────────┐
                                                       │ 前置增益 / 采集电平调整           │
                                                       │ 高通滤波                           │
                                                       │ AEC3（或 AECM，mobile_mode）       │
                                                       │ NS（kLow / kModerate / kHigh / kVeryHigh） │
                                                       │ AGC1（模拟 / 自适应数字 / 固定数字）│
                                                       │ AGC2（自适应数字增益 + 限幅，内含 RNN VAD）│
                                                       └──────────────────────────────────┘
                                                                     │
                                                       处理后的 near-end 帧（原地写回）
                                                       recommended_stream_analog_level()
```

- 处理单位是**约 10 ms 一块**：`kChunkSizeMs = 10`，`GetFrameSize(rate) = rate / 100`（`webrtc/api/audio/audio_processing.h:723-745`）。
- 采样率 8 kHz–384 kHz；int16 接口是交错数据，float 接口是非交错数据（`audio_processing.h:83-85`）。内部最高按 48 kHz 或 32 kHz 处理（`Pipeline.maximum_internal_processing_rate`，`:157`）。
- 所有组件创建时默认关闭，通过 `Config` 打开（`audio_processing.h:68-73`）。`Config` 适合在初始化时设置，运行中频繁改会导致子模块重置，运行时调整应走 `RuntimeSetting`（`:140-145`）。
- 线程约定：`ProcessStream` 和各个 `set_stream_*` 必须在同一线程调用（`:75-81`）。
- 头文件里给的位置建议：**APM 应尽量靠近音频硬件抽象层（HAL）**；服务端一般不用 reverse stream，只对每条输入流做处理（`audio_processing.h:58-66`）。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 公共 API | `webrtc/api/audio/audio_processing.h:AudioProcessing` | `ApplyConfig` / `ProcessStream` / `ProcessReverseStream` / `AnalyzeReverseStream` / `set_stream_delay_ms` / `set_stream_analog_level` / `recommended_stream_analog_level` |
| 配置结构 | `audio_processing.h:AudioProcessing::Config` | `pipeline`、`pre_amplifier`、`capture_level_adjustment`、`high_pass_filter`、`echo_canceller`、`noise_suppression`、`gain_controller1`、`gain_controller2` |
| 创建 | `AudioProcessingBuilder().Create()` | 见头文件注释中的用法示例（`audio_processing.h:88-130`） |
| 主实现 | `webrtc/modules/audio_processing/audio_processing_impl.cc` | 流水线调度 |
| AEC3 | `webrtc/modules/audio_processing/aec3/` | 约 125 个文件，自适应 FIR 滤波 + 残余回声抑制；配置在 `api/audio/echo_canceller3_config.h` |
| AECM | `webrtc/modules/audio_processing/aecm/` | `echo_canceller.mobile_mode = true` 时使用 |
| NS | `webrtc/modules/audio_processing/ns/noise_suppressor.cc` | 分位数噪声估计 + 维纳滤波 |
| AGC1 | `webrtc/modules/audio_processing/agc/`、`gain_control_impl.cc` | 模拟增益建议 + 数字压缩 |
| AGC2 | `webrtc/modules/audio_processing/agc2/` | 自适应数字增益、限幅器、`rnn_vad/` 语音检测 |
| 经典 WebRTC VAD | `webrtc/common_audio/vad/` | GMM VAD（`webrtcvad` 那一套），APM 公共配置里没有开关 |
| 离线示例 | `examples/run-offline.cpp` | 读播放和录音两个 raw 文件，48 kHz / 10 ms 一块，依次调 `ProcessReverseStream` 和 `ProcessStream`，输出回声消除后的录音 |
| 上游同步说明 | `UPDATING.md` | 按 Chromium 使用的 WebRTC 版本同步 |

## AEC、NS、AGC 各自做什么

| 组件 | 做什么 | 需要什么输入 | 关键配置 |
|---|---|---|---|
| AEC3 | 从麦克风信号里减掉扬声器播放内容的回声 | **必须**有 far-end 参考（`ProcessReverseStream`）和准确的回放到采集延迟（`set_stream_delay_ms`） | `echo_canceller.enabled`；`mobile_mode=false` 用 AEC3，`true` 用 AECM；默认强制开高通（`audio_processing.h:206-213`） |
| NS | 压平稳背景噪声（风扇、空调、底噪） | 只要麦克风信号 | `noise_suppression.level` 四档，默认 `kModerate`；可选分析 AEC 线性输出（`:216-221`） |
| AGC1 | 把电平拉到目标范围，可给出建议的模拟麦克风音量 | 麦克风信号；模拟模式还要 `set_stream_analog_level` 回传当前硬件音量 | `mode` = `kAdaptiveAnalog`（默认）/ `kAdaptiveDigital` / `kFixedDigital`；`target_level_dbfs=3`、`compression_gain_db=9`（`:235-270`） |
| AGC2 | 新一代数字 AGC，带限幅器，用 RNN VAD 只在有人声时调增益 | 麦克风信号 | `adaptive_digital`：`headroom_db=5`、`max_gain_db=50`、`initial_gain_db=15`、`max_gain_change_db_per_second=6`（`:361-371`） |
| 高通 | 去直流和低频隆隆声 | 麦克风信号 | `high_pass_filter.enabled` |

`set_stream_delay_ms` 的定义：`delay = (t_render − t_analyze) + (t_process − t_capture)`，即"送进 reverse 到真正从喇叭放出来"加上"麦克风采到到送进 ProcessStream"（`audio_processing.h:610-620`）。这个值估不准，AEC 收敛就慢或者残余回声大。

## 在 voice agent 链路里放在哪

| 组件 | 端侧 | 服务端 | 理由 |
|---|---|---|---|
| AEC | **应该在这里** | 基本做不了 | AEC 需要扬声器参考信号和本机的播放 / 采集时钟。服务端拿不到真正的播放时刻，网络抖动和设备缓冲让延迟不可估 |
| NS | 可以 | 可以 | 只依赖麦克风信号 |
| AGC | 优先端侧 | 可以 | 端侧能调模拟增益、避免 ADC 削波；服务端只能做数字增益 |

几个框架里的实际用法（都是 libwebrtc 的 APM，不一定是这个打包）：

- LiveKit 的 Python SDK 暴露了 `rtc.AudioProcessingModule`。livekit-agents 只在本地 console 模式（本机麦克风 + 本机扬声器）同时打开 AEC、NS、高通、AGC，把扬声器输出按 10 ms 喂给 `process_reverse_stream`，并用输入输出缓冲的实际延迟调 `set_stream_delay_ms`（`livekit-agents/livekit-agents/livekit/agents/cli/_legacy.py:350-355`、`:698`、`:782`）。服务端 room 输入只开 AGC（`livekit-agents/.../voice/room_io/_input.py:364-366`）。这正好对应上表：AEC 只在"扬声器所在的机器"上做。
- 浏览器和 libwebrtc 客户端 SDK 默认在端侧做这些处理，所以 WebRTC 链路的服务端通常拿到的已经是处理过的音频。
- MCU 设备（如 [xiaozhi-esp32](../devices/xiaozhi-esp32.md)）用的是芯片厂商的方案（乐鑫 ESP-SR AFE），不是这个库。

## 接口形态

C++ 类接口，没有 C API，也没有官方 Python 绑定。最小用法（摘自 `examples/run-offline.cpp`）：

```cpp
auto apm = webrtc::AudioProcessingBuilder().Create();
webrtc::AudioProcessing::Config config;
config.echo_canceller.enabled = true;
config.gain_controller1.enabled = true;
config.gain_controller2.enabled = true;
config.high_pass_filter.enabled = true;
apm->ApplyConfig(config);
webrtc::StreamConfig sc(48000, 1);
// 每 10 ms：
apm->ProcessReverseStream(play_frame, sc, sc, play_frame);
apm->ProcessStream(rec_frame, sc, sc, rec_frame);   // 原地写回
```

构建依赖 abseil-cpp（≥ 20240722），产物是 `webrtc-audio-processing-2` 库和 pkg-config 文件（`meson.build:22`、`:48-60`、`:197-199`）。

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：不涉及。内部的 AGC2 RNN VAD 和 `common_audio/vad` 的 GMM VAD 都不通过 APM 的公共配置暴露为判停信号；头文件注释里的 `stream_has_voice()` 在当前声明里已经不存在（`audio_processing.h:121`）。
- [打断与截断](../../03-mechanisms/interruption.md)：间接相关。开放麦克风下能否"边播边听、说话即打断"取决于端侧 AEC 的质量；AEC 收敛前的残余回声会造成误打断。APM 本身不做打断逻辑。
- [首音优化](../../03-mechanisms/first-audio.md)：不涉及。按 10 ms 块处理，不引入可观的额外缓冲（具体算法延迟待确认）。
- [工具回合](../../03-mechanisms/tool-calls.md)：不涉及。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：不涉及。AEC 有收敛状态，新通话时头文件建议调 `Initialize()`（`audio_processing.h:124-125`）。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：核心功能。AEC（`modules/audio_processing/aec3/`）、NS（`ns/noise_suppressor.cc`）、AGC（`agc/`、`agc2/`）一站式，入口 `AudioProcessing::ProcessStream` / `ProcessReverseStream`。AEC 必须放在端侧。
- [评测](../../03-mechanisms/evaluation.md)：仓库里没有评测脚本（上游的单元测试在拆包时被去掉了，`UPDATING.md`）。`examples/run-offline.cpp` 可以拿来对录好的播放 / 采集文件做离线 A/B。

## 取舍与局限

- **AEC 的效果高度依赖延迟估计和参考信号质量**。参考信号必须是真正送到喇叭的那一路（含音量），延迟要准；喇叭和麦克风之间的非线性失真（小喇叭、外壳共振）会让残余回声变大。
- **服务端用不上 AEC**：voice agent 场景下，服务端只能靠播放状态标志、打断保护窗口等手段弥补（见 [打断与截断](../../03-mechanisms/interruption.md)）。
- **NS 是传统方法**：对平稳噪声有效，对人声类噪声、突发噪声效果有限；比 RNNoise 等神经网络方法弱，但音乐噪声更少（通识，未在本仓库验证）。
- **C++17 + abseil 依赖**，嵌入式 MCU 上难以直接使用；没有官方 Python 绑定，Python 侧通常借助 LiveKit SDK 或第三方封装。
- 跟随上游版本（当前 M131），API 会随上游有破坏性变化（2.0 的 NEWS 列了一批删除项）。

## 相关

- 机制页：[音频前处理](../../03-mechanisms/audio-preprocessing.md)、[打断与截断](../../03-mechanisms/interruption.md)
- 项目页：[rnnoise](rnnoise.md)、[aiortc](aiortc.md)、[livekit-agents](../frameworks/livekit-agents.md)、[xiaozhi-esp32](../devices/xiaozhi-esp32.md)
- 对比页：待补
