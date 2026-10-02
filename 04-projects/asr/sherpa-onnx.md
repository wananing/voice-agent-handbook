# sherpa-onnx

> 仓库：https://github.com/k2-fsa/sherpa-onnx
> 许可：Apache-2.0（模型随各自来源）（见仓库 LICENSE 文件）
> 分析基于：commit 040afe3
> 状态：草稿
> 最后更新：2026-10-01

## 定位

新一代 Kaldi（k2-fsa）团队的**本地语音推理运行时**，不是一个模型。它用 ONNX Runtime 承载大量第三方和自家训练的模型，提供 ASR（流式和非流式）、TTS、VAD、关键词唤醒（KWS）、标点、语音增强、说话人识别 / 分离、语种识别、音频标签、声源分离等能力（`README.md:97-111`）。

voice agent 关心的是它的覆盖面：同一套 C++ 核心，绑定 12 种语言（C++、C、Python、JavaScript、Java、C#、Kotlin、Swift、Go、Dart、Rust、Pascal）和 WebAssembly，跑在 Android / iOS / Windows / macOS / Linux / HarmonyOS，x86 / arm / riscv64，并支持 RKNN、QNN、Ascend、Axera、OpenVINO 等 NPU（`README.md:26-92`）。所以它常被用作"端侧 / 自托管 ASR + VAD + 唤醒"的一站式底座。

和同类比：FunASR、SenseVoice、FireRedASR 是"模型 + 自家推理脚本"，sherpa-onnx 是"把这些模型（及 icefall 的 zipformer）导出成 ONNX 后统一跑"的运行时；它的中文准确率取决于你选哪个模型，仓库本身不给 CER。

## 整体架构

```
                       ┌─ OnlineRecognizer（流式）──── transducer（zipformer / conformer / lstm / NeMo）
 PCM float32 ─▶ Stream ┤                           ├─ paraformer（FunASR 在线版）
   AcceptWaveform      │                           └─ CTC（zipformer2 / NeMo / WeNet / T-one）
                       │   IsReady → DecodeStreams → GetResult（partial）→ IsEndpoint → Reset
                       │
                       └─ OfflineRecognizer（整段）── SenseVoice / Paraformer / Whisper / FireRedASR /
                                                      Fun-ASR-Nano / Qwen3-ASR / Moonshine / Canary / ...
 旁路组件：VoiceActivityDetector（Silero / TEN VAD）、KeywordSpotter、OnlinePunctuation /
          OfflinePunctuation、OnlineSpeechDenoiser（GTCRN / DPDFNet）、SpeakerEmbedding、TTS
```

核心抽象是 `Recognizer` + `Stream`：调用方持续 `AcceptWaveform`，循环 `IsReady` / `DecodeStream`，随时 `GetResult` 拿 partial，`IsEndpoint` 为真时把当前结果当作一段的 final 并 `Reset`（`sherpa-onnx/csrc/online-recognizer.h:174-234`）。流式模型的种类由 `sherpa-onnx/csrc/online-*-model-config.h` 决定，非流式由 `offline-*-model-config.h` 决定；`offline-recognizer-impl.cc` 按配置里哪个模型路径非空分派实现。

进程模型由调用方决定。仓库自带的服务端：

- C++ `online-websocket-server`：asio 线程池，解码循环每 10 ms 收集就绪连接、跨流 batch（`max_batch_size` 默认 5），客户端发 float32 二进制帧，发文本 `"Done"` 结束（`sherpa-onnx/csrc/online-websocket-server-impl.h:55-70`、`.cc:185-232`、`:325-350`）。
- C++ `offline-websocket-server`、Python `streaming_server.py` / `non_streaming_server.py`、`two-pass-wss.py`（流式首遍 + 离线二遍）。

### 流式 ASR：voice agent 视角

| 维度 | 情况 | 出处 |
|---|---|---|
| 流式模型 | transducer、paraformer、CTC 三类 | `online-recognizer-*-impl.h` |
| chunk 粒度 | paraformer 固定 610 ms（61 帧），左 5、右 3 个 LFR 帧；zipformer 由 ONNX 元数据 `T` / `decode_chunk_len` 决定，具体中文模型的数值仓库里没有，待确认 | `online-recognizer-paraformer-impl.h:508-513`；`online-zipformer2-transducer-model.cc:124-125` |
| 首字 / 定稿延迟 | 仓库没有给出测量值，待确认 | — |
| partial / final | `GetResult` 随时可取 partial；websocket 服务在 `IsEndpoint` 时置 `is_final`，流结束置 `is_eof` | `online-websocket-server-impl.cc:215-225` |
| 端点 | 不是 VAD，按"最后一个非 blank 之后的帧数"算尾部静音。三条规则：没出字静音 2.4 s、出字后静音 1.2 s、段长 20 s；`enable_endpoint` 可关 | `endpoint.h:40-52`；`endpoint.cc:IsEndpoint`；`online-recognizer-transducer-impl.h:375-390` |
| 冲尾巴 | 结束时要补静音再 `InputFinished`：C++ 服务端 `end_tail_padding` 默认 0.8 s；paraformer 最后一块需 `SetOption("is_final","1")` | `online-websocket-server-impl.cc:89-107`；`online-recognizer-paraformer-impl.h:162-166` |
| 热词 | 仅 transducer + `modified_beam_search`：`hotwords_file` / `hotwords_buf`，默认分数 1.5，可按流传入（"/" 分隔） | `online-recognizer-transducer-impl.h:109-119`；`online-recognizer.h:110-133`、`:195` |
| 同音替换 | 所有模型可用 `HomophoneReplacerConfig hr` | `online-recognizer.h:128`；paraformer `GetResult` 中调用 |
| 时间戳 | transducer / CTC 给 token 级时间（秒），在线 paraformer 不填 | `online-recognizer.h:36-38`；`online-recognizer-transducer-impl.h:74-76` |
| 标点 | 流式模型本身不出标点，另有 `OnlinePunctuation` / `OfflinePunctuation`（CT-Transformer）组件 | `online-punctuation.h`、`offline-punctuation-ct-transformer-impl.h` |
| ITN | `rule_fsts` / `rule_fars` 加载 FST，在 `GetResult` 里 `ApplyInverseTextNormalization` | `online-recognizer.h:118`；`online-recognizer-paraformer-impl.h:182` |
| 输入 | float32 采样；websocket 服务端 `--input-sample-rate` 可内部重采样（本 commit 刚加） | `online-websocket-server-impl.h:66-70` |

### 非流式模型怎么做"伪流式"

SenseVoice、Paraformer 离线版、FireRedASR、Whisper 等非流式模型在 sherpa-onnx 里有两种用法：

1. **VAD 切段后整段识别**：`vad-with-non-streaming-asr.py`，VAD 段结束即 final。
2. **VAD + 定时重解**：`simulate-streaming-sense-voice-microphone.py` 用 Silero VAD（`min_silence_duration=0.1`）检出语音后，每 0.2 s 把当前缓冲整段重解一次作为 partial，VAD 段结束后再出 final（`:157-161`、`:202-219`）。
3. **两遍**：`two-pass-speech-recognition-from-microphone.py` / `two-pass-wss.py`：流式模型（如 `streaming-zipformer-zh-14M`）出 partial 并负责端点，离线模型（paraformer 或 SenseVoice）重识别定稿。

### 承载的中文相关模型（README 列出的部分）

- 流式：`streaming-zipformer-bilingual-zh-en`、`streaming-zipformer-small-bilingual-zh-en`、`streaming-zipformer-zh-14M`（"Suitable for Cortex A7 CPU"）（`README.md:283-291`）；WASM 演示还有流式 Paraformer 中英、Paraformer-large 中英粤（`README.md` 演示表）。
- 非流式：SenseVoice（中英日韩粤）、Paraformer-large / small、Zipformer CTC zh、TeleSpeech-ASR（多方言）、FireRedASR（`offline-fire-red-asr-model-config.h`）、Fun-ASR-Nano（`offline-funasr-nano-model-config.h`）、Qwen3-ASR（`offline-qwen3-asr-model-config.h`）。
- 参数量和 CER：README 不给，指向外部文档站，待确认。

### 附带能力（voice agent 常用）

| 能力 | 入口 | 说明 |
|---|---|---|
| VAD | `voice-activity-detector.h:VoiceActivityDetector`；`vad-model-config.h` | Silero VAD 或 TEN VAD；Silero 默认 threshold 0.5、`min_silence_duration` 0.5 s、`min_speech_duration` 0.25 s、`max_speech_duration` 20 s（`silero-vad-model-config.h:16-39`）；RKNN 版 Silero |
| 关键词唤醒 | `keyword-spotter.h:KeywordSpotter` | 基于 transducer，`keywords_file` / `keywords_buf`，默认 `keywords_score` 1.0、`keywords_threshold` 0.25；可按流传入关键词 |
| 语音增强 | `online-speech-denoiser.h`（GTCRN / DPDFNet） | 有流式版本，示例 `online-speech-enhancement-gtcrn.py` |
| 说话人 | `speaker-embedding-extractor.h`、`offline-speaker-diarization.h` | 声纹识别 / 验证、离线分离 |
| 语种识别 / 音频标签 | `spoken-language-identification.h`、`audio-tagging.h` | Whisper LID；zipformer / CED 标签 |
| TTS | `offline-tts-*.h` | VITS、Matcha、Kokoro、ZipVoice 等（本页不展开） |

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 流式识别 API | `sherpa-onnx/csrc/online-recognizer.h:OnlineRecognizer` | `CreateStream` / `IsReady` / `DecodeStreams` / `GetResult` / `IsEndpoint` / `Reset` |
| 流式 transducer | `online-recognizer-transducer-impl.h:OnlineRecognizerTransducerImpl` | 热词、端点按 trailing blanks × 4 计算 |
| 流式 paraformer | `online-recognizer-paraformer-impl.h:OnlineRecognizerParaformerImpl` | 610 ms 块，`DecodeStreams` 逐个解码不 batch（`:171-176`） |
| 端点规则 | `endpoint.h:EndpointConfig`、`endpoint.cc:Endpoint::IsEndpoint` | 三条规则 |
| 非流式分派 | `offline-recognizer-impl.cc:OfflineRecognizerImpl::Create` | 按模型配置选实现 |
| VAD | `voice-activity-detector.cc:VoiceActivityDetector` | `AcceptWaveform` / `IsSpeechDetected` / `Front` / `Pop` |
| KWS | `keyword-spotter.cc:KeywordSpotter` | 流式唤醒词 |
| 流式 websocket 服务 | `online-websocket-server-impl.cc:OnlineWebsocketDecoder::Decode` | 跨流 batch、发 JSON 结果 |
| 结束补静音 | `online-websocket-server-impl.cc:OnlineWebsocketDecoder::InputFinished` | `end_tail_padding` 0.8 s |
| 结果 JSON | `online-recognizer.cc:OnlineRecognizerResult::AsJsonString` | text / tokens / timestamps / segment / is_final |
| Python 示例 | `python-api-examples/speech-recognition-from-microphone-with-endpoint-detection.py` | 端点驱动的 partial / final 循环 |

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：两套。流式识别器内置基于解码结果的端点（`endpoint.cc:Endpoint::IsEndpoint`，出字后尾部静音 1.2 s），另有独立声学 VAD（`voice-activity-detector.h:VoiceActivityDetector`，Silero / TEN VAD）。都不是语义判停。
- [打断与截断](../../03-mechanisms/interruption.md)：不涉及。VAD 的 `IsSpeechDetected` 和 KWS 可被上层用作 barge-in / 唤醒信号，仓库不处理播放截断。
- [首音优化](../../03-mechanisms/first-audio.md)：ASR 侧不涉及；仓库的 TTS 部分未在本页分析。
- [工具回合](../../03-mechanisms/tool-calls.md)：不涉及。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：不涉及。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：提供流式语音增强（`online-speech-denoiser.h`，GTCRN / DPDFNet）和重采样（websocket `--input-sample-rate`）；没有 AEC。
- [评测](../../03-mechanisms/evaluation.md)：仓库不提供 CER 或延迟基准，模型精度要看各模型来源。待确认外部文档站是否有统一测量。

## 取舍与局限

- **能力强在"广"**：一个库同时覆盖流式 ASR、VAD、KWS、增强、TTS 和十几种语言绑定、多 NPU，端侧集成成本最低。
- **准确率要自己选模型、自己测**：仓库不给 CER；端侧 14M 级流式模型和服务端大模型差距明显。
- **流式模型中文生态偏旧**：README 列出的中文流式 zipformer 多为 2023 年模型；新的中文强模型（SenseVoice、Fun-ASR-Nano、Qwen3-ASR、FireRedASR）在这里都是非流式，只能"VAD + 重解"或两遍。
- **结束要冲尾巴**：补 0.3–0.8 s 静音的计算时间要算进定稿延迟。
- **热词有条件**：热词只对 transducer + beam search 生效；在线 paraformer 不 batch、不给时间戳。
- websocket 服务端协议是 sherpa 自定义的（float32 帧 + `"Done"`），不是 OpenAI realtime 协议。

## 相关

- 机制页：[turn-detection](../../03-mechanisms/turn-detection.md)、[audio-preprocessing](../../03-mechanisms/audio-preprocessing.md)、[interruption](../../03-mechanisms/interruption.md)
- 对比页：[model-matrix](../../05-comparison/model-matrix.md)
- 同类项目：[funasr](funasr.md)、[sensevoice](sensevoice.md)、[fireredasr](fireredasr.md)、[ten-vad](../turn-vad/ten-vad.md)
