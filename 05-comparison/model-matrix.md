# 模型对比矩阵：ASR / TTS / 端到端

> 状态：草稿
> 最后更新：2026-10-02

对象：[04-projects](../04-projects/README.md) 中 asr 下 4 个、tts 下 6 个、e2e-models 下 3 个加 full-duplex 下的 moshi，另附 turn-vad 下 2 个和 audio-processing 下 3 个的小表。表中每格都取自对应项目页；项目页没有依据的写"待确认"；数字一律是项目页引用的仓库自述，后面括号注明出处文件，本手册没有复现。

阅读提示：

- 带"推断"的格子是项目页按代码推出、没有实测的结论。
- 各项目自述数字的硬件、测试集、计时起点都不同，表里不做对齐，差异写在每张表下方和文末。

## 一、ASR

### 流式与端点

| 项目 | 流式 | chunk 与首字延迟 | 内置 VAD / 端点 |
|---|---|---|---|
| [funasr](../04-projects/asr/funasr.md) | 是：2pass，在线 Paraformer 出 partial，VAD 段结束后离线模型定稿；另有 Fun-ASR-Nano 的重解码式伪流式（需 GPU） | 块 600 ms，回看 300 ms、前瞻 300 ms；首字约一块 + 前瞻，实测待确认 | FSMN-VAD，C++ 默认尾部静音 800 ms、最长段 15 s |
| [sensevoice](../04-projects/asr/sensevoice.md) | 否，整句模型；`sensevoice-server` 每 400 ms 把未结束语音段整段重编码做伪流式 | 不分块；首字待确认 | 模型无；`sensevoice-server` 内置 FSMN-VAD，开头一档尾部静音约 1850 ms（推断），可改客户端 `commit` |
| [fireredasr](../04-projects/asr/fireredasr.md) | 否，编码器只有 padding mask，无 chunk 接口 | 不适用，只有整句 final | 无，需外接 |
| [sherpa-onnx](../04-projects/asr/sherpa-onnx.md) | 运行时：流式 transducer / paraformer / CTC；非流式模型可走"VAD + 定时重解"或两遍 | 在线 paraformer 610 ms；zipformer 由模型元数据决定，中文模型数值待确认；首字待确认 | 解码端点（出字后静音 1.2 s、未出字 2.4 s、段长 20 s）；另有独立 Silero / TEN VAD |

### 后处理能力

| 项目 | 标点 | ITN | 热词 | 时间戳 |
|---|---|---|---|---|
| [funasr](../04-projects/asr/funasr.md) | 有，定稿过 ct-punc，partial 无 | 有，FST ITN 默认开 | 有，仅离线那一遍 | 字级，仅离线那一遍 |
| [sensevoice](../04-projects/asr/sensevoice.md) | 模型直接生成（`withitn`） | 模型直接生成，与标点同一开关 | 官方无，第三方版本有 | 有，CTC 强制对齐 |
| [fireredasr](../04-projects/asr/fireredasr.md) | 无，训练文本去掉了标点 | 无，评测脚本可选 `cn2an` | 无 | 无 |
| [sherpa-onnx](../04-projects/asr/sherpa-onnx.md) | 流式模型不出，另有 CT-Transformer 标点组件 | 可加载 FST 规则 | 仅 transducer + `modified_beam_search` | transducer / CTC 有 token 级，在线 paraformer 无 |

### 中文准确率、部署与许可

| 项目 | 中文 CER（仓库自述，注明出处） | 部署形态 | 许可 |
|---|---|---|---|
| [funasr](../04-projects/asr/funasr.md) | Paraformer-zh（220M）AISHELL-1 1.95%（`README_zh.md`、`runtime/docs/benchmark_onnx_cpp.md`）；SenseVoiceSmall 历史报告 7.81%（`docs/benchmark/historical_asr_zh.md`） | C++ ONNX websocket 服务（decoder 线程池，无跨路 batch）；GPU 走 Triton；Python `AutoModel`；端侧弱 | 代码 MIT；权重在 ModelScope 另有许可，待确认 |
| [sensevoice](../04-projects/asr/sensevoice.md) | README 只有对比图；`runtime/llama.cpp/BENCHMARKS.md` 184 条普通话长音频 CER 7.81（fp32）/ 8.17（Q8） | Python / ONNX / libtorch；llama.cpp GGUF 单二进制；OpenAI realtime 兼容的 `sensevoice-server`（全局锁，默认 4 连接）；FunASR 二遍 | 代码 MIT；权重待确认 |
| [fireredasr](../04-projects/asr/fireredasr.md) | AED（1.1B）aishell1 0.55 / Average-4 3.18；LLM（8.3B）0.76 / 3.05（`README.md`） | Python CLI / API，按文件路径批量；AED 有 Triton + TensorRT；sherpa-onnx 有离线支持 | 代码 Apache-2.0；权重待确认 |
| [sherpa-onnx](../04-projects/asr/sherpa-onnx.md) | 仓库不给，取决于所选模型 | C++ 核心，12 种语言绑定 + WASM，覆盖移动端和多种 NPU；websocket 服务可跨流 batch | Apache-2.0（模型随各自来源） |

**说明**

- **本质差异：是不是流式模型。** FunASR 在线 Paraformer 和 sherpa-onnx 承载的 transducer / CTC 是真流式；SenseVoice、FireRedASR 结构上是整句模型，partial 只能靠重解，段越长每次越贵。这决定了它们在链路里的位置：前两者可出 partial，后两者适合当定稿或二遍。
- **端点都不是语义判停**：FunASR、sensevoice-server 用 FSMN-VAD，sherpa-onnx 用"最后一个非 blank 之后的帧数"。三者默认尾部静音（800 ms / 约 1850 ms / 1.2 s）定义不同，不能直接比。
- **实现进度差异**：SenseVoice 没有官方热词、FireRedASR 没有标点 / ITN / 时间戳，都可以外挂补；FunASR 热词和时间戳只在离线那一遍，是 2pass 结构的结果。
- 许可一列是代码许可（仓库 LICENSE 文件）；FunASR / SenseVoice / FireRedASR 的模型权重托管在 ModelScope / HF，许可需单独核对。

## 二、TTS

### 结构与流式

| 项目 | 结构 | 采样率 | 文本流入 | 音频流出 |
|---|---|---|---|---|
| [cosyvoice](../04-projects/tts/cosyvoice.md) | Qwen2 LM（0.5B）→ 25 Hz 语音 token → flow matching → HiFT | 24 kHz（CV1 为 22.05 kHz） | 支持（CV2 / 3 的 bistream），不能与 vLLM 同用 | 支持，首块要先攒约 1.1–2.1 s 音频的 token（推断） |
| [fish-speech](../04-projects/tts/fish-speech.md) | Dual-AR（4B Slow AR + 400M Fast AR）+ modded DAC codec | 44.1 kHz | 不支持 | 名义支持，实际按 batch 整段出；无说话人标签时首包等于整段合成 |
| [index-tts](../04-projects/tts/index-tts.md) | GPT AR → semantic code → CFM flow matching（25 步）→ BigVGAN；2.5 为 0.8B | 22.05 kHz | 不支持 | PyTorch 按文本段整段出（超过 120 token 才切）；TRT 后端（仅 2.0）100 code 一块 |
| [spark-tts](../04-projects/tts/spark-tts.md) | Qwen2.5-0.5B → BiCodec token，无 flow matching | 16 kHz | 不支持 | 本地整段；Triton + TRT-LLM 流式，首块 1.0 s 音频 |
| [fireredtts2](../04-projects/tts/fireredtts2.md) | dual-transformer AR 逐帧出多码本 → 12.5 Hz 流式 codec | 24 kHz | 不支持 | 逐帧，每块 80 ms |
| [voxcpm](../04-projects/tts/voxcpm.md) | MiniCPM-4 骨干 + LocDiT 扩散自回归，AudioVAE 连续表征；VoxCPM2 为 2B | 48 kHz（1.5 为 44.1，0.5B 为 16） | 不支持 | 每步一个 patch，约 160 ms（推断） |

### 首包、音色与文本前端

| 项目 | 首包（仓库自述，注明口径） | 克隆方式 | 文本前端 TN |
|---|---|---|---|
| [cosyvoice](../04-projects/tts/cosyvoice.md) | README"低至 150 ms"，未说明硬件和口径；Triton + TRT-LLM，L20 并发 1：CV2 P50 218 ms，开说话人缓存 185 ms（客户端发请求到收到首块）；CV3 只有并发 4 的 P50 740 ms | 零样本：参考音频（≤ 30 s）+ 参考文本；跨语言模式不需参考文本；instruct 控制方言、情感；说话人可缓存 | 有（ttsfrd / wetext）；文本流入时跳过 |
| [fish-speech](../04-projects/tts/fish-speech.md) | README H200 TTFA 约 100 ms，是外部 SGLang-Omni 的数字；本仓库路径待确认 | 参考音频（通常 10–30 s）+ 参考文本；可注册 `reference_id`；句内自由文本情感标签 | 推理路径无（`normalize` 字段未生效） |
| [index-tts](../04-projects/tts/index-tts.md) | 无首包数字；只有 RTF（4090，2.5 bf16）7 字 0.29、16 字 0.22，项目页推断 7 字首包约 0.4 s | 单段参考（截断到 15 s），不需参考文本；情感与音色分离（情感参考 / 8 维向量 / 文本推断） | 有（WeTextProcessing / wetext），支持拼音标注 |
| [spark-tts](../04-projects/tts/spark-tts.md) | Triton 流式，L20、26 条样本，首块 P50：并发 1 为 210 ms，并发 2 为 226 ms，并发 4 为 1018 ms（客户端发请求到收到首块；流式行的提交链接指向第三方 fork） | 参考音频 + 可选参考文本（续写式）；不克隆时可按性别 / 音高 / 语速造音色 | 无 |
| [fireredtts2](../04-projects/tts/fireredtts2.md) | README"低至 140 ms"（L20），未说明口径；仓库唯一计时不含参考编码和音频解码 | 参考音频 + 参考文本必须同时给；无参考时随机音色；最多 4 说话人 | 只有符号清洗，无 TN |
| [voxcpm](../04-projects/tts/voxcpm.md) | 无首包数字；只有 RTF（4090）VoxCPM2 约 0.30，Nano-vLLM 约 0.13 | 只给参考音频即可（无需转写），或续写式，或两者同时；可凭描述设计音色；括号风格指令 | 有（wetext），默认关 |

### 取消、部署与许可

| 项目 | 取消能力 | 部署加速 | 显存 | 许可 |
|---|---|---|---|---|
| [cosyvoice](../04-projects/tts/cosyvoice.md) | 无取消接口，LLM 线程会跑完（推断） | vLLM（LLM 部分）、TensorRT（flow）、Triton + TRT-LLM | 待确认 | 代码 Apache-2.0；权重待确认 |
| [fish-speech](../04-projects/tts/fish-speech.md) | 无，客户端断开不中止 worker（推断） | 本仓库无，可 `--compile`；外部 SGLang-Omni / vLLM-Omni | 文档建议 ≥ 24 GB | Fish Audio Research License，商用需另签授权 |
| [index-tts](../04-projects/tts/index-tts.md) | PyTorch 停止迭代后不再合成下一段，段内不可中断（推断）；TRT 路径待确认 | DeepSpeed、`use_accel`、`torch.compile`；TRT + TRT-LLM + PyTriton（仅 2.0）；vLLM 为外部 recipe | README 未给；< 10 GB 自动进 low-VRAM 模式 | bilibili Model Use License（代码和权重），大主体需另行申请 |
| [spark-tts](../04-projects/tts/spark-tts.md) | 本地一次性 `generate`，不可中止；Triton 路径待确认 | Triton + TRT-LLM；无 vLLM | 待确认 | 代码 Apache-2.0；权重待确认 |
| [fireredtts2](../04-projects/tts/fireredtts2.md) | 停止迭代即停，粒度 80 ms（推断） | 无 vLLM / TensorRT，支持 bf16 | fp32 约 14 GB，bf16 约 9 GB | 代码 Apache-2.0；权重待确认；README 称克隆仅限学术研究 |
| [voxcpm](../04-projects/tts/voxcpm.md) | 停止迭代即停，粒度约 160 ms（推断） | `optimize()`（torch.compile）；外部 Nano-vLLM / vLLM-Omni；llama.cpp-omni 端侧 | VoxCPM2 约 8 GB，1.5 约 6 GB，0.5B 约 5 GB | 代码和权重 Apache-2.0，可商用 |

**说明**

- **本质差异：流式粒度由结构决定。** FireRedTTS-2（逐帧 80 ms）和 VoxCPM（逐 patch）结构上就是细粒度流式；CosyVoice 要攒一段 token 再过 flow matching；IndexTTS 的 CFM + BigVGAN 和 Spark-TTS 的整段 detokenize 在官方 PyTorch 路径里都是整段出。Spark-TTS 和 IndexTTS 的分块流式只在 Triton / TRT 部署路径里，属于部署实现，不是本地推理能力。
- **本质差异：文本流入。** 六个里只有 CosyVoice CV2 / 3 训练了文本-语音交错（5:15），其余都要整句文本。这一项补代码补不出来。
- **实现进度差异：取消和服务化。** 六个项目都没有显式取消接口，"停止迭代就停"只对同步 generator 成立；CosyVoice、fish-speech 的后台线程会继续跑。官方服务端普遍不开流式，生产形态多依赖 Triton 或外部 vLLM 类项目。
- **中文前端**：CosyVoice、IndexTTS 默认带 TN；VoxCPM 有但默认关；fish-speech、Spark-TTS、FireRedTTS-2 没有，数字、单位要在上游处理。

**首包口径不一致（不要直接比大小）**

- CosyVoice 218 / 185 ms 和 Spark-TTS 210 ms 都是"Triton + TRT-LLM、L20、客户端发请求到收到首块"，口径最接近，但样本集不同（Spark-TTS 26 条，CosyVoice 待确认），且 Spark-TTS 流式行指向第三方 fork。
- CosyVoice"150 ms"和 FireRedTTS-2"140 ms"是 README 宣传值，没有说明计时起止；FireRedTTS-2 仓库里唯一的计时从参考 token 化之后算到第 2 帧，不含参考编码和音频解码。
- fish-speech"约 100 ms"是 H200 上外部 SGLang-Omni 的数字，与本仓库推理代码无关。
- IndexTTS、VoxCPM 只有 RTF，首包是项目页按 RTF 推算或待实测。

**质量自述互相冲突**：VoxCPM README 的 Seed-TTS test-ZH 里 VoxCPM2 SIM 79.5 高于 CosyVoice3 的 78.0，IndexTTS README 的 CV3-Eval zh 里 VoxCPM2 SS 74.99 低于 CosyVoice3 的 80.01；FireRedTTS-2 在 VoxCPM README 里 CER 1.14，在 IndexTTS README 里 WER 8.22。都是各家自报、测试集不同，本页不列质量排名。

## 三、端到端与音频 LLM

### 模态、流式与工具

| 项目 | 输入 | 输出 | 流式 | 工具调用 |
|---|---|---|---|---|
| [qwen3-omni](../04-projects/e2e-models/qwen3-omni.md) | 文本、图像、音频、视频 | 文本 + 语音（Instruct，Talker 3 个内置音色）；`vllm serve` 只有 Thinker，只出文本 | 开源仓库不能：整段进、整段出；实时交互只在云服务 | 系统提示手写 `<tools>` 模板的单次触发示例；回注和续答无示例，待确认 |
| [step-audio2](../04-projects/e2e-models/step-audio2.md) | 音频 + 文本 | 文本，或文本 + 语音（逐回合可选，token2wav 24 kHz） | 输出可流式（SSE + token2wav 分块）；输入整段，按 25 s 切块 | 原生：stepfun vLLM 分支的 tool-call-parser + OpenAI `tools` 参数，有"调用 → 回注 → 续答"完整示例；并行调用待确认 |
| [ultravox](../04-projects/e2e-models/ultravox.md) | 音频 + 文本 | 只出文本 | 输出文本流式；输入整段（块因果编码只在实验配置） | 本仓库无；继承底座 LLM 的能力待确认 |
| [moshi](../04-projects/full-duplex/moshi.md) | 用户音频流 | 音频 + 自身文本（inner monologue，不转写用户） | 原生全双工，每 80 ms 一步；README 理论 160 ms，L4 实测约 200 ms | 无 |

### 中文、资源与开源程度

| 项目 | 中文 | 显存 | 开源程度 | 许可 |
|---|---|---|---|---|
| [qwen3-omni](../04-projects/e2e-models/qwen3-omni.md) | 语音输入 19 种、输出 10 种语言，均含中文；中文 ASR WER：WenetSpeech net / meeting 4.69 / 5.89，Fleurs-zh 2.20（`README.md`） | 30B-A3B MoE；BF16 15 s 视频输入 Instruct 78.85 GB；纯音频待确认；`disable_talker()` 约省 10 GB | 权重 + demo / cookbook，仓库不含模型实现（在 transformers / vLLM） | 代码 Apache 2.0；权重待确认 |
| [step-audio2](../04-projects/e2e-models/step-audio2.md) | mini 中文 ASR CER：AISHELL 0.78，中文平均 3.19，方言口音平均 9.85（`README.md`） | 7B 级；仓库无显存数字，单张 24 GB 是否够待确认 | 只开源 mini 系列三个权重；不含模型实现（`trust_remote_code` + stepfun vLLM 分支） | 权重 Apache 2.0 |
| [ultravox](../04-projects/e2e-models/ultravox.md) | 有 wenetspeech 训练数据；无中文评测数字，待确认 | 随底座：8B 单卡，默认 70B 需多卡（`start_vllm` 写 tp=8） | 完整训练、数据处理、评测栈，底座可换 | 代码 MIT；权重随底座，待确认 |
| [moshi](../04-projects/full-duplex/moshi.md) | 不支持，只说英语 | PyTorch bf16 约 24 GB；Rust 支持 int8，MLX 支持 int4 / int8 | 模型 + 三套推理 + Mimi codec + 服务端 | 权重 CC-BY 4.0；代码 Python 部分 MIT、Rust 后端 Apache-2.0 |

**说明**

- **本质差异：会话形态。** moshi 是唯一的流式输入 + 流式输出，判停和打断都在模型内；Qwen3-Omni、Step-Audio2、Ultravox 的开源形态都是请求-响应，判停、打断由外层负责，在链路里更接近[半级联](../02-architectures/half-cascade.md)或轮次式 S2S。三家的"实时"能力都在闭源云服务里。
- **本质差异：输出模态。** Ultravox 只出文本，必须外接 TTS；Step-Audio2 每回合可选出不出语音；Qwen3-Omni 走 vLLM 服务时只出文本，要出语音得用 transformers 路径。
- **实现进度差异：工具调用。** Step-Audio2 有原生 parser 和完整回注示例；Qwen3-Omni 和 Ultravox 理论上继承底座的工具格式，但仓库没有验证。moshi 没有系统提示和工具训练，属于结构限制。
- **共同限制**：四个模型都不产出用户转写（moshi 的文本流只转写自己），要文本历史得另跑 ASR 或额外请求一次转写。
- **中文 ASR 数字口径**：Qwen3-Omni 页标注为 WER，Step-Audio2 页标注为 CER，两者都是 WenetSpeech 的 net / meeting 子集，但项目页的指标名不一致，见文末。

## 四、判停与 VAD

| 项目 | 类型 | 输入 | 输出 | 体量与速度（README 自述） | 中文 | 接入 | 许可 |
|---|---|---|---|---|---|---|---|
| [smart-turn](../04-projects/turn-vad/smart-turn.md) | 只看音频的语义判停 | 16 kHz，整轮音频最长 8 s，每次整轮重算 | P(complete)，阈值写死 0.5 | int8 8 MB / fp32 32 MB；部分 CPU 低至 10 ms，多数云主机 < 100 ms | 23 种语言含中文，效果待确认 | pipecat 默认判停 | BSD 2-clause |
| [ten-vad](../04-projects/turn-vad/ten-vad.md) | 帧级 VAD | 16 kHz int16，每次 160 或 256 样本 | 帧级概率 + 0/1 标志，无起止状态机 | 库约 300 KB；Xeon Gold 6348 RTF 0.0086（Silero 0.0127） | 评测集以英文为主，中文待确认 | TEN Framework 扩展、sherpa-onnx | Apache 2.0 加附加条款（禁止与 Agora 产品竞争的部署） |

两者不是同一层：ten-vad 给出"静音开始了"的信号，smart-turn 在短静音后判断"说完没有"。ten-vad 只有帧级结果，上层状态机的默认值差异很大（TEN 扩展 1000 ms 静音判结束，sherpa-onnx 0.5 s）。

## 五、音频前处理与传输

| 项目 | 功能 | 输入规格 | 应放在哪 | 接口 | 框架接入 | 许可 |
|---|---|---|---|---|---|---|
| [rnnoise](../04-projects/audio-processing/rnnoise.md) | 单通道降噪（顺带语音概率），不做 AEC / AGC | 固定 48 kHz，480 样本（10 ms）一帧；算法延迟约 20 ms（推断） | 端侧或服务端，AEC 之后、VAD 之前 | C | pipecat `RNNoiseFilter` | BSD 风格 |
| [webrtc-audio-processing](../04-projects/audio-processing/webrtc-audio-processing.md) | AEC3 / AECM、NS、AGC1 / AGC2、高通 | 约 10 ms 一块，8–384 kHz | AEC 必须在端侧（要扬声器参考和准确延迟）；NS、AGC 两端都可 | C++17 类接口，无 C API、无官方 Python 绑定 | livekit-agents 仅 console 模式用 libwebrtc 的 APM（不是这个打包） | BSD 风格加专利授权 |
| [aiortc](../04-projects/audio-processing/aiortc.md) | 纯 Python WebRTC 协议栈（传输层，不处理音频内容） | Opus 固定 48 kHz 立体声 96 kbps；另有 G.722、PCMU、PCMA | 服务端传输层，浏览器直连 Python 进程 | Python asyncio | pipecat SmallWebRTC | BSD-3-Clause |

aiortc 的音频抗弱网能力弱（无 NACK、无 PLC、固定 16 包抖动缓冲），项目页结论是适合开发和一对一场景；这是实现进度，不是 WebRTC 协议的限制。

## 项目页之间的口径差异

- **Paraformer 的 AISHELL-1 CER 两页不一致**：[funasr](../04-projects/asr/funasr.md) 页引 FunASR README，Paraformer-zh 为 1.95%；[fireredasr](../04-projects/asr/fireredasr.md) 页引 FireRedASR README 的对比表，Paraformer-Large aishell1 为 1.68。两者都是各自仓库自报，测试条件待确认。
- **"SenseVoice 7.81" 出现在两页、出处不同**：funasr 页引 `docs/benchmark/historical_asr_zh.md` 的 GPU 行，sensevoice 页引 `runtime/llama.cpp/BENCHMARKS.md` 的 184 条长音频 fp32 结果。是否同一次测量待确认。另外 FireRedASR 表里的 SenseVoice-L（1.6B）是未开源的大模型，不是 SenseVoiceSmall。
- **中文 ASR 指标名**：Qwen3-Omni 页写 WER，Step-Audio2 和 FireRedASR 页写 CER，三页都报了 WenetSpeech net / meeting（4.69 / 5.89、4.82 / 4.87、4.88 / 4.76），横比前需要确认指标和归一化口径。
- **TTS 首包**：计时起止点各不相同，见第二节"首包口径不一致"。
- **TTS 质量**：VoxCPM 与 IndexTTS 两家 README 对同一组模型的排序相反，见第二节。
- **端点 / 尾部静音**：FunASR 800 ms（C++ 默认，以模型配置为准）、sensevoice-server 约 1850 ms（推断，分档）、sherpa-onnx 出字后 1.2 s（按解码帧计），定义各不相同。

## 相关

- 项目页：见各表第一列
- 机制页：[判停](../03-mechanisms/turn-detection.md)、[首音优化](../03-mechanisms/first-audio.md)、[打断与截断](../03-mechanisms/interruption.md)、[音频前处理](../03-mechanisms/audio-preprocessing.md)、[评测](../03-mechanisms/evaluation.md)
- 对比页：[framework-matrix](framework-matrix.md)、[decision-guide](decision-guide.md)
