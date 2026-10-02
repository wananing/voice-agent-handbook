# 音频前处理：降噪、AEC、AGC

> 状态：草稿
> 最后更新：2026-10-02

## 问题是什么

麦克风采到的信号里，除了用户的声音，还有三样东西：环境噪声，agent 自己从扬声器放出来又被麦克风收回去的回声，以及忽大忽小的电平。前处理要在信号进入 VAD、判停、ASR 或 S2S 上游之前处理这三样，同时把采样率转成下游要求的格式。

不处理的后果主要落在回合控制上，而不是识别率上：

- **回声**：开放麦克风下，agent 的声音被 VAD 判成用户开口，造成误打断。全双工模型一直在听，回声会被当成用户输入。
- **噪声**：VAD 误触发、句中切段，噪声被当成一轮输入送给模型。
- **电平**：小声用户（儿童、远场）达不到 VAD 或音量门槛，判停和识别一起变差。

处理过头也有代价：降噪伪影会让在原始音频上训练的 ASR 或 S2S 变差，每一级处理都增加延迟，激进的降噪会把弱辅音和小声的儿童语音一起压掉。

采样率、帧长、重采样代价的基础知识见 [audio-basics](../01-foundations/audio-basics.md)，VAD 本身见 [vad](../01-foundations/vad.md)，本页不重复。

## 解法分类

**降噪（NS）**

1. **传统 DSP**：维纳滤波加噪声估计，有增益下限。代表是 WebRTC APM 的 NS，四档对应最多衰减 6 / 12 / 18 / 约 21 dB，默认 `kModerate`。
2. **DSP + 小型 RNN**：RNNoise，按频带估增益，48 kHz、10 ms 帧，没有增益下限。
3. **神经网络语音增强**：GTCRN / DPDFNet（sherpa-onnx 的 `online-speech-denoiser.h`，有流式版本）；ZipEnhancer（VoxCPM 只拿它处理参考音频，不处理用户上行）。
4. **商业 SDK**：Krisp VIVA、Picovoice Koala、ai-coustics，接在 Pipecat `audio_in_filter` 或 LiveKit `noise_cancellation` 上，都要授权。
5. **交给上游**：S2S 上游可能自带降噪选项。openai-realtime-agents 的项目页提到它没有配置服务端 `noise_reduction`，说明 OpenAI Realtime 有这一项。效果和延迟，开源仓库里都没有数据，待确认。
6. **不处理，靠模型抗噪**：Ultravox 训练时用 MUSAN 噪声样本教模型对纯噪声输出 `((noise))`；Moshi、Qwen3-Omni、Step-Audio 2 推理侧都不做前处理。

**回声消除（AEC）**

AEC 要用"扬声器正在放什么"（参考信号，render / far-end）从麦克风信号里减掉回声，所以**只能放在同时拿得到扬声器参考和麦克风信号的那台机器上**，一般就是端侧。

1. **芯片 / 端侧 SDK**：乐鑫 ESP-SR AFE（xiaozhi-esp32 用 `AEC_MODE_FD_LOW_COST`），需要硬件回采参考通道和声学隔离。
2. **WebRTC APM（AEC3）**：每 10 ms 交替喂 render 和 capture，自己估延迟（搜索窗约 0–512 ms，推算）。累计 2.5 s 有效 render 后才离开初始状态。LiveKit 只在本地 console 模式用它，因为只有这时能拿到本机扬声器输出。
3. **浏览器自带**：`getUserMedia` 的 `echoCancellation` 约束，底层同样是 WebRTC。
4. **服务端近似**：设备把正在播放的下行帧时间戳塞进上行，服务端用下行音频作参考做对齐和抵消。xiaozhi 的 MQTT 网关路径有一个实验性实现（标 Unstable）。一份调研笔记的分析是：上行只在按键时才有，AEC 每轮冷启动；延迟里混着网络抖动；回声还经过了 Opus 编解码。所以效果和性价比都差，不建议当主方案。
5. **服务端补偿（不是 AEC）**：AEC 收敛前屏蔽打断（LiveKit `aec_warmup_duration` 默认 3.0 s，unmute 开头 3 s），加打断门槛（最短时长、最少词数）。

**自动增益（AGC）**

1. **端侧硬件增益 + 固定数字增益**：APM AGC1 的 `kFixedDigital`（头文件推荐嵌入式设备用这一档），或 AGC2 的 `fixed_digital.gain_db`。
2. **自适应数字增益**：APM AGC2。增益被"输出噪声 ≤ −50 dBFS"限住，每秒最多升 6 dB，RNN VAD 概率 ≥ 0.95 才更新语音电平估计。
3. **服务端 AGC**：LiveKit room_io 用 `rtc.AudioProcessingModule(auto_gain_control=True)`。
4. **浏览器**：`autoGainControl` 约束。

**重采样放在哪一环**

降噪要在它自己的采样率上跑（RNNoise 固定 48 kHz），VAD 只吃 8 / 16 kHz（Silero 8 / 16 kHz，TEN VAD 16 kHz），所以顺序是：**解码 → 降噪 → 一次降到 16 kHz → VAD / ASR**。Opus 能直接解码到 16 kHz 时，就不要先解到 48 kHz 再降。值得照搬的是 LiveKit Silero 插件的做法：**判别支路和主路分开**，VAD 看降采样（可以再加降噪）后的信号，送 STT / 上游的主路保留原始采样率。

## 各解法的代价

| 环节 | 延迟 | CPU / 部署 | 主要失败模式 |
|---|---|---|---|
| APM NS | 约 6 ms（推算，256 点 FFT 重叠 96 点） | 低，C++，ARM Linux 可交叉编译 | 对风噪、人声干扰这类非平稳噪声效果有限（通识，未实测） |
| RNNoise | 约 20 ms（按代码推断），Pipecat 包装后还要加 soxr 重采样和攒 480 样本 | 低，有 NEON；实际 CPU 占用仓库无数字 | 没有增益下限，噪声一阵一阵时有"呼吸感"；训练数据没有中文和儿童；对别人说话基本不压；Pipecat 包装丢掉了它返回的语音概率 |
| GTCRN / DPDFNet | 待确认 | ONNX，sherpa-onnx 可上端侧 | 仓库无评测数据，待确认 |
| Krisp / Koala / ai-coustics | 待确认（ai-coustics 启动时打印模型延迟） | 商业授权；Koala 要求 transport 采样率等于模型采样率，否则直接禁用 | 无公开的中文、儿童评测；Krisp 自己有"等机器人语音播完再启动降噪"的模式，防止压掉之后的真人语音 |
| AEC3（端侧） | 约 4 ms（推算），加 10 ms 帧缓冲 | ARM Linux 可行（一份调研笔记估 3–5 人日打通、1–2 周调 ERLE）；RTOS / MCU 移植成本高 | 播放期间麦克风必须常开，否则不收敛；render 必须是进 DAC 前的最后一份 PCM；ADC 和 DAC 时钟不同源、播放缓冲大小变化（蓝牙）时变差 |
| ESP-SR AFE | 待确认 | 只在 S3 / P4 等带 PSRAM 的芯片上，且只对白名单板型开放 | 没有回采通道的板子开不了；关掉时只能半双工 |
| 服务端软件参考 AEC | 依赖对齐 | 每路一个实例 | 每轮冷启动；延迟不固定；Opus 非线性失真 |
| AGC2 自适应 | 0（逐帧增益） | 低 | 户外底噪 −40 dBFS 时允许增益为 0 dB，恰好在最需要放大小声孩子时不起作用；一次 1–3 s 的按键追不上增益 |
| 重采样 | 几 ms（取决于滤波器，待确认） | 高质量档不便宜 | 块边界爆音、流结束不 flush 吞尾音 |

**前处理和 VAD / 判停 / 打断的依赖**

- **AEC → 打断**：开放麦克风下能否"边播边听、说话即打断"，取决于端侧 AEC 的质量。AEC 收敛前的残余回声会被 VAD 判成用户开口，造成误打断。所以 LiveKit、unmute 都在会话开头屏蔽一段声学打断；xiaozhi 只有 AEC 开着时才默认用 realtime（说话即打断）模式，否则用按键或 auto 模式。按键说话时，回声不会造成误打断（按键本身就是打断意图），风险变成回声混进上行：用户在设备播放时按键，上游会听到设备自己的声音。
- **NS → VAD**：降噪后 VAD 不一定更准，要 A/B 测。RNNoise 的语音概率理论上可以当 VAD 用，但没有阈值、平滑和状态机，框架也没用它。
- **AGC → VAD 门槛**：Pipecat VAD 默认 `min_volume=0.6`，判定条件是置信度和音量都达标，这对小声用户不友好。用了绝对音量门槛，就必须先做 AGC，或者干脆不设门槛。
- **重采样 → VAD**：采样率不对，VAD 结果不可信，而且不报错。
- **降噪 → 打断之后的识别**：降噪器如果在 agent 说话期间学到了"回声也是噪声"，之后可能把用户的真人语音一起压掉。Krisp VIVA 在 Pipecat 里有一个 TTS 检测模式，要等机器人语音播完再启动降噪，就是为了防这个（`krisp_viva_filter.py`）。
- **编码 → 一切下游**：电话线路是 8 kHz PCMU / PCMA。openai-realtime-agents 允许切到这两种编码，用来模拟电话线路下的 ASR 和 VAD 表现。产品要走电话时，前处理和评测都要在 8 kHz 上再做一遍。
- **全双工**：模型一直在听，AEC 比级联更关键。Moshi README 推荐用 Web UI，理由是浏览器的回声消除"helps the overall model quality"。

**儿童和噪声场景**：基频高（250–400 Hz）、声音小、句中停顿长，户外有风噪和别的孩子说话。RNNoise 的频带增益分不开和孩子基频重叠的噪声，也压不了人声干扰；APM NS 有增益下限，对小声语音更温和（推断）；风噪导致的削波发生在 ADC，服务端救不回来，只能靠防风罩、开孔位置和端侧 100–150 Hz 高通（APM 高通固定 100 Hz）。smart-turn 的数据规范要求"尽量少背景噪音"，训练里没有加噪，嘈杂环境下的判停鲁棒性待确认。

## 各项目怎么做

| 项目 | 做法 | 代码路径 | 备注（默认开了什么） |
|---|---|---|---|
| [xiaozhi-esp32](../04-projects/devices/xiaozhi-esp32.md) | AEC 三选一（关 / 设备端 ESP-SR AFE / 服务端），编译期决定；AFE 内 VADNet 只刷 LED；小芯片走 `LiteAudioEngine` 上行原始 PCM | `main/audio/engines/afe_audio_engine.cc`、`application.cc:26-34` | 默认 AEC 关、NS 关（不带 NSNet 模型）、AGC 关；部分板型可运行时 `SetAecMode` 切换 |
| [xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md) | 不做降噪和 AGC；MQTT 网关路径有实验性服务端 AEC（互相关延迟估计 + 维纳滤波 + 谱减） | `connection.py:_apply_aec` | 设备需编译 `USE_SERVER_AEC`；设备声明 `features.aec` 时 VAD 一判到有声就打断 |
| [livekit-agents](../04-projects/frameworks/livekit-agents.md) | `noise_cancellation` 帧处理器挂在 VAD 之前；room_io 可用 APM 做 AGC；服务端不做 AEC，只有 console 模式开 APM 回声消除；房间模式靠客户端 WebRTC，服务端用 `aec_warmup_duration` 屏蔽打断 | `voice/room_io/_input.py:_ParticipantAudioInputStream`、`room_io.py:131-136`、`cli/_legacy.py` | 降噪默认不开（Krisp 插件商业授权）；AGC 未显式配置且没有直接配降噪时默认开（源码 `room_io.py`）；`aec_warmup_duration` 默认 3.0 s，外呼 SIP 为 None；Silero 只在推理支路降到 16 kHz |
| [pipecat](../04-projects/frameworks/pipecat.md) | 输入 transport 的 `audio_in_filter` 在 VAD 之前，可选 RNNoise、Koala、Krisp VIVA、ai-coustics；VAD 另有 Krisp VIVA、ai-coustics Quail | `transports/base_input.py:BaseInputTransport`、`audio/filters/` | 默认不开任何 filter；代码中未找到 AEC，推断交给客户端或传输服务，待确认；VAD 默认 `min_volume=0.6` |
| [rnnoise](../04-projects/audio-processing/rnnoise.md) | 只做降噪，48 kHz / 10 ms 帧 | `src/denoise.c:rnnoise_process_frame`；Pipecat 接入 `rnnoise_filter.py:RNNoiseFilter` | 只能开关，不能调强度；自带高通只去直流（约 15 Hz） |
| [webrtc-audio-processing](../04-projects/audio-processing/webrtc-audio-processing.md) | AEC3、NS、AGC1 / AGC2、高通一站式 | `AudioProcessing::ProcessStream` / `ProcessReverseStream`、`modules/audio_processing/aec3/` | 所有子模块 create 时都关，要显式 `ApplyConfig`；处理顺序是高通 → AEC → NS → AGC |
| [sherpa-onnx](../04-projects/asr/sherpa-onnx.md) | 流式语音增强（GTCRN / DPDFNet）和重采样 | `online-speech-denoiser.h` | 没有 AEC；增强需要上层显式接入 |
| [aiortc](../04-projects/audio-processing/aiortc.md) | 不处理音频内容 | — | 前处理依赖浏览器 `getUserMedia` 约束或服务端另接降噪；音频没有 NACK / PLC |
| [openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md) | `getUserMedia({ audio: true })`，回声消除、降噪用浏览器默认；可选 Opus 48 kHz 或 PCMU / PCMA 8 kHz 模拟电话线路 | SDK `openaiRealtimeWebRtc.mjs`、`lib/codecUtils.ts:applyCodecPreferences` | 没有配置服务端 `noise_reduction` |
| [qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md) | 服务端不做前处理；Web 客户端三个约束全开 | `web/src/realtime/useRealtimeVoice.js` | 桌面端、移动端怎么处理待确认 |
| [unmute](../04-projects/full-duplex/unmute.md) | 服务端只做 Opus 解码，依赖浏览器 `echoCancellation` 等约束；开头 3 s 屏蔽声学打断 | `frontend/src/app/useAudioProcessor.ts`、`UNINTERRUPTIBLE_BY_VAD_TIME_SEC` | — |
| [moshi](../04-projects/full-duplex/moshi.md) | 服务端不做前处理，Mimi 直接编码原始 PCM；Web UI 开 `echoCancellation` / `noiseSuppression` | `UserAudio.tsx` | 命令行客户端和 MLX 本地版没有 AEC |
| [ten-framework](../04-projects/frameworks/ten-framework.md) | 服务端扩展没有降噪或 AEC，依赖 Agora RTC 一侧 | `ext/ten_vad_python/` | Agora 是否开 AEC / ANS 待确认 |
| [ten-vad](../04-projects/turn-vad/ten-vad.md) | 只有预加重和 STFT，作为 VAD 放在降噪和重采样之后 | `src/aed.cc` | 只收 16 kHz |
| [funasr](../04-projects/asr/funasr.md) | 只做重采样和特征提取，VAD 前不做增强 | `audio.cpp:Audio::WavResample` | 没有 AEC、降噪、AGC |
| [sensevoice](../04-projects/asr/sensevoice.md) | 只做重采样 / 解码和 fbank（预加重 0.97） | `sensevoice-server.cpp:67` | 事件标签（Laughter / Cry / BGM）只分类不抑制 |
| [fireredasr](../04-projects/asr/fireredasr.md) | 只做 fbank + CMVN，不做重采样 | `asr_feat.py:ASRFeatExtractor` | README 要求先用 ffmpeg 转 16 kHz |
| [ultravox](../04-projects/e2e-models/ultravox.md) | 推理侧不处理；训练侧数据增强，纯噪声输出 `((noise))` | `ultravox/data/aug/` | 可当误触发过滤，抗噪程度无对比数据，待确认 |
| [step-audio2](../04-projects/e2e-models/step-audio2.md) | 只重采样到 16 kHz 和切块 | `utils.py:load_audio` | 没有降噪或 AEC |
| [qwen3-omni](../04-projects/e2e-models/qwen3-omni.md) | 音频经 `process_mm_info` 读入后直接进模型 | — | 没有降噪或 AEC |
| [voxcpm](../04-projects/tts/voxcpm.md) | 只对参考音频可选 ZipEnhancer 降噪、VAD 静音裁剪 | `core.py`（`denoise=True`）、`voxcpm2.py:_trim_audio_silence_vad` | 不处理用户上行 |
| [smart-turn](../04-projects/turn-vad/smart-turn.md) | 不做前处理；训练里没有找到加噪或增强 | `docs/data_generation_contribution_guide.md:43` | 噪声下的鲁棒性待确认 |

不涉及：[pipecat-flows](../04-projects/frameworks/pipecat-flows.md)、TTS 项目（[cosyvoice](../04-projects/tts/cosyvoice.md)、[fireredtts2](../04-projects/tts/fireredtts2.md)、[fish-speech](../04-projects/tts/fish-speech.md)、[index-tts](../04-projects/tts/index-tts.md)、[spark-tts](../04-projects/tts/spark-tts.md) 只对参考音频做截断和重采样）。

**各框架和端默认开了什么**：

| 位置 | AEC | NS | AGC | 打断兜底 |
|---|---|---|---|---|
| LiveKit 服务端（room_io） | 不做 | 不开 | 开（没直接配降噪时） | `aec_warmup_duration` 3.0 s、`min_duration` 0.5 s |
| Pipecat 服务端 | 不做 | 不开 | 不开 | 可选 `MinWordsUserTurnStartStrategy` |
| unmute 服务端 | 不做 | 不开 | 不开 | 开头 3 s 不许声学打断 |
| 浏览器客户端（qwen-audio-agent、unmute、Moshi Web UI、openai-realtime-agents） | 开 | 开 | qwen-audio-agent 显式开；其他用浏览器默认 | — |
| xiaozhi-esp32 设备 | 关（可编译为设备端或服务端） | 关 | 关 | AEC 关时播放期间关麦 |
| APM（库本身） | 关 | 关 | 关 | 全部要显式 `ApplyConfig` |

**汇总**：开源框架的服务端都不做 AEC，默认也不开降噪（LiveKit 默认开的是 AGC）。AEC 一律交给浏览器或设备，服务端只用"开头几秒不许打断"来兜底。浏览器 `getUserMedia({ audio: true })` 不写约束时各浏览器默认开哪几项，项目页没有记录，待确认。

## 我们的判断

**浏览器 / WebRTC 客户端**：打开 `getUserMedia` 的 `echoCancellation`、`noiseSuppression`、`autoGainControl` 三个约束，服务端不再叠降噪。服务端设 AEC 预热窗口（3 s 左右，LiveKit、unmute 的取值）和打断门槛（最短时长或最少词数）。

**自有硬件，开放麦克风或全双工**：必须做端侧 AEC。芯片有 AEC 就用芯片的（ESP-SR AFE 一类）。ARM Linux 设备没有现成 AEC 时，移植 APM AEC3，要满足三条：

- 播放期间麦克风和 APM 一直在跑；
- APM 实例跨轮保持，不要每轮 `Initialize()`；
- render 取进 DAC 前的最后一份 PCM，音量变化要通知 APM。

做不到端侧 AEC，就退回半双工或按键说话，不要指望服务端 AEC 救场。

**全双工模型（Moshi、unmute 一类）**：模型或 STT 一直在听，没有"播放中忽略打断"这个开关可用（Moshi 根本没有打断事件），所以 AEC 必须常开，而且要在第一句播放之前就开始收敛。预热窗口只能挡住打断，挡不住回声进入模型输入。

只给命令行或 MLX 本地版这种没有 AEC 的客户端时，要求用户戴耳机。

**自有硬件，按键说话**：AEC 仍然建议做，但优先级低于开放麦，因为它只影响"回声混进上行"，不会造成误打断。没有 AEC 时，按键瞬间本地停播，并把上行开头 N ms 标记为"可能含回声尾巴"。只标记不丢弃，N 按实测的收敛时间和房间混响定，一份调研笔记的起点是 150–300 ms。

**降噪**：默认只放在判别支路，也就是 VAD 和判停前面；送 ASR 或 S2S 的主路不降噪，除非在目标人群的录音上 A/B 证明有益。

- 主路要开降噪时，先选有增益下限的：APM NS `kLow`（6 dB），或者 Krisp 50–75（LiveKit 默认 75），不要直接用 100（Pipecat 的默认值）。
- RNNoise 只能开或关，又没有增益下限，不建议直接压儿童语音的主路。
- 只用 S2S 上游时，噪声的主要危害是服务端 VAD 误切段。这时在上游前面加自己的 VAD 和判别支路降噪，比在主路降噪更有效（见 [s2s](../02-architectures/s2s.md) 第 4.1 节）。

**AGC**：小声用户靠端侧硬件增益加固定数字增益打底，自适应 AGC 只做小范围修正。VAD 不设绝对音量门槛。

**顺序**：设备端是"麦克风 → AEC → 高通 → （可选）NS → AGC → 编码"；服务端是"解码 → （48 kHz 降噪）→ 一次降到 16 kHz → VAD"，判别支路和主路分开。一份调研笔记给出的完整链路（自有设备 + WebSocket，参数都要在目标样本上 A/B）：

```
设备端：麦克风（防风罩 / 开孔位置）
        → AEC（芯片或 APM，render 取 DAC 前的 PCM）→ 高通 100–150 Hz
        →（可选）NS → AGC（固定增益打底，不设绝对音量门槛）
        → Opus 16 kHz / 20 ms
服务端：Opus 直接解码到 16 kHz（要跑 RNNoise 时解码到 48 kHz → RNNoise → 降到 16 kHz）
        ├─ 判别支路：[降噪] → VAD（16 kHz）→ 判停、打断、停顿压缩、短按过滤
        └─ 主路：原始 16 kHz PCM → ASR / S2S 上游
```

APM 内部的顺序可以直接参考：高通 → AEC3 → NS → AGC，NS 在 AEC 之后，AGC2 在最后（`audio_processing_impl.cc:ProcessCaptureStreamLocked`，见 [webrtc-audio-processing](../04-projects/audio-processing/webrtc-audio-processing.md)）。自己拼链路时也按这个顺序：先去回声再估噪声，增益放在最后，避免把回声和噪声一起放大。

**服务端拿不到 AEC 时能做什么**：

- 维护"正在播放"标志和播放位置（来自设备回执），给打断判断和上下文截断用；
- 会话开头设 AEC 预热窗口，播放期间提高打断门槛（最短时长、最少词数，或者用 LiveKit `mode="adaptive"` 一类的模型区分真打断和回声、附和）；
- 想给"这一轮可能含回声"打标记，可以试 APM 的 `ResidualEchoDetector`（用下行 render 和上行 capture 算回声似然）。它收敛很慢（平滑系数 0.001），能不能按轮判定待确认。

**上线前要实测的项**（开源仓库都没给数字）：

1. 端侧 AEC 的收敛时间和残余回声电平（边播边说的录音），决定预热窗口和"回声尾巴"标记的长度；
2. 各降噪方案在目标人群录音上的识别率、误切段率和首音变化，分"只进判别支路"和"也进主路"两组；
3. RNNoise、GTCRN 等在服务端的每路 CPU 占用和实际算法延迟；
4. AGC 固定增益取多少（按硬件增益实测），自适应部分开和关的对比；
5. 风噪：有无防风罩 × 高通截止频率 × 降噪开关，统计削波帧比例和识别率。

**儿童、户外噪声场景**：上面每一项都要在目标人群的真实录音上 A/B（分"只用于判别"和"也送上游"两组，看识别率、误切段率和首音），不要用开源仓库的默认值直接上线。几个开源降噪方案都没有中文或儿童的评测数据。

## 相关

- 架构层：[full-duplex](../02-architectures/full-duplex.md)（AEC 什么时候必须常开）、[s2s](../02-architectures/s2s.md)（服务端 VAD 关不掉时在上游前加 VAD）、[cascade](../02-architectures/cascade.md)、[floor-control](../02-architectures/floor-control.md)（播放中标志）
- 其他机制：[interruption](interruption.md)（AEC 未收敛的误打断、打断门槛）、[turn-detection](turn-detection.md)（VAD 前的降噪和重采样）、[evaluation](evaluation.md)（噪声注入、按 SNR 分层）、[session-recovery](session-recovery.md)（前处理状态跨轮保持）
- 基础：[audio-basics](../01-foundations/audio-basics.md)（采样率、重采样代价）、[vad](../01-foundations/vad.md)、[transport](../01-foundations/transport.md)
- 项目页：[webrtc-audio-processing](../04-projects/audio-processing/webrtc-audio-processing.md)、[rnnoise](../04-projects/audio-processing/rnnoise.md)、[aiortc](../04-projects/audio-processing/aiortc.md)、[xiaozhi-esp32](../04-projects/devices/xiaozhi-esp32.md)、[livekit-agents](../04-projects/frameworks/livekit-agents.md)、[pipecat](../04-projects/frameworks/pipecat.md)、[sherpa-onnx](../04-projects/asr/sherpa-onnx.md)
