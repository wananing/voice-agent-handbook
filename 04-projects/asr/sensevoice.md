# SenseVoice

> 仓库：https://github.com/FunAudioLLM/SenseVoice
> 许可：代码 MIT；权重待确认（见仓库 LICENSE 文件）
> 分析基于：commit ea15219
> 状态：草稿
> 最后更新：2026-10-01

## 定位

SenseVoiceSmall 是一个 234M 的非自回归语音理解模型：一次前向同时给出语种（LID）、情感（SER）、音频事件（AED）和带标点 / ITN 的转写，支持中、粤、英、日、韩五种语言（`README_zh.md:29`、`:37`）。仓库本身是模型的推理、微调、导出和部署样例集合，底层推理大多依赖 FunASR（`README_zh.md:107`）。

在 voice agent 里它的定位是"**整句识别快、附带副语言标签**"：短句识别延迟低，`<|Laughter|>`、`<|Cry|>` 等事件和情感标签可以直接喂给对话策略。但它**不是流式模型**，partial 只能靠反复重解整段来模拟。和 FunASR 的关系：FunASR 是工具箱，SenseVoiceSmall 是其中一个 checkpoint，也能当 FunASR 2pass 服务的二遍模型；和 sherpa-onnx 的关系：sherpa-onnx 把它作为离线模型承载，配 VAD 做伪流式。

## 整体架构

```
16k mono PCM ─▶ 80 维 fbank + LFR ─▶ 前置 4 个 query token [lang, event, emotion, itn]
             ─▶ SAN-M encoder（70 层）─▶ CTC head ─▶ greedy CTC
             ─▶ "<|zh|><|NEUTRAL|><|Speech|><|withitn|>文本……"
             ─▶ rich_transcription_postprocess 去掉 / 转换标签
```

模型结构是 SAN-M 编码器 + CTC，没有自回归解码（`runtime/llama.cpp/README.md` Architecture 一节）。语种、ITN 开关都是作为 query embedding 拼在特征前面的：`lid_dict` 选语种，`textnorm_dict` 的 `withitn` / `woitn` 决定输出是否带标点和 ITN（`model.py:634-638`、`:825-847`）。所以**标点和 ITN 是模型直接生成的**，不需要外挂 ct-punc 或 FST。

自注意力看整段输入，输出一次性给出，因此"流式"只有两种做法：

1. 第三方 `streaming-sensevoice`：截断注意力分块推理，"牺牲了部分精度"，带 CTC prefix beam search 和热词（`README_zh.md:465`）。不在本仓库。
2. 本仓库 `sensevoice-server`：FSMN-VAD 开着时，每 `--partial-ms`（默认 400 ms）把当前未结束的语音段整段重编码一次，和上次结果做前缀 diff 后发 delta；VAD 段结束时整段再识别一次作为 final（`runtime/llama.cpp/sensevoice-server/sensevoice-server.cpp:792-839`）。

部署形态：

| 形态 | 入口 | 适合 |
|---|---|---|
| Python（FunASR `AutoModel`） | `README_zh.md` 推理一节、`demo1.py`、`demo2.py` | 文件 / 整句识别，可配 `vad_model="fsmn-vad"` 切长音频 |
| ONNX / libtorch | `demo_onnx.py`（`funasr_onnx.SenseVoiceSmall`）、`demo_libtorch.py`、`export.py` | 无 PyTorch 训练栈的推理 |
| HTTP 文件转写 | `api.py`：FastAPI `POST /api/v1/asr` | 整段上传，非流式 |
| C++ 单二进制（llama.cpp / GGUF） | `runtime/llama.cpp/`：`funasr-sensevoice` CLI、`funasr-vad`、`sensevoice-server` | CPU / 端侧，无 Python；Q8 量化 |
| OpenAI 兼容实时服务 | `sensevoice-server`：REST `/v1/audio/transcriptions` + WS `/v1/realtime?intent=transcription` | 单机演示、端侧；见下 |
| FunASR runtime 二遍 | FunASR `funasr-wss-server-2pass` 的 `svs_lang` / `svs_itn` | 需要真流式 partial + SenseVoice 定稿时 |

### sensevoice-server：voice agent 最可能直接接的路径

协议是 OpenAI realtime transcription：客户端发 `session.update`、`input_audio_buffer.append`（base64 PCM16，16 kHz mono）、`commit`、`clear`；服务端回 `speech_started` / `speech_stopped`、`...transcription.delta`（partial）、`...transcription.completed`（final）（`sensevoice-server/README.md` Realtime WebSocket 一节）。

| 维度 | 行为 | 出处 |
|---|---|---|
| 流式 | 伪流式：VAD 判定在说话时，每 400 ms 整段重编码；新音频不足 5 帧则跳过 | `sensevoice-server.cpp:792-800` |
| partial | 前缀相同时发增量，否则重发全文（假设被修正） | `:808-814` |
| final | VAD 段结束 → `finalize_segment` 整段识别；或客户端 `commit` | `:772-774`、`:823-853` |
| 内置端点 | FSMN-VAD 状态机；最长段 `--vad-maxseg` 默认 30000 ms | README 参数表 |
| 尾部静音 | 阈值随累计语音时长分档，会话开头一档为 2000−150 = 1850 ms（从代码推断）；实际判停延迟待实测 | `VadStream::recompute`，`:395-399`；判停在 `:529` |
| 关掉 VAD | `turn_detection.type` 为 `none` / `disabled` / `manual` 时 `vad_on_=false` | `:693-695` |
| 手动模式 | 没有 partial（`maybe_partial` 在无 VAD 时直接返回，`:793`），`commit` 时整段出一次 final | `:841-848` |
| 并发 | 前向用一把全局 mutex 串行，面向单 CPU 端侧；`--max-connections` 默认 4 | `:166`、`:255`；README |

### 能力清单

| 维度 | 情况 | 出处 |
|---|---|---|
| 标点 / ITN | 有，`use_itn=True`（或 `textnorm="withitn"`）一个开关同时控制 | `README_zh.md:164`；`model.py:836-840` |
| 时间戳 | 有：`output_timestamp=True` 时用 CTC 强制对齐给出 `timestamp=[[开始毫秒,结束毫秒],...]`，与 `words` 一一对应（旧版 `[词元, 开始秒, 结束秒]` 三元组已不兼容） | `README_zh.md:119-121`；`model.py:886-935`、`model.py:SenseVoiceSmall.post` |
| 热词 | 官方没有；README 列了第三方 `SenseVoiceSmall_hotword` 和 `streaming-sensevoice` | `README_zh.md:465-467` |
| 情感 | HAPPY / SAD / ANGRY / NEUTRAL / FEARFUL / DISGUSTED / SURPRISED（微调数据格式），推理侧 `emo_dict` 只列了 happy / sad / angry / neutral / unk | `README_zh.md:399`；`model.py:639` |
| 事件 | BGM / Speech / Applause / Laughter / Cry / Sneeze / Breath / Cough | `README_zh.md:411` |
| 输入 | 16 kHz mono；`api.py` 会用 torchaudio 重采样，sensevoice-server REST 用 miniaudio 解码任意格式，WS 只收 PCM16 | `api.py:56-75`；`sensevoice-server/README.md` |

### 中文效果定位

- README 正文的识别准确率对比只以图片给出（`image/asr_results1.png`、`asr_results2.png`，`README_zh.md:61-65`），文字结论是"中文和粤语识别效果上 SenseVoice-Small 具有明显优势"（对比对象 Whisper）。
- README 唯一的文字数字是速度：参数量与 Whisper-Small 相当，推理比 Whisper-Small 快 5 倍以上、比 Whisper-Large 快 15 倍（`README_zh.md:93`）。
- 非 README 来源：`runtime/llama.cpp/BENCHMARKS.md` 在 184 条成人普通话长音频（44–60 s）上给出 CER 7.81（fp32）/ 8.17（Q8），CPU 8 线程约 20 倍实时，f16 权重 449 MB。注意这组数据是长音频、走了 VAD 切段，和 voice agent 的短句分布不同。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 模型定义与推理 | `model.py:SenseVoiceSmall.inference` | 拼 lang / event / emo / textnorm query，CTC greedy，可选时间戳 |
| 时间戳后处理 | `model.py:SenseVoiceSmall.post` | subword 秒级对齐 → 词级毫秒对 |
| CTC 强制对齐 | `utils/ctc_alignment.py` | 时间戳的对齐实现 |
| 标签清洗 | FunASR `rich_transcription_postprocess` | 把语种、情感、事件等特殊标签转成 emoji 或去掉 |
| HTTP 服务 | `api.py:turn_audio_to_text` | FastAPI 文件上传，非流式 |
| C++ 实时服务 | `runtime/llama.cpp/sensevoice-server/sensevoice-server.cpp:WsSession` | VAD 驱动的 partial / final，OpenAI realtime 协议 |
| C++ 流式 VAD | `sensevoice-server.cpp:VadStream` | FSMN-VAD 状态机的增量版本，抄自 `funasr-common/funasr_vad.h` |
| GGUF 导出 | `runtime/llama.cpp/export_sensevoice_gguf.py`、`export_vad_gguf.py` | 转 GGUF 并量化 |
| 长音频无 VAD | `long_audio_no_vad.py` | 有界重叠窗口，离线场景 |
| 微调 | `finetune.sh` | 修长尾样本 |

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：模型本身不涉及；`sensevoice-server` 内置 FSMN-VAD 端点（`sensevoice-server.cpp:VadStream`），尾部静音按累计时长分档、开头一档约 1850 ms（从代码推断），可以用 `turn_detection.type:"none"` 改为客户端 `commit` 判停。
- [打断与截断](../../03-mechanisms/interruption.md)：不涉及。`input_audio_buffer.speech_started` 事件可被上层当作 barge-in 信号。
- [首音优化](../../03-mechanisms/first-audio.md)：不涉及。
- [工具回合](../../03-mechanisms/tool-calls.md)：不涉及。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：不涉及；每个 WS 会话自带音频缓冲，`--vad-slot-ms`（默认 2000 ms）无声后重置。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：只做重采样 / 解码和 fbank（预加重 0.97，`sensevoice-server.cpp:67`），没有 AEC、降噪。事件标签（Laughter / Cry / BGM）可视为对非语音输入的分类，但不做抑制。
- [评测](../../03-mechanisms/evaluation.md)：`runtime/llama.cpp/BENCHMARKS.md` 给出 CER（micro，`normalize_zh` 口径）和 CPU 实时倍数；`benchmarks/ser/` 是情感识别评测契约（UA / WA）。没有流式延迟评测。

## 取舍与局限

- **非流式**：partial 来自整段重编码，语音段越长每次 partial 越贵；sensevoice-server 的全局锁让多路并发时互相排队。多路服务应走 FunASR runtime 二遍或 Triton（`FunASR/runtime/triton_gpu/model_repo_sense_voice_small`）。
- **判停偏慢**：sensevoice-server 的 VAD 尾部静音在会话开头约 1.85 s（从代码推断），比 FunASR C++ 默认的 800 ms 长，对话场景要么调参要么用客户端 commit。
- **没有官方热词**，专有名词只能靠微调或第三方版本。
- **标签要剥离**：输出里带 `<|zh|><|NEUTRAL|><|Speech|>` 等 token，直接拼进 LLM 上下文前要处理；sensevoice-server 默认去掉，`--keep-tags` 保留。
- 优点是整句识别快、标点 / ITN 内置、副语言标签对陪伴类 agent 有用。

## 相关

- 机制页：[turn-detection](../../03-mechanisms/turn-detection.md)、[audio-preprocessing](../../03-mechanisms/audio-preprocessing.md)、[evaluation](../../03-mechanisms/evaluation.md)
- 对比页：[model-matrix](../../05-comparison/model-matrix.md)
- 同类项目：[funasr](funasr.md)、[sherpa-onnx](sherpa-onnx.md)、[fireredasr](fireredasr.md)
