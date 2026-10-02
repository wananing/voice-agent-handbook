# Ultravox

> 仓库：https://github.com/fixie-ai/ultravox
> 许可：代码 MIT；权重随底座，待确认（见仓库 LICENSE 文件）
> 分析基于：commit 69ddc63
> 状态：草稿
> 最后更新：2026-10-01

## 定位

Ultravox 是 Fixie 开源的**语音进、文本出**的多模态 LLM。做法是在任意开源权重的文本 LLM 前面接一个音频编码器和投影层，把音频直接映射到 LLM 的 embedding 空间，省掉单独的 ASR。

README 原话："Ultravox currently takes in audio and emits streaming text"。它**不出语音**，要配合外部 TTS 使用。

这一点决定了它在语音 agent 里的位置：它是[半级联](../../02-architectures/half-cascade.md)里"ASR + LLM"那一段的合体，和 S2S 没有关系。上游的判停、下游的 TTS、打断、工具执行、历史管理全部留在调用方，模型只负责"一段音频加文本上下文，流式吐出文本"。半级联架构天然适合它，调用方保留了级联方案的全部控制点，同时省掉了 ASR 定稿这一步。

按四个问题定位：

| 问题 | 结论 |
|---|---|
| 能否实时流式 | **输出文本流式，输入整段**。`infer_stream` 用 `TextIteratorStreamer` 逐 token 返回（`ultravox/inference/infer.py:LocalInference.infer_stream`）。音频编码默认非因果，要整段提交；块因果的 Whisper 编码只出现在实验配置里（`training/configs/streaming_tinyllama.yaml` 的 `audio_latency_block_size: 100`） |
| 是否出语音 | 不出。README 说未来会训练输出语音 token，当前版本没有 |
| 是否支持 function call | 本仓库没有任何工具调用代码。理论上继承底座 LLM 的工具能力（接口就是 chat messages），但没有验证，待确认。托管的 Ultravox Realtime 支持工具，那是闭源服务，不在本仓库 |
| 中文能力 | 有中文训练数据：v0.6 的 Qwen3-32B 配置包含 `wenetspeech-continuation` 和 `wenetspeech-transcription`；验证集包含 CoVoST2 zh↔en。但仓库没有中文对话质量的数字，待确认 |

底座方面：README 说默认模型基于 Llama 3.3 70B，另有 8B 版本，训练过 Llama 3、Mistral、Gemma 系列。仓库里还有 Qwen3-32B、Gemma3-27B 的 v0.6 训练配置（`ultravox/training/configs/`）。哪些版本在 HF 上发布了权重，以 HF 页面为准，待确认。

和 [Qwen3-Omni](qwen3-omni.md)、[Step-Audio2](step-audio2.md) 相比：

- Ultravox 只出文本，中文较弱，训练目标是"表现得像读了转写稿"；
- 但它是**纯开源训练栈**（含数据处理、训练、评测），而且可以换任意底座 LLM。

## 整体架构

```
音频 16 kHz ──► Whisper encoder（v0.3 起的配置为 whisper-large-v3-turbo；v0.5 后可加 LoRA）
                 │ 50 Hz 特征
                 ▼
          StackAudioFrames（每 8 帧拼接，stack_factor=8）
                 ▼
          UltravoxProjector：RMSNorm → Linear → SwiGLU → Linear (→ RMSNorm)
                 │ 替换 prompt 中 <|audio|> 位置的 embedding
                 ▼
   system / 历史（文本）+ 当前用户消息（含音频）──► 文本 LLM（冻结）──► 流式文本
```

- **模型**：`ultravox/model/ultravox_model.py:UltravoxModel`，包含音频塔 `_create_audio_tower`、投影 `UltravoxProjector` 和语言模型 `_create_language_model`。
- **训练**：README 写的是 LLM 和编码器冻结，只训练投影；v0.5 之后的配置给编码器加了 LoRA（例如 `v0.6_config_qwen3_32b.yaml` 的 `audio_model_lora_config: r: 8`）。
- **损失是 KL 蒸馏**（`training/configs/meta_config.yaml` 的 `loss_function: "KL_Divergence"`；`UltravoxModel._compute_kl_loss`）：
  - 老师：同一个 LLM 读转写文本得到的分布；
  - 学生：读音频得到的分布。
  - 也就是说，训练目标是"听音频的行为 = 读转写稿的行为"。README 自己也把理解副语言（语气、情绪）写成 "In the future" 的愿景。
- **抗噪**：训练数据里有 MUSAN 纯噪声样本，目标输出是特殊串 `((noise))`（`ultravox/data/configs/musan.py`、`ultravox/data/types.py`）。模型学过"听不清就不回应"。

**推理接口**有三种：

1. **本地 transformers**：`ultravox/inference/ultravox_infer.py:UltravoxInference`（继承 `infer.LocalInference`），提供 `infer` / `infer_batch` / `infer_stream`。
   - `conversation_mode=True` 时保留 KV cache 做多轮；
   - 重建历史消息时，过去的音频位置被替换成 `eos_token × audio_token_len`（`LocalInference._build_past_messages`）。
2. **vLLM 的 OpenAI 兼容接口**：`ultravox/tools/infer_api.py:OpenAIInference`。
   - 音频作为最后一条 user 消息里的 `audio_url` content part（data URI），前面的 system 和历史都是普通文本（`_build_messages`）；
   - vLLM 启动方式见 `ultravox/inference/run_vllm_inference.py:start_vllm`。
3. **托管服务**：README 指向 BaseTen 和 Ultravox Realtime 托管 API，不在本仓库。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 模型前向与音频嵌入 | `ultravox/model/ultravox_model.py:UltravoxModel.forward` / `_prepare_audio_embeds` | 把投影后的音频 embedding 填进 `<|audio|>` 位置 |
| 投影层 | `ultravox/model/ultravox_model.py:UltravoxProjector`、`StackAudioFrames` | 8 帧拼接 + SwiGLU MLP |
| 块因果编码（实验） | `ultravox/model/ultravox_model.py:ModifiedWhisperEncoder.init_latency_mask` | `audio_latency_block_size` 非空时启用 |
| KL 蒸馏损失 | `ultravox/model/ultravox_model.py:UltravoxModel._compute_kl_loss` | 文本老师、音频学生 |
| 本地流式推理 | `ultravox/inference/infer.py:LocalInference.infer_stream` | `TextIteratorStreamer`；会话模式下深拷贝 KV 后续用 |
| 多轮历史重建 | `ultravox/inference/infer.py:LocalInference._build_past_messages` | 过去的音频变成 `eos_token` 占位 |
| OpenAI 接口客户端 | `ultravox/tools/infer_api.py:OpenAIInference._build_messages` | `audio_url` content part |
| vLLM 启动 | `ultravox/inference/run_vllm_inference.py:start_vllm` | OpenAI api_server；没有开启工具解析参数 |
| 语音 demo | `ultravox/tools/gradio_voice.py:make_demo` | `gradio_webrtc` 的 `ReplyOnPause` 判停后整段送模型，流式显示文本 |
| 训练配置 | `ultravox/training/configs/*.yaml` | v0.3 到 v0.6，含 Llama / Gemma / Qwen3 底座 |
| 评测 | `ultravox/evaluation/eval.py`、`evaluation/configs/*.yaml` | CoVoST2、FLEURS、CommonVoice、VoiceBench 等；GPT 评分器 `gpt_eval_*.py` |

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：模型不涉及，由调用方决定何时提交一段音频。
  - 仓库的语音 demo 用的是 `gradio_webrtc` 的 `ReplyOnPause`：停顿后把整段音频交给 `infer_stream`（`ultravox/tools/gradio_voice.py:make_demo`）。
  - 这正是半级联的分工：判停在模型外，可以接 VAD、语义判停或按键。
- [打断与截断](../../03-mechanisms/interruption.md)：不涉及。由上层取消文本流和 TTS。
  - 上下文截断也由调用方处理：助手历史是普通文本，截到已播放的位置写回即可。
- [首音优化](../../03-mechanisms/first-audio.md)：
  - 模型侧的收益是省掉 ASR 定稿：判停后直接"编码整段音频 + 预填充 + 生成"。
  - 音频 token 率约为 Whisper 50 Hz ÷ 8 ≈ 6.25 token/s（由 `stack_factor=8` 推算），预填充开销小。
  - README 只说 "respond much more quickly than systems that combine separate ASR and LLM"，**没有任何延迟数字**，待确认。
  - 文本流式输出可以直接接流式 TTS。
- [工具回合](../../03-mechanisms/tool-calls.md)：本仓库不涉及。
  - 证据：`infer.py` 的 `apply_chat_template` 没有传 `tools`；`start_vllm` 没有开启工具解析。
  - 理论上，vLLM 部署时打开底座 LLM 对应的工具解析器，就能沿用底座的工具能力。KL 蒸馏让音频输入下的行为贴近文本输入，但工具调用保真度没有评测，待确认。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：请求无状态（OpenAI 接口），历史由调用方以 chat messages 持有，系统提示、摘要、截断都在调用方手里。
  - 注意：**模型不产出用户转写**。本地会话模式靠 KV cache 记住过去的音频；KV 一旦丢失，过去的用户话语只剩 `eos_token` 占位（`_build_past_messages`）。
  - 要可恢复的文本历史，就得另跑 ASR，或者额外请求一次转写。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：推理侧不涉及（没有降噪或 AEC）。
  - 训练侧有数据增强（`ultravox/data/aug/`），并且用 MUSAN 噪声样本训练模型对纯噪声输出 `((noise))`。
  - 这个能力在调用方可以当作"误触发过滤"，但抗噪程度没有对比数据，待确认。
- [评测](../../03-mechanisms/evaluation.md)：有完整的评测框架（`ultravox/evaluation/`）：
  - 数据集：ASR / 翻译用 CoVoST2、FLEURS、CommonVoice；对话能力用 VoiceBench（`eval_config_voicebench.yaml` 含 bbh、ifeval、wildvoice、alpacaeval、commoneval 等）。
  - 指标：BLEU / WER 等字符串指标，以及 GPT 评分器。
  - README 没有给出 benchmark 数字，结果需自己跑或看 HF 模型卡。

## 作为 S2S 上游的实用约束

- **它不是 S2S**。作为语音 agent 的"大脑"时，前面要有判停，后面要接 TTS。好处是这条链路上判停、打断、上下文、工具、音色全部可控，和[级联](../../02-architectures/cascade.md)的可控性一样，又少了 ASR 一跳。
- **音频必须整段提交**，适合"判停后提交"的轮次式交互，不适合边听边想的全双工。
- **历史要转成文本**才能做持久化、摘要和前缀缓存，而模型不给用户转写，需要额外的 ASR 或转写请求。
- **副语言信息**：受训练目标所限，"听出情绪"不应作为选型理由（README 自己写的是未来目标）。
- **资源**：随底座变化。8B 底座单卡即可；README 默认的 70B 需要多卡。`start_vllm` 写的是 `--tensor-parallel-size=8`、`--max-model-len=8192`。
- **训练成本低**，可以自己适配：README 说 v0.4 在 8×H100 上训练 2–3 小时、14K 步。想加语言，准备 `audio` + `continuation` 数据重训投影；想加知识，直接微调底座 LLM，已有投影仍可用。

## 取舍与局限

- 最轻的"语音理解端"：保留级联的全部控制点，又去掉 ASR 定稿的延迟和误差传递。代价是要外接 TTS，并且失去用户转写。
- KL 蒸馏让它"像读转写稿一样回答"。这对工具和指令遵循保真是利好，但不带来超出转写的声学理解。
- 中文和儿童语音没有公开评测；推理延迟没有公开数字。
- 仓库是训练和研究栈，没有实时服务端，也没有工具调用示例；生产实时能力在闭源托管服务里。

## 相关

- 机制页：[判停](../../03-mechanisms/turn-detection.md)、[打断与截断](../../03-mechanisms/interruption.md)、[首音优化](../../03-mechanisms/first-audio.md)、[工具回合](../../03-mechanisms/tool-calls.md)、[会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)、[音频前处理](../../03-mechanisms/audio-preprocessing.md)、[评测](../../03-mechanisms/evaluation.md)
- 项目页：[qwen3-omni](qwen3-omni.md)、[step-audio2](step-audio2.md)、[unmute](../full-duplex/unmute.md)（同样是"文本 LLM 居中"的可控路线）
- 架构页：[半级联](../../02-architectures/half-cascade.md)、[级联](../../02-architectures/cascade.md)
- 对比页：[model-matrix](../../05-comparison/model-matrix.md)
