# 音频前处理：降噪、AEC、AGC（audio preprocessing）

手册：03-mechanisms/audio-preprocessing.md

## 问题

麦克风信号里除了用户声音，还有环境噪声、agent 自己被收回的回声、忽大忽小的电平，要在进入 VAD、判停、ASR 或 S2S 上游前处理，并转成下游要求的采样率。后果主要落在回合控制：回声造成误打断（全双工下回声被当用户输入），噪声造成 VAD 误触发和句中切段，小声用户（儿童、远场）达不到 VAD 或音量门槛。处理过头也有代价：降噪伪影让在原始音频上训练的 ASR / S2S 变差，每级加延迟，激进降噪压掉弱辅音和儿童语音。

## 解法分类

**降噪（NS）**
- 传统 DSP：WebRTC APM NS，四档最多衰减 6 / 12 / 18 / 约 21 dB，默认 `kModerate`；有增益下限，对小声语音更温和（推断）。延迟约 6 ms（推算）。对风噪、人声干扰效果有限。
- DSP + 小 RNN：RNNoise，48 kHz / 10 ms 帧，约 20 ms（推断），只能开关、没有增益下限，噪声时有"呼吸感"；训练数据无中文和儿童，对人声干扰基本不压；Pipecat 包装丢掉了它的语音概率。
- 神经增强：GTCRN / DPDFNet（sherpa-onnx `online-speech-denoiser.h`，有流式），无评测数据，需实测。
- 商业 SDK：Krisp VIVA、Koala、ai-coustics（Pipecat `audio_in_filter`、LiveKit `noise_cancellation`），要授权，无中文 / 儿童评测；Koala 要求 transport 采样率等于模型采样率。Krisp 有"等机器人语音播完再启动降噪"的模式，防止把回声学成噪声后压掉真人语音。
- 交给 S2S 上游（OpenAI Realtime `noise_reduction`）：效果无数据。
- 不处理靠模型抗噪：Ultravox 训练用 MUSAN 噪声，纯噪声输出 `((noise))`；Moshi、Qwen3-Omni、Step-Audio 2 推理侧不做前处理。

**回声消除（AEC）** 必须放在同时拿得到扬声器参考和麦克风信号的机器上，一般是端侧。
- 芯片 / 端侧 SDK：ESP-SR AFE（xiaozhi `AEC_MODE_FD_LOW_COST`），只在 S3 / P4 等带 PSRAM 芯片、白名单板型，需硬件回采通道。
- WebRTC APM AEC3：每 10 ms 交替喂 render 和 capture，自估延迟；累计 2.5 s 有效 render 后才离开初始状态。延迟约 4 ms（推算）+ 10 ms 帧缓冲。ARM Linux 可行（调研笔记估 3–5 人日打通、1–2 周调 ERLE）；RTOS / MCU 移植成本高。失败条件：播放期间麦克风没常开、render 不是进 DAC 前最后一份 PCM、ADC / DAC 时钟不同源、蓝牙播放缓冲变化。
- 浏览器 `echoCancellation`：底层同为 WebRTC。
- 服务端近似（xiaozhi MQTT 网关，标 Unstable）：每轮冷启动、延迟混网络抖动、回声经过 Opus 非线性失真。不建议当主方案。
- 服务端补偿（不是 AEC）：AEC 预热期屏蔽打断（LiveKit `aec_warmup_duration` 默认 3.0 s，外呼 SIP 为 None；unmute 开头 3 s）+ 打断门槛。

**AGC**
- 端侧硬件增益 + 固定数字增益：APM AGC1 `kFixedDigital`（头文件推荐嵌入式）或 AGC2 `fixed_digital.gain_db`。
- 自适应：APM AGC2，输出噪声 ≤ −50 dBFS 限制增益、每秒最多升 6 dB、RNN VAD 概率 ≥ 0.95 才更新。代价：户外底噪 −40 dBFS 时允许增益为 0 dB，正好在最需要放大小声孩子时不起作用；1–3 s 的按键追不上。
- 服务端：LiveKit room_io `rtc.AudioProcessingModule(auto_gain_control=True)`；浏览器 `autoGainControl`。

**重采样位置**：解码 → 降噪（RNNoise 固定 48 kHz）→ 一次降到 16 kHz → VAD / ASR（Silero 8 / 16 kHz，TEN VAD 只收 16 kHz）。Opus 能直接解到 16 kHz 就别先解 48 kHz。照搬 LiveKit Silero 插件：判别支路（降采样 + 可选降噪 → VAD）和主路（原始采样率 → STT / 上游）分开。采样率不对 VAD 结果不可信且不报错；块边界爆音、流结束不 flush 吞尾音。

**依赖关系**
- AEC → 打断：开放麦能否说话即打断取决于端侧 AEC；xiaozhi 只有 AEC 开时才默认 realtime 模式。按键说话时回声不造成误打断，风险变成回声混进上行。
- NS → VAD：降噪后 VAD 不一定更准，要 A/B。
- AGC → VAD 门槛：Pipecat VAD 默认 `min_volume=0.6`，用绝对音量门槛就必须先 AGC，或者不设门槛。
- 电话线路 8 kHz PCMU / PCMA：前处理和评测要在 8 kHz 上再做一遍。
- 儿童户外：基频 250–400 Hz、声小、停顿长；风噪削波发生在 ADC，服务端救不回，只能靠防风罩、开孔位置、端侧 100–150 Hz 高通。

默认开了什么：开源框架服务端都不做 AEC，默认不开降噪（LiveKit room_io 默认开 AGC，没直接配降噪时）；AEC 一律交给浏览器或设备，服务端只用"开头几秒不许打断"兜底。xiaozhi 设备默认 AEC / NS / AGC 全关，AEC 关时播放期间关麦。APM 库所有子模块 create 时都关，要显式 `ApplyConfig`。

## 推荐

| 场景 | 做法 |
|---|---|
| 浏览器 / WebRTC 客户端 | 打开 `echoCancellation`、`noiseSuppression`、`autoGainControl`，服务端不再叠降噪；服务端设 AEC 预热约 3 s + 打断门槛（最短时长或最少词数） |
| 自有硬件，开放麦或全双工 | 必须端侧 AEC。芯片有就用芯片的；ARM Linux 没有就移植 APM AEC3，满足三条：播放期间麦克风和 APM 一直跑；APM 实例跨轮保持，不每轮 `Initialize()`；render 取进 DAC 前最后一份 PCM，音量变化通知 APM。做不到就退回半双工或按键，不要指望服务端 AEC |
| 全双工模型（Moshi、unmute） | AEC 必须常开，且在第一句播放前开始收敛；预热窗口只挡打断，挡不住回声进模型。没有 AEC 的客户端（命令行、MLX）要求戴耳机 |
| 自有硬件，按键说话 | AEC 仍建议做，优先级低于开放麦。没有 AEC 时按键瞬间本地停播，把上行开头 N ms 标记为"可能含回声尾巴"，只标记不丢弃；N 起点 150–300 ms（调研笔记建议值），按实测收敛时间和混响定 |

降噪：**默认只放判别支路**（VAD、判停前），送 ASR / S2S 的主路不降噪，除非在目标人群录音上 A/B 证明有益。
- 主路要降噪先选有增益下限的：APM NS `kLow`（6 dB）或 Krisp 50–75（LiveKit 默认 75），不要直接用 100（Pipecat 默认）。
- RNNoise 只能开关、无增益下限，不直接压儿童主路。
- 只用 S2S 上游时，噪声主要危害是服务端 VAD 误切段：在上游前加自己的 VAD 和判别支路降噪，比主路降噪有效。

AGC：端侧硬件增益 + 固定数字增益打底，自适应只做小范围修正。VAD 不设绝对音量门槛。

顺序：设备端"麦克风 → AEC → 高通 100–150 Hz → （可选）NS → AGC → Opus 16 kHz / 20 ms"；服务端"Opus 解码到 16 kHz（跑 RNNoise 时解 48 kHz → RNNoise → 降 16 kHz）→ 判别支路 [降噪] → VAD → 判停 / 打断 / 停顿压缩 / 短按过滤；主路原始 16 kHz → ASR / S2S"。APM 内部顺序可直接参考：高通 → AEC3 → NS → AGC（先去回声再估噪声，增益放最后）。

服务端拿不到 AEC 时：维护"正在播放"标志和播放位置（来自设备回执）；会话开头 AEC 预热窗口，播放期间提高打断门槛（或 LiveKit `mode="adaptive"`）；想给"可能含回声"打标记可试 APM `ResidualEchoDetector`（平滑系数 0.001 收敛很慢，能否按轮判定需实测）。

上线前必须实测（开源仓库都没数字）：
1. 端侧 AEC 收敛时间和残余回声电平 → 决定预热窗口和回声尾巴标记长度。
2. 各降噪方案在目标人群录音上的识别率、误切段率、首音变化，分"只进判别支路"和"也进主路"两组。
3. RNNoise、GTCRN 每路 CPU 和实际算法延迟。
4. AGC 固定增益取值，自适应开关对比。
5. 风噪：防风罩 × 高通截止 × 降噪开关，统计削波帧比例和识别率。

儿童、户外场景：以上每项在真实录音上 A/B，不用开源默认值直接上线。

## 各项目怎么做

- xiaozhi-esp32：AEC 三选一（关 / ESP-SR AFE / 服务端），编译期决定，部分板型运行时 `SetAecMode`；默认全关。`main/audio/engines/afe_audio_engine.cc`、`application.cc:26-34`。
- xiaozhi-esp32-server：不降噪不 AGC；MQTT 网关实验性服务端 AEC（互相关延迟估计 + 维纳滤波 + 谱减），设备需编译 `USE_SERVER_AEC`。`connection.py:_apply_aec`。
- livekit-agents：`noise_cancellation` 挂在 VAD 前；room_io 用 APM 做 AGC；只有 console 模式开 APM AEC；Silero 只在推理支路降到 16 kHz。`voice/room_io/_input.py:_ParticipantAudioInputStream`、`room_io.py:131-136`、`cli/_legacy.py`。
- pipecat：`audio_in_filter` 在 VAD 前，可选 RNNoise / Koala / Krisp VIVA / ai-coustics，默认不开；未找到 AEC。`transports/base_input.py:BaseInputTransport`、`audio/filters/`、`rnnoise_filter.py:RNNoiseFilter`、`krisp_viva_filter.py`。
- webrtc-audio-processing：AEC3、NS、AGC1 / AGC2、高通一站式，全部要显式 `ApplyConfig`。`AudioProcessing::ProcessStream` / `ProcessReverseStream`、`audio_processing_impl.cc:ProcessCaptureStreamLocked`。
- unmute：服务端只解 Opus，依赖浏览器约束；开头 3 s 屏蔽声学打断。`frontend/src/app/useAudioProcessor.ts`、`UNINTERRUPTIBLE_BY_VAD_TIME_SEC`。
- sherpa-onnx：流式增强（GTCRN / DPDFNet）和重采样，无 AEC。`online-speech-denoiser.h`。
- openai-realtime-agents：浏览器默认约束；可切 PCMU / PCMA 8 kHz 模拟电话。`lib/codecUtils.ts:applyCodecPreferences`。

## 相关

- 架构：02-architectures/full-duplex.md（AEC 何时必须常开）、02-architectures/s2s.md（4.1 节服务端 VAD 关不掉时在上游前加 VAD）、02-architectures/cascade.md、02-architectures/floor-control.md（播放中标志）
- 基础：01-foundations/audio-basics.md、01-foundations/vad.md、01-foundations/transport.md
- 机制：03-mechanisms/interruption.md、03-mechanisms/turn-detection.md、03-mechanisms/evaluation.md（噪声注入、按 SNR 分层）、03-mechanisms/session-recovery.md
