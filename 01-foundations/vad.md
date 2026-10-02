# VAD：能量、神经网络、端点检测

> 状态：草稿
> 最后更新：2026-10-01

VAD（voice activity detection）回答的是一个很窄的问题：**这一小段音频里有没有人声。** 它输出帧级的概率或 0/1，再由一个小状态机把帧级结果变成"开始说话 / 结束说话"事件。

"用户这一轮说完了没有、agent 该不该接话"是**判停**（turn detection / end-of-turn）的问题，属于机制层，见 [判停](../03-mechanisms/turn-detection.md)。VAD 是判停最常用的输入之一，但两者不是一回事，见 §1.4。

---

## 1. 核心概念

### 1.1 能量 VAD

按帧算能量（或响度），和阈值比较。通常配一个自适应的噪声底估计，阈值跟着底噪浮动。

- 优点：计算量几乎为零，适合放在设备上做第一道门；没有模型，行为可解释。
- 缺点：分不清"人声"和"响的非人声"（风噪、关门、音乐、电视）；对小声说话的人（孩子、离麦远的人）不友好；阈值强依赖环境和麦克风增益。

框架里能量更多是作为**辅助门槛**出现，而不是单独的 VAD。例子：Pipecat 的 VAD 判"在说话"要求**神经网络置信度和音量同时**过阈值（默认 `confidence=0.7`、`min_volume=0.6`），音量用的是 ITU-R BS.1770 响度，要求至少 400 ms 音频。对孩子来说这个音量门槛是风险点（待确认，需样本验证）。

### 1.2 神经网络 VAD

用一个小模型（RNN / 小 CNN）按帧输出语音概率。agent 场景里常见的两个：

| | Silero VAD | TEN VAD |
|---|---|---|
| 采样率 | 8k / 16k | 只收 16k，其他采样率要先重采样 |
| 每次输入 | 16k 下 512 样本（32 ms），8k 下 256 样本；样本数不对会报错 | 16k 下 hop 160 或 256 样本（10 / 16 ms） |
| 结构 / 大小 | 待确认（本手册未精读 Silero 仓库） | 40 维 mel + 1 维基频，3 帧上下文，带状态的小 RNN；ONNX 约 308 KB |
| 输出 | 帧级概率 | 帧级概率和 0/1 标志；**没有 hangover，也没有最短静音**，开始 / 结束状态机要调用方自己写 |
| 自述特点 | — | 句尾检测比 Silero 快几百 ms；更能识别两段语音之间的短静音；基频估计范围约 62 Hz–1.33 kHz，能覆盖儿童音高 |

两者都是声学模型，跟语言关系不大，但**都没有公开的中文或儿童评测**（TEN VAD 的测试集来自 LibriSpeech、GigaSpeech、DNS Challenge）。阈值要在自己的样本上扫 PR 曲线来定，TEN VAD 仓库自带 `plot_pr_curves.py` 可以换成自己的数据。

设备端芯片也常自带 VAD，比如 ESP-SR AFE 里的 VAD（小智固件的配置是 `VAD_MODE_0`、`vad_min_noise_ms=100`），它跟唤醒、AEC 在同一条前处理链里。

### 1.3 端点检测：从帧到事件

帧级概率很抖，直接用会在一句话里开开关关。端点检测在帧级结果上加三样东西：

| 参数 | 作用 | 各框架默认值 |
|---|---|---|
| **起点阈值 / 起说确认**（start） | 连续多久高于阈值才算"开始说话"，防止咳嗽、敲击误触发 | Pipecat `start_secs=0.2`；LiveKit Silero `min_speech_duration=0.05`；TEN VAD 扩展要求最近 120 ms 全部过阈值（参数名叫 `prefix_padding_ms`，实际是确认窗口） |
| **终点阈值 / 静音时长**（end） | 连续多久低于阈值才算"结束说话" | Pipecat `stop_secs=0.2`；LiveKit Silero `min_silence_duration=0.55`（新 `inference.VAD` 降到 0.25）；TEN VAD 扩展 `silence_duration_ms=1000`；小智服务端配置 `min_silence_duration_ms: 200`（代码里的缺省值是 1000）；FunASR 内置 FSMN-VAD 尾部静音 800 ms |
| **双阈值（滞回）** | 进入"说话"用高阈值，退出用低阈值，在临界值附近不抖 | LiveKit Silero 退出阈值默认 = 激活阈值 − 0.15；小智服务端 `threshold=0.5`、`threshold_low=0.3`，中间区间沿用上一帧状态 |

另外两个常配的参数：

- **前缀补偿**（prefix padding）：判定"开始说话"时已经晚了一个确认窗口，要把之前缓存的一段音频一起送出去，否则吞字头。LiveKit Silero `prefix_padding_duration=0.5`；Pipecat smart-turn 往前多带 `pre_speech_ms=500`。
- **最长段**：一段语音超过上限就强制切开。FunASR C++ 默认 15 s、Python 默认 60 s；SenseVoice 的 FSMN-VAD 30 s。

start / end 阈值的取舍直接对着用户体验：

- end 设短（0.2 s）：反应快，但句中停顿会被当成说完；
- end 设长（1 s）：不容易截断，但每一轮都多等这么久，直接加在首音上；
- start 设短：打断灵敏，但噪声、回声、附和语（"嗯"）都可能触发打断。

### 1.4 VAD 与判停的区别

| | VAD | 判停 |
|---|---|---|
| 问题 | 这一帧有没有人声 | 这一轮说完了没有，现在该不该接话 |
| 输入 | 音频帧 | VAD 结果、音频韵律、ASR 文本、对话上下文、按键事件 |
| 输出 | 帧级概率 → 开始 / 结束说话事件 | 回合结束信号 |
| 时间尺度 | 10–32 ms 一帧 | 一轮 |
| 典型做法 | 能量、Silero、TEN VAD | "VAD 静音 N ms"、语义判停模型（smart-turn、LiveKit turn detector、TEN Turn Detection）、按键松开、服务端判停 |

最简单的判停就是"VAD 结束说话 = 回合结束"，所以两者经常被混为一谈。但很多判停策略里 VAD 只是**触发点**：例如 Pipecat 在 VAD 静音 200 ms 时跑一次 smart-turn，判"未说完"就继续等；LiveKit 在 VAD 结束后按语义模型的概率在 `min_delay` 和 `max_delay` 之间选择等待时长。按键说话的场景里，回合结束由松键决定，VAD 退化成"有没有说话"和"压缩停顿"的工具。

VAD 在 agent 里除了判停还有这些用途：

- **打断检测**：agent 说话时检测用户开口（这时要特别提防回声被当成用户声音）；
- **过滤误触和纯噪声**：按键时长和累计语音时长都很短，就不提交；
- **压缩句中停顿**：不让上游服务端 VAD 看到够长的静音而切段；
- **给 ASR 切段**：离线 ASR 按 VAD 段送入。

---

## 2. 对 agent 设计的影响

| 决策 | 跟 VAD 的哪个特性有关 | 去哪看 |
|---|---|---|
| 开放麦克风还是按键说话 | 开放麦克风全靠 VAD + 判停决定回合边界；按键说话只用 VAD 做辅助 | [回合模型](../02-architectures/turn-model.md)、[判停](../03-mechanisms/turn-detection.md) |
| VAD 放在设备还是服务端 | 设备端省流量、能配合 AEC；服务端可以用更大的模型、可以统一调参 | [transport](transport.md)、[音频前处理](../03-mechanisms/audio-preprocessing.md) |
| 上游服务端 VAD 关不掉时怎么办 | 在上游前加自己的 VAD，压缩停顿 | [判停](../03-mechanisms/turn-detection.md)、[S2S](../02-architectures/s2s.md) |
| 打断的灵敏度 | start 阈值、回声、附和语 | [打断与截断](../03-mechanisms/interruption.md)、[全双工](../02-architectures/full-duplex.md) |
| 前处理顺序 | 降噪在自己的采样率上跑，然后降到 16k，再进 VAD | [音频前处理](../03-mechanisms/audio-preprocessing.md) |

---

## 3. 常见坑

- **把 VAD 的 end 阈值当成判停的全部**，然后在"太快截断"和"太慢回应"之间来回调。真正的解法通常在判停层，不在 VAD 阈值。
- **以为 TEN VAD 自带状态机**。它只输出帧级结果，没有 hangover 和最短静音，直接用会在辅音、气声处频繁切换。
- **帧长不对齐**。Silero 要求固定样本数，传输帧是 20 / 60 ms，中间要缓冲拼接。
- **采样率不对**。Silero 只收 8k / 16k，TEN VAD 只收 16k，直接喂 24k / 48k 的音频结果不可信。
- **用墙钟算静音时长**。小智服务端 Silero VAD 用 `time.time()` 计算距上次有声的时长（从代码看）。网络卡顿后音频突发到达时，墙钟和音频时间对不上，静音会被算长或算短。按处理过的采样数算更稳。
- **绝对音量门槛**伤害小声的孩子。要么不加，要么先做 AGC。
- **回声触发打断**。设备没做 AEC 时，agent 自己的声音会被 VAD 判成用户说话。
- **降噪伪影**。降噪后的音频不一定让 VAD 更准，要 A/B。
- **没有前缀补偿**，字头被吞，ASR 第一个字总是错。

---

## 4. 相关页面

- 同层：[audio-basics](audio-basics.md)、[asr](asr.md)、[latency-budget](latency-budget.md)
- 机制：[判停](../03-mechanisms/turn-detection.md)、[打断与截断](../03-mechanisms/interruption.md)、[音频前处理](../03-mechanisms/audio-preprocessing.md)、[评测](../03-mechanisms/evaluation.md)
- 架构：[回合模型](../02-architectures/turn-model.md)、[全双工](../02-architectures/full-duplex.md)、[S2S](../02-architectures/s2s.md)
