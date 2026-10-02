# Qwen3-Omni

> 仓库：https://github.com/QwenLM/Qwen3-Omni
> 分析基于：commit e423585
> 状态：草稿
> 最后更新：2026-10-01

## 定位

阿里通义的全模态基础模型，能输入文本、图像、音频、视频，输出文本和语音。README 称其为 "natively end-to-end multilingual omni-modal foundation models"。开源的三个型号都是 30B-A3B MoE（`README.md` "Model Description and Download" 一节）：

| 型号 | 组件 | 输出 |
|---|---|---|
| Instruct | Thinker + Talker | 文本 + 语音 |
| Thinking | 只有 Thinker，带思维链 | 只出文本 |
| Captioner | 从 Instruct 微调的音频细粒度描述模型 | 只出文本 |

放在语音 agent 的语境里，按四个问题定位：

| 问题 | 结论 |
|---|---|
| 能否实时流式 | **开源仓库里不能**。README 宣称 "Real-time Audio/Video Interaction: Low-latency streaming with natural turn-taking"，但实时交互只在 Qwen Chat 和 DashScope Realtime API（Qwen3-Omni-Flash）上提供。仓库里所有调用都是"整段音频进、整段生成"；本地 Web demo 是录音后点 Submit |
| 是否出语音 | Instruct 能出（Talker，3 个内置音色 Ethan / Chelsie / Aiden）。但 `vllm serve` 只支持 Thinker，所以走 vLLM 服务时只出文本 |
| 是否支持 function call | 能，但仓库只有"在系统提示里手写 Qwen/Hermes `<tools>` 模板"的单次触发示例（`cookbooks/audio_function_call.ipynb`）。没有 `tools` 参数用法，也没有工具结果回注和续答的示例 |
| 中文能力 | 强。语音输入支持 19 种语言（含中文、粤语），语音输出支持 10 种（含中文），见 `README.md` "Multilingual"。中文 ASR 数字见下文 |

和 [Step-Audio2](step-audio2.md) 相比：Qwen3-Omni 理解能力更强，但更重（30B 总参数，BF16 需要 80 GB 级显卡）；工具调用不是原生接口。和 [ultravox](ultravox.md) 相比：它自带语音输出，中文明显更强。

## 整体架构

README 的描述：MoE 架构的 Thinker–Talker 设计，AuT 音频编码器预训练，multi-codebook 设计用来压低延迟。仓库只给了一张架构图（`README.md` "Model Architecture"），**不含模型实现代码**。建模代码在 transformers（README 要求 ≥ 5.2.0）和 vLLM / vLLM-Omni 里。

```
音频/图像/视频/文本 ──► Thinker (MoE LLM) ──► 文本 token ──► (Talker) ──► 语音 24 kHz
                         ▲                                  ▲
               chat messages（system / 历史 / 当前多模态）     speaker = Ethan|Chelsie|Aiden
```

**推理接口有三种形态**：

1. **transformers**：`Qwen3OmniMoeForConditionalGeneration.generate()`。
   - `return_audio=False` 时只出文本；
   - `model.disable_talker()` 卸掉 Talker，README 称能省约 10 GB 显存；
   - 用 `speaker=` 选择音色；
   - 一次返回 `text_ids, audio`，不是流式。
2. **vLLM 离线**：`LLM(...).generate()`，`multi_modal_data` 里放音频（`web_demo.py:predict`），输出文本。
3. **vLLM 服务**：`vllm serve Qwen/Qwen3-Omni-30B-A3B-Instruct ...`，OpenAI 兼容接口，音频作为 `audio_url` content part。README 原话："vLLM serve for Qwen3-Omni currently only supports the thinker model"。README 还推荐 vLLM-Omni，但仓库里没有它的用法细节。

`vllm serve` 的请求就是标准 chat messages，音频和图像、文本并列放在 user content 里（摘自 `README.md` "vLLM Serve Usage"）：

```json
{"messages": [
  {"role": "system", "content": "You are a helpful assistant."},
  {"role": "user", "content": [
    {"type": "audio_url", "audio_url": {"url": "https://.../cough.wav"}},
    {"type": "text", "text": "What can you see and hear? Answer in one sentence."}
  ]}
]}
```

辅助工具是 `qwen-omni-utils` 的 `process_mm_info`，它把 messages 里的音频、图像、视频（支持 URL、base64、本地路径）读成张量。

## 关键代码路径

仓库只有 demo 和 cookbook，没有模型实现，所以下表是"使用入口"。

| 功能 | 入口 | 说明 |
|---|---|---|
| 本地 Web demo | `web_demo.py:_launch_demo` / `predict` | Gradio；transformers 路径能出语音，vLLM 路径只出文本；强制 `VLLM_USE_V1=0` |
| 多轮历史组装 | `web_demo.py:format_history` | 每轮重发全部历史（含过去的原始音频）；超过 5 个音频回合（`AUDIO_TURN_LIMIT = 5`）就删掉最早的整个回合 |
| 录音提交 | `web_demo.py` 中的 `Submit (提交)` 按钮 | 没有 VAD，没有流式输入 |
| 音频描述 demo | `web_demo_captioner.py` | Captioner 型号 |
| 工具调用示例 | `cookbooks/audio_function_call.ipynb` | 系统提示里写 `<tools>` 模板；中文语音输入，输出两个并行的 `<tool_call>` |
| ASR 示例 | `cookbooks/speech_recognition.ipynb` | 用提示词驱动转写 |
| 推荐的语音助手系统提示 | `README.md` "Prompt for Audio-Visual Interaction" | 短句、口语化、不用符号、不描述动作、跟随用户语言、听不清就反问 |
| 运行环境 | `docker/` | 官方镜像，含 transformers 和 vLLM |

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：不涉及。开源仓库没有 VAD 或判停代码，一次请求处理一段完整音频，由调用方决定何时提交（`web_demo.py` 是手动 Submit）。云端 Realtime 的判停不在本仓库里。
- [打断与截断](../../03-mechanisms/interruption.md)：不涉及。模型本身是请求-响应式；打断由上层取消请求、停止播放来完成。demo 的 Stop 按钮只是取消 Gradio 事件。
- [首音优化](../../03-mechanisms/first-audio.md)：
  - README 把 "multi-codebook design that drives latency to a minimum" 列为架构特性，但开源推理路径都是非流式 `generate`，Talker 的流式出音在仓库里没有用法。待确认。
  - 只出文本时，README 建议 `return_audio=False`，"resulting in faster text responses"。
  - 仓库里没有 TTFT 数字。cookbook 的 vLLM 日志里有输出吞吐（例如 `audio_function_call.ipynb` 约 96 tok/s，GPU 型号未标），不能当首包延迟用。
- [工具回合](../../03-mechanisms/tool-calls.md)：靠 Thinker 继承的 Qwen 工具格式，在系统提示里手写 `<tools>` / `<tool_call>` 模板，从文本输出里解析（`cookbooks/audio_function_call.ipynb`）。以下几点仓库都没有覆盖，待确认：
  - `tools` 参数能否直接使用；
  - `vllm serve` 能否用 hermes 解析器；
  - 工具结果如何回注、如何续答；
  - 带工具时 Talker 的行为。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：模型无状态，上下文就是每次请求的 chat messages，调用方自己持有。
  - demo 的策略是"保留原始音频历史，超过 5 个音频回合就整轮删除"（`web_demo.py:format_history`）。删除会改变前缀，前缀缓存会失效。
  - 模型不产出用户转写。想把历史存成文本，需要额外调一次转写（README 的 ASR 提示示例），或者另跑 ASR。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：不涉及。仓库没有降噪或 AEC；音频经 `process_mm_info` 读入后直接进模型。
- [评测](../../03-mechanisms/evaluation.md)：README 有大量 benchmark 表（ASR、音频理解、语音对话、视觉等），但仓库不含评测代码。中文 ASR（WER，越低越好，摘自 `README.md` "EN & ZH ASR" 表，Qwen3-Omni-30B-A3B-Instruct 一列）：
  - WenetSpeech net / meeting：4.69 / 5.89
  - CV15-zh：4.31
  - Fleurs-zh：2.20
  - README 自述 "Reaches SOTA on 22 of 36 audio/video benchmarks and open-source SOTA on 32 of 36"。

## 作为 S2S 上游的实用约束

- **形态是"请求-响应"，不是实时会话**。开源权重要放进实时语音 agent，只能当"整段音频进、文本（或语音）出"的一步。判停、打断、播放都得由外层框架负责，结构上更接近[半级联](../../02-architectures/half-cascade.md)，而不是 Realtime 式的 S2S。
- **出语音的路径受限**：
  - Talker 只能经 transformers 使用。README 说 transformers 跑 MoE "can be very slow"。
  - vLLM 服务只有 Thinker。
  - 实际部署常见的组合是"vLLM 跑 Thinker 出文本 + 外接流式 TTS"。
- **显存**：README 只给了视频输入的最低显存。BF16 + FlashAttention 2、15 s 视频：Instruct 78.85 GB，Thinking 68.74 GB。纯音频短句没有数字，待确认。README 的 `vllm serve` 示例有 `-tp 1` 和 `-tp 4` 两种。仓库没有官方量化版本，待确认。
- **工具调用不是开箱即用**：要自己写模板、解析 `<tool_call>`、设计回注格式。
- **语音输出只有 3 个内置音色**，不支持声音克隆。
- **选 Instruct 还是 Thinking**：Thinking 会先输出思维链，首 token 更晚。实时对话一般用 Instruct；如果只要文本，就加 `disable_talker()`。
- **系统提示**：README 推荐的那段语音助手提示原本是为了让 Talker 念得顺，接外部 TTS 时同样适用。
- **许可证**：代码是 Apache 2.0（`LICENSE`）；权重许可证仓库里没写，待确认（需看 HF 模型卡）。

## 取舍与局限

- 理解能力和多语种覆盖是这一组开源模型里最强的，但体量也最大（30B 总参数 / 3B 激活）。MoE 解码快，显存门槛却高。
- 开源侧没有实时流式能力，没有流式输入，也没有流式语音输出的用法。"实时交互"是云服务的能力，不是开源仓库的能力。
- 工具调用、上下文管理、判停全部交给调用方，灵活但工作量大。
- 不含模型代码，内部机制只能依据 README 和技术报告；本页没有读技术报告。

## 相关

- 机制页：[判停](../../03-mechanisms/turn-detection.md)、[打断与截断](../../03-mechanisms/interruption.md)、[首音优化](../../03-mechanisms/first-audio.md)、[工具回合](../../03-mechanisms/tool-calls.md)、[会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)、[音频前处理](../../03-mechanisms/audio-preprocessing.md)、[评测](../../03-mechanisms/evaluation.md)
- 项目页：[step-audio2](step-audio2.md)、[ultravox](ultravox.md)
- 架构页：[半级联](../../02-architectures/half-cascade.md)、[S2S](../../02-architectures/s2s.md)
- 对比页：[model-matrix](../../05-comparison/model-matrix.md)
