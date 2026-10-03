# Step-Audio2

> 仓库：https://github.com/stepfun-ai/Step-Audio2
> 许可：代码 Apache-2.0（见仓库 LICENSE 文件）；权重 Apache-2.0
> 分析基于：commit 76e272b
> 状态：草稿
> 最后更新：2026-10-01

## 定位

阶跃星辰的端到端音频大模型。README 称其为 "end-to-end multi-modal large language model designed for industry-strength audio understanding and speech conversation"。

**开源的只有 mini 系列**，三个权重都是 Apache 2.0（`README.md` "Introduction"）：

- Step-Audio 2 mini
- Step-Audio 2 mini Base
- Step-Audio 2 mini Think

README 里很多表格分数最高的 "Step-Audio 2" 是闭源的完整版，只在 StepFun 云端提供，读数字时要分清。mini 以 Qwen2-Audio（编码器）和 Qwen2.5-7B 初始化（`README.md` "Acknowledgements"）。

放在语音 agent 的语境里，按四个问题定位：

| 问题 | 结论 |
|---|---|
| 能否实时流式 | **输出可以流式，输入不行**。vLLM 服务支持 SSE 流式返回文本和音频 token（`examples-vllm-stream.py`），token2wav 也有分块流式合成（`token2wav.py:Token2wav.stream`）。但输入是整段音频，按 25 s 切块只是为了适配编码器窗口。仓库没有 VAD 或实时服务端；实时体验只在 StepFun realtime console |
| 是否出语音 | 能。同一个 LLM 交错输出文本 token 和音频 token，音频 token 交给 token2wav（CosyVoice2 风格的 flow + HiFT）合成。**逐回合可选**：助手消息为 `content: None` 时只出文本；加 `<tts_start>` 时出语音 |
| 是否支持 function call | **原生支持**。vLLM 分支带 `--tool-call-parser step_audio_2`，客户端用 OpenAI `tools` 参数；仓库有"调用 → 回注 → 续答"的完整示例（`examples-vllm.py:tool_call_test`） |
| 中文能力 | 强，还有方言和口音数据（见"评测"）。示例系统提示和工具示例都是中文 |

和 [Qwen3-Omni](qwen3-omni.md) 相比：Step-Audio2 mini 体量小（7B 级），工具调用是原生的，"出文本还是出语音"按回合切换。它的短板是推理能力：mini 的 Big Bench Audio 只有 54.8（`README.md` "Audio understanding and reasoning"），而且 vLLM 要用 stepfun 自己的分支。和 [ultravox](ultravox.md) 相比：它自带语音输出，副语言有评测，中文更强。

## 整体架构

```
用户音频 (16 kHz, 每 25 s 一块) ──► 音频编码器 + adapter ──┐
system / 历史 / 工具 schema（文本）───────────────────────┤
                                                       ▼
                          单个 LLM（交错输出）
                    ├─ 文本 token（id < 151688）──► 文本 / tool_calls
                    └─ 音频 token（id > 151695，减偏移后 < 6561）
                                  ▼
             token2wav：s3tokenizer + CAMPPlus 说话人向量（来自 prompt_wav）
                         + flow + HiFT ──► 24 kHz 波形
```

- 文本和音频 token 的分流写在 `stepaudio2.py:StepAudio2Base.__call__`：`< 151688` 是文本，`> 151695` 的减去 151696 是音频。
- token2wav 的音色由 `prompt_wav` 决定（`examples.py` 用 `assets/default_female.wav` / `default_male.wav`），可以换成任意参考音频。

**推理接口有两种**：

1. **transformers**（`stepaudio2.py:StepAudio2`）：本地 `generate`，非流式；对话格式由自定义的 `apply_chat_template` 拼接。
2. **vLLM**（推荐）：
   - 需要 stepfun 的 vLLM 分支：`Dockerfile` 基于 `vllm/vllm-openai:v0.10.1`，再安装 `stepfun-ai/vllm` 的 `step-audio2-mini` 分支。
   - 启动参数要加 `--tokenizer-mode step_audio_2`、`--audio-parser step_audio_2_tts_ta4`、`--enable-auto-tool-choice --tool-call-parser step_audio_2`。
   - 客户端 `stepaudio2vllm.py:StepAudio2` 发 `/v1/chat/completions`，音频切成 25 s 的 WAV 块后以 `input_audio` 发送。
   - 响应里语音部分放在 `tts_content.tts_text` 和 `tts_content.tts_audio`（`<audio_N>` 串）。

**对话格式是方言**，不是标准 OpenAI 角色：

- 用户角色叫 `human`；
- 工具结果角色叫 `input`，带 `tool_call_id`；
- transformers 路径下，工具 schema 放在 `role: "tool_json_schemas"` 消息里（`examples.py`）；
- 助手消息带 `eot: False` 表示续写。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| transformers 推理 | `stepaudio2.py:StepAudio2Base.__call__` / `StepAudio2.apply_chat_template` | 非流式；按 id 区间拆分文本和音频 token |
| vLLM 客户端 | `stepaudio2vllm.py:StepAudio2.stream` | SSE 流式；解析 `tts_content`；`eot` / `content: None` 决定续写还是新开一轮 |
| 输入切块 | `stepaudio2vllm.py:StepAudio2.process_content_item` | 16 kHz，每 25 s 一块 |
| 流式工具调用与流式合成 | `examples-vllm-stream.py:stream_client` | 工具参数分片到达，用最新值覆盖；每攒 25 个音频 token（加 lookahead）调一次 `token2wav.stream` |
| 工具回合完整示例 | `examples-vllm.py:tool_call_test` | 首轮带 `tools` → 读 `tool_calls` → 追加 `role: input` 结果 → 再请求一次续答 |
| 语音合成 | `token2wav.py:Token2wav.__call__` / `stream` / `set_stream_cache` | 一次性合成或分块流式合成；`mel_cache_len = 8`（160 ms）做重叠淡入淡出 |
| 思考模式 | `examples-think.py` | 历史里去掉思维链 |
| 本地 Web demo | `web_demo.py`、`web_demo_vllm.py:predict` | `gr.Audio` 录音 + Submit；历史里助手侧存 `tts_content` 或文本 |
| vLLM 镜像 | `Dockerfile` | stepfun 分支，基于 v0.10.1 |

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：不涉及。仓库没有 VAD 或判停代码，一次请求处理一段完整音频，由调用方提交（`web_demo_vllm.py` 是 Submit 按钮）。云端 realtime console 的判停不在本仓库。
- [打断与截断](../../03-mechanisms/interruption.md)：不涉及。由上层取消 HTTP 流、停止 token2wav 和播放。
  - 模型侧的一个便利：助手历史存的是 `tts_content`（文本 + 音频 token）。被打断时，调用方可以只把已播部分写回历史，但仓库没有这类示例，待确认。
- [首音优化](../../03-mechanisms/first-audio.md)：
  - 输出侧有流式路径：vLLM SSE 逐块返回音频 token，`token2wav.stream` 每 25 个 token（25 Hz 下约 1 s 音频，另加 flow 的 `pre_lookahead_len`）合成一块（`examples-vllm-stream.py:stream_client`）。
  - 仓库没有 TTFT 或首包延迟数字，待确认。
  - 只出文本时，可以跳过音频 token 的生成和 token2wav。
- [工具回合](../../03-mechanisms/tool-calls.md)：原生支持。
  - 服务端：`--tool-call-parser step_audio_2`。
  - 客户端：标准 `tools` 参数；结果以 `role: "input"` + `tool_call_id` 回注；续答由客户端再发一次请求，不会自动续答（`examples-vllm.py:tool_call_test`）。
  - 示例里客户端默认 `parallel_tool_calls: False`，流式时只处理第一个工具调用（`examples-vllm-stream.py`），并行调用待确认。
  - 示例首轮就带了 `<tts_start>`，所以模型可能先说一句话再发出工具调用；"只出文本 + 工具"的组合仓库没有示例，待确认。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：模型无状态，上下文就是请求里的 messages。
  - 示例的历史里，用户音频原样保留；助手侧，只出文本时存 `content`，出语音时存 `tts_content`（`examples-vllm.py`）。
  - 模型不产出用户转写。要文本历史，就额外发一次转写请求（`examples.py` 的 ASR 提示"请记录下你所听到的语音内容。"），或者另跑 ASR。
  - 两个仓库都没有提前缀缓存，stepfun vLLM 分支下多模态前缀缓存是否生效，待确认。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：不涉及。仓库只有重采样到 16 kHz 和切块（`utils.py:load_audio`），没有降噪或 AEC。
- [评测](../../03-mechanisms/evaluation.md)：README 有自家评测集和多张表，仓库不含评测代码。以下 mini 的数字均摘自 `README.md`：
  - 中文 ASR（CER，越低越好，"Automatic speech recognition"）：AISHELL 0.78；AISHELL-2 2.16；WenetSpeech meeting / net 4.87 / 4.82；KeSpeech phase1 3.97；中文平均 3.19；自建方言和口音集平均 9.85。
  - 副语言（StepEval-Audio-Paralinguistic，自家评测集）：平均 80.00，其中性别 100、年龄 94、情绪 82。
  - 语音对话（URO-Bench 中文 Basic 平均）：77.81（闭源完整版 83.32）。
  - 工具调用（StepEval-Audio-Toolcall）：表里**只有闭源完整版和文本基线 Qwen3-32B**，没有 mini 的数字。

## 作为 S2S 上游的实用约束

- **形态是请求-响应**。和 Qwen3-Omni 一样，开源权重只能当实时 agent 里"整段音频进、文本或语音出"的一步，判停和打断由外层负责。好处是"出文本还是出语音"每回合可选：
  - 只出文本时，就是[半级联](../../02-architectures/half-cascade.md)的理解端，外接自己的 TTS；
  - 出语音时，就是轮次式的 S2S，可以用 `prompt_wav` 换音色。克隆效果和稳定性待确认。
- **vLLM 绑定私有分支**：stepfun 分支基于 v0.10.1，带专用的 tokenizer-mode、audio-parser、tool-parser。升级和社区修复要等上游合并。用标准 OpenAI SDK 直接发 `role: tool` / `user` 能否被接受，待确认（示例都用 `human` / `input`）。
- **资源**：仓库没有显存或 GPU 要求的数字，只提到自建 docker 镜像需要 32 GiB 内存。docker 示例是单卡 `--tensor-parallel-size 1`、`--max-num-seqs 32`、`--max-model-len 16384`。7B 级 BF16 单张 24 GB 卡是否够用，待确认。
- **mini 和完整版差距**：工具调用质量没有 mini 的公开数字；推理类评测明显落后于完整版。
- **流式输入不支持**：音频必须整段提交，长音频按 25 s 切块。

## 取舍与局限

- 中文 ASR、方言、副语言和原生工具调用都在 7B 级体量下提供，是这一组里最接近"能直接接进中文语音 agent"的开源权重。
- 代价有三个：推理能力弱；工具调用质量没有 mini 的数据；vLLM 用私有分支；对话角色是方言格式。
- 不含模型实现代码（权重走 `trust_remote_code`，服务走 stepfun vLLM 分支）。模型内部机制、训练方式仓库没写，待确认。

## 相关

- 机制页：[判停](../../03-mechanisms/turn-detection.md)、[打断与截断](../../03-mechanisms/interruption.md)、[首音优化](../../03-mechanisms/first-audio.md)、[工具回合](../../03-mechanisms/tool-calls.md)、[会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)、[音频前处理](../../03-mechanisms/audio-preprocessing.md)、[评测](../../03-mechanisms/evaluation.md)
- 项目页：[qwen3-omni](qwen3-omni.md)、[ultravox](ultravox.md)、[cosyvoice](../tts/cosyvoice.md)（token2wav 组件来源）
- 架构页：[半级联](../../02-architectures/half-cascade.md)、[S2S](../../02-architectures/s2s.md)
- 对比页：[model-matrix](../../05-comparison/model-matrix.md)
