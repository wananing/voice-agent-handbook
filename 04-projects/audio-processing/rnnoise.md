# RNNoise

> 仓库：https://github.com/xiph/rnnoise（主仓库在 https://gitlab.xiph.org/xiph/rnnoise ，GitHub 是镜像）
> 分析基于：commit `70f1d25`（2025-02-22）
> 状态：草稿
> 最后更新：2026-10-01

## 定位

RNNoise 是 Xiph 的**单通道实时降噪库**：DSP 做频带分析和合成，小型 RNN 只预测每个频带的增益，整体很轻，C 实现，可在 x86（AVX2 / SSE4.1）和 ARM（NEON）上跑。算法来自 Valin 2018 年的论文《A Hybrid DSP/Deep Learning Approach to Real-Time Full-Band Speech Enhancement》（`README:1-8`）。

在 voice agent 链路里它只做一件事：**噪声抑制（NS）**。它不做 AEC，也不做 AGC。顺带输出一个语音概率，可以当成粗糙的 VAD 信号。

面向：要一个免费、开源、可嵌入的降噪模块的场景。商业替代品（Krisp、ai-coustics、Picovoice Koala）在 Pipecat 和 LiveKit 里都有接入，RNNoise 是其中唯一完全开源的。许可是 BSD 风格（`COPYING`）。

## 整体架构

```
48 kHz float PCM（按 int16 量级），每次 480 样本（10 ms）
   │  高通（只去直流，截止约十几 Hz）
   ▼
加窗 FFT（窗长 960，50% 重叠）
   ▼
32 个频带的能量 + 基音相关特征 → 65 维特征
   ▼
RNN：Conv1d×2 → GRU×3 → Dense
   ├─► 32 个频带增益 g[i]
   └─► 语音概率 vad_prob
   ▼
基音滤波 + 增益衰减限速（每帧最多 ×0.6）→ 插值到每个频点
   ▼
作用在上一帧的频谱上（delayed_X）→ IFFT + overlap-add
   ▼
480 样本降噪输出
```

- 帧长 480、窗长 960、32 个频带、65 维特征（`src/denoise.h:31-35`）。
- 网络结构（PyTorch 训练定义）：两层 kernel=3 的 valid Conv1d、三层 GRU，输出 32 维增益和 1 维 VAD（`torch/rnnoise/rnnoise.py:59-72`）。`gru_size` 类默认 256。
- 推理时的 C 版网络在 `src/rnn.c:compute_rnn`，权重是 `rnnoise_data.c`，由 `autogen.sh` 从 Xiph 服务器下载，不在 git 里（`README:22-24`）。
- 增益衰减限制在每帧 ×0.6，注释说相当于 RT60 135 ms，避免过快压低（`src/denoise.c:480-485`）。没有增益下限。
- 无线程模型：调用方每 10 ms 调一次 `rnnoise_process_frame`，同步返回。

**算法延迟**：仓库没有给数字。按代码推断：一帧 10 ms 的缓冲，加上增益作用在上一帧频谱（`delayed_X`，`src/denoise.c:489-496`）带来的一帧延迟，合计约 20 ms（推断，未实测）。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 公共 API | `include/rnnoise.h` | `rnnoise_create(model)` / `rnnoise_destroy` / `rnnoise_get_frame_size()`（返回 480） / `rnnoise_process_frame(st, out, in)` |
| 单帧处理 | `src/denoise.c:rnnoise_process_frame` | 高通 → 特征 → RNN → 增益 → 合成；返回值是语音概率 |
| 特征提取 | `src/denoise.c:rnn_compute_frame_features` | 频带能量、基音相关；静音帧直接跳过 RNN |
| 合成 | `src/denoise.c:frame_synthesis` | IFFT + overlap-add |
| RNN 推理 | `src/rnn.c:compute_rnn` | GRU 前向，SIMD 实现在 `vec_avx.h` / `vec_neon.h` |
| 模型加载 | `include/rnnoise.h:rnnoise_model_from_file` / `_from_buffer` / `_from_filename` | 运行时换模型；另有一个更稀疏的 "little" 模型（`README:121-124`） |
| 命令行示例 | `examples/rnnoise_demo.c` | 读写 48 kHz 16-bit **raw** PCM（不是 WAV） |
| 训练数据生成 | `src/dump_features.c` | 干净语音 + 背景噪声 + 前景噪声 + 可选 RIR 混合，随机增益、随机滤波、随机低通 |
| 训练 | `torch/rnnoise/train_rnnoise.py`、`dump_rnnoise_weights.py` | 训练后转成 C 权重文件 |
| Pipecat 接入 | `pipecat/src/pipecat/audio/filters/rnnoise_filter.py:RNNoiseFilter` | 用 `pyrnnoise` 绑定；transport 采样率不是 48 kHz 时用 soxr（默认 QQ）转 48 kHz 再转回；丢弃 `speech_prob` |

## 接口形态

```c
DenoiseState *st = rnnoise_create(NULL);       // NULL = 内置默认模型
float in[480], out[480];                        // 48 kHz，数值按 int16 量级
float vad_prob = rnnoise_process_frame(st, out, in);
rnnoise_destroy(st);
```

- **采样率固定 48 kHz，帧长固定 480**。16 kHz 链路要先升采样再降回来，Pipecat 就是这么做的。
- **只有开关，没有强度参数**。想要"轻度降噪"只能换模型或自己做干湿混合。
- 返回的语音概率没有在框架里被利用（Pipecat 直接丢掉了）。

## 在 voice agent 链路里放在哪

| 位置 | 是否可行 | 说明 |
|---|---|---|
| 端侧（设备采集后、编码前） | 可行，有 NEON 实现 | 能在 ARM 手机或 Linux 设备上跑；MCU 级设备（如 ESP32）仓库里没有支持，要自己移植，算力和内存是否够待确认 |
| 服务端（解码后、VAD 之前） | 可行，框架里的常见用法 | Pipecat 的 `audio_in_filter` 就挂在输入 transport 之后、VAD 之前（`pipecat/src/pipecat/audio/filters/base_audio_filter.py` 的文档注释） |
| 和 AEC 的先后 | AEC 在前 | RNNoise 不知道扬声器参考信号，回声对它来说是"语音"，压不掉 |

推荐顺序（服务端）：解码 → （需要时升到 48 kHz）→ RNNoise → 降到 16 kHz → VAD → ASR / 上游模型。要不要让 ASR 也吃降噪后的音频是另一个问题：降噪带来的频谱空洞和音乐噪声可能让在原始音频上训练的 ASR / S2S 变差，最好只把降噪后的信号给 VAD，主路保留原始音频，再用 A/B 决定。

## 训练数据

- 干净语音：`datasets.txt` 列出的 OpenSLR 多语种朗读语料（僧伽罗语、南非诸语、孟加拉语、爪哇语、西班牙语方言、英式英语口音等）和 hi_fi_tts 的子集。列表里没有中文语料，也没有儿童语音。
- 噪声：`background_noise.sw`、`foreground_noise.sw` 和 `rnnoise_contributions.tar.gz`（2025-01-30 更新），都是外部下载，不在仓库里（`README:50-59`）。噪声类型分布待确认。
- 增强：语音增益在 −45…+10 dB 间随机，噪声增益相对语音随机，1/8 概率无背景噪声，随机滤波器响应，随机低通，可选 RIR 混响（`src/dump_features.c:385-420`）。

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：不涉及。`rnnoise_process_frame` 返回的语音概率理论上能当 VAD 用，但没有阈值、平滑和状态机，框架也没用它。
- [打断与截断](../../03-mechanisms/interruption.md)：不涉及。
- [首音优化](../../03-mechanisms/first-audio.md)：不涉及；它会在上行链路增加约 20 ms 算法延迟（按代码推断），加上 Pipecat 包装里的重采样和攒帧。
- [工具回合](../../03-mechanisms/tool-calls.md)：不涉及。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：不涉及。`DenoiseState` 有内部状态，换会话时应新建或重置。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：核心功能，只覆盖噪声抑制一项。入口 `src/denoise.c:rnnoise_process_frame`，48 kHz / 10 ms 帧；框架接入见 `pipecat/.../audio/filters/rnnoise_filter.py:RNNoiseFilter`。不做 AEC、AGC。
- [评测](../../03-mechanisms/evaluation.md)：仓库里没有评测脚本或客观指标，只有 README 指向的在线 demo。

## 取舍与局限

- **轻**：DSP + 小 RNN，适合实时和端侧；代价是频带级分辨率（32 个频带，低频每个频带约 100 Hz 宽，`src/denoise.c:63-65`），对低频风噪和与人声重叠的噪声分不干净。
- **无增益下限、无强度调节**：噪声突变时可能出现"呼吸感"，小声说话可能被一起压低。
- **对"别人说话"基本不压**：它的目标是保留语音，人群嘈杂声、电视里的人声都会被当成语音（推断）。
- **固定 48 kHz**：16 kHz 链路要来回重采样。
- **训练语料无中文、无儿童**；模型权重不在 git 里，构建时要联网下载。
- 本身没有 AEC，不能替代 [webrtc-audio-processing](webrtc-audio-processing.md) 那一类的回声消除。

## 相关

- 机制页：[音频前处理](../../03-mechanisms/audio-preprocessing.md)
- 项目页：[webrtc-audio-processing](webrtc-audio-processing.md)、[pipecat](../frameworks/pipecat.md)、[aiortc](aiortc.md)
- 对比页：待补
