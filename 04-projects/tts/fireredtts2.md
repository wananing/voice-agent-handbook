# FireRedTTS-2

> 仓库：https://github.com/FireRedTeam/FireRedTTS2
> 分析基于：commit 404f3f6
> 状态：草稿
> 最后更新：2026-10-01

## 定位

小红书 FireRed 团队的长对话 TTS，标题是 "Towards Long Conversational Speech Generation for Podcast and Chatbot"。主打**多说话人对话生成**：一次最多 4 个说话人、3 分钟对话，说话人切换稳定，韵律能感知上下文（`README.md:25-31`）。支持中、英、日、韩、法、德、俄，可零样本跨语言和中英混说克隆；另有随机音色生成，用来造 ASR / 对话数据。

和同类项目的区别：架构借鉴了 Moshi / Sesame CSM 的 dual-transformer（`README.md:285`），在文本-语音交错序列上逐帧生成，**结构上就是逐帧流式**，每块 80 ms。README 自述 L20 上首包低至 140 ms（`README.md:30`）。

在 voice agent 里，它适合作为"细粒度流式、首包型"的 TTS 节点；它的对话模式也是少数能把前几轮音频作为上下文、跨轮保持韵律连贯的开源 TTS。

## 整体架构

```
text ─► clean_text / split_text ─┐
prompt_wav(16 kHz) + prompt_text ─► RedCodec.encode（每次调用都重新编码）
                                  ▼
  context = [参考 Segment] (+ 对话模式下：之前每一轮生成的音频，重采样到 16 kHz)
                                  ▼
  dual-transformer LLM（Qwen2.5-1.5B 文本 tokenizer）
     generate_frame：每步出 1 帧（多码本），12.5 Hz = 80 ms
                                  ▼  滞后 1 帧
  RedCodec.decode_one_token（有状态 codec_cache，Vocos 式声学解码）─► 24 kHz 音频块
```

**模型结构**：dual-transformer 自回归模型在文本-语音交错序列上逐帧预测多码本 token，再由 12.5 Hz 流式 codec 解码成音频（`README.md:30`，`fireredtts2/fireredtts2.py`）。codec 的声学解码器参考了 Xcodec2 的 Vocos 方案（`README.md:289`）。输出 24 kHz（`README.md` 示例的 `torchaudio.save(..., 24000)`），参考音频按 16 kHz 读入（`fireredtts2.py:19,72`）。属于 "LLM + codec" 一类，没有 flow matching。

**进程 / 线程模型**：单进程同步 generator，没有后台线程。KV cache 按 batch 1 建（`self._model.setup_caches(1)`，`fireredtts2.py:54`），每次 `generate` 先 `reset_caches()`。所以一个实例同一时刻只能服务一个请求。

**流式能力**：

- 音频流式输出：支持，使用 `FireRedTTS2_Stream` 类。`generate` 每生成一帧，就解码上一帧并 yield（`fireredtts2.py:428-446`），README 写 "Each audio chunk is 0.08 seconds, except the first (a little shorter) and last (a little longer)"。独白克隆模式在攒到 3 帧之后才开始解码（`generate_monologue` 里的 `len(tokens) > 2`）。
- 文本流式输入：不支持。每次调用要给出整段（整轮）文本。对话模式下 `text_list` 要一次给全，再逐轮生成。
- 首包量级：README 写 "first-packet latency as low as 140ms"（L20），没有说明口径。仓库里唯一的计时在非流式 `generate_single` 里，从参考 token 化**之后**算起，到生成第 2 帧为止，**不包括**参考音频编码和音频解码（`fireredtts2.py:233-260`）。端到端首包待确认。
- 首包随上下文增长：`generate` 每次都对 context 里的每个 Segment 重新跑 codec 编码再 prefill（`fireredtts2.py:394-401`）。对话模式会把之前每一轮生成的音频放回 context（`generate_dialogue`，`fireredtts2.py:513-566`），`max_seq_len=3100` 帧（约 4 分钟）是硬上限。所以首包会随轮数增长（推断）。

**音色**：

- 零样本克隆：参考音频 + 参考文本，两者必须同时给（`generate_monologue` 的 assert，`fireredtts2.py:572-574`）。独白模式把参考文本和目标文本拼成一段（`prompt_text[:-1] + "," + text`）。参考音频的推荐长度仓库里没写，待确认。
- 预置音色：没有；不给参考时是**随机音色**（`generate_monologue` 的 else 分支）。
- 跨语言：README 称支持跨语言和 code-switching 的零样本克隆。
- 多说话人：`[S1]`–`[S4]` 标签；对话模式用 `llm_posttrain.pt`，独白模式用 `llm_pretrain.pt`（`fireredtts2.py:25-28`）。
- 情感 / 语速：没有控制参数，只有 `temperature`、`topk`。
- 微调：有完整的微调教程（`bin/finetune_example/tutorial.md`）。

**中文**：

- 只有符号清洗，没有 TN：`clean_text` 把中文标点换成英文标点、去 emoji、合并连续的句点和逗号（`fireredtts2/utils/spliter.py:59-73`）；`split_text` 切长文本时会保护小数点（`protect_float`）。数字、单位不转写，多音字没有处理机制。
- 中英混读：README 称支持 code-switching，效果待确认。

**推理部署**：

- 形态：只有 Python API 和 Gradio（`gradio_demo.py`），没有 HTTP / gRPC 服务端。
- 加速：没有 vLLM / TensorRT 路径；支持 bf16（`use_bf16=True`）。
- 显存：fp32 约 14 GB，bf16 约 9 GB（`README.md:67`）。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 非流式入口 | `fireredtts2/fireredtts2.py:FireRedTTS2` | `generate_monologue` / `generate_dialogue`，整段返回 |
| 流式入口 | `fireredtts2/fireredtts2.py:FireRedTTS2_Stream` | 同名方法改成 generator，逐 80 ms 块 yield |
| 逐帧生成 | `fireredtts2/fireredtts2.py:FireRedTTS2_Stream.generate` | reset cache → 编码 context → 逐帧 `generate_frame` → 滞后 1 帧解码 |
| 对话上下文 | `fireredtts2/fireredtts2.py:FireRedTTS2_Stream.generate_dialogue` | 每轮生成后把音频重采样到 16 kHz，追加进 context |
| 参考编码 | `fireredtts2/fireredtts2.py:FireRedTTS2._tokenize_audio` | 每次调用重新编码 |
| 流式 codec 解码 | `fireredtts2/codec/model.py:RedCodecInfer` | `decode_one_token` 带 `codec_cache` |
| 文本清洗 / 切分 | `fireredtts2/utils/spliter.py:clean_text`、`split_text` | 符号归一，长文本切段 |
| 首帧计时 | `fireredtts2/fireredtts2.py:FireRedTTS2.generate_single` | 打印 "first pack duration"，口径偏窄 |

## 对各机制的回答

- 判停：不涉及。
- 打断与截断：`FireRedTTS2_Stream` 是同步 generator，调用方停止迭代后就不再生成下一帧，可以按 80 ms 粒度中止（推断，`fireredtts2/fireredtts2.py:FireRedTTS2_Stream.generate`）。由于每块时长固定，调用方可以按已播放块数换算已播放时长；但没有字级时间戳。
- 首音优化：逐帧（80 ms）流式，首块只需要 prefill 加两三帧（`FireRedTTS2_Stream.generate`）。但每次调用都要重新编码参考和历史音频，需要自己缓存参考 token，否则首包会被 prefill 前的编码拖慢。
- 工具回合：不涉及。
- 会话恢复与上下文同步：对话模式把之前各轮生成的音频作为 context 带入下一轮（`generate_dialogue`），这是 TTS 侧的跨轮韵律上下文。但状态只存在一次 `generate_dialogue` 调用内部，没有持久化或恢复接口，长度受 `max_seq_len=3100` 帧限制。
- 音频前处理：不涉及。
- 评测：README 只定性说独白和对话测试中 WER / CER 低、相似度高（`README.md:31`），没有给表，也没有评测脚本。第三方数字：VoxCPM README 的 Seed-TTS test-ZH CER 1.14 / SIM 73.6；IndexTTS README 的 CV3-Eval zh WER 8.22 / SS 68.10。两者差别很大，待确认。

## 取舍与局限

- **延迟优先**：逐帧 80 ms 流式、bf16 下 9 GB，结构上首包门槛低；代价是没有服务端、没有并发、每次重新编码参考。
- **质量存疑**：中文第三方评测结果分歧大，在 CV3-Eval 上明显落后于 CosyVoice3 / IndexTTS2.5（IndexTTS README 自报）。
- **可控性弱**：没有情感、语速控制，只能靠参考音频和采样参数。
- **对话模式的代价**：跨轮韵律连贯，但首包随轮数增长、总长有上限。voice agent 里更现实的用法是独白模式 + 固定参考 + 自己缓存参考 token。
- **许可与免责**：代码 Apache-2.0（`LICENSE`），权重许可仓库里没写，待确认。README 免责声明写零样本克隆 "solely for academic research purposes"（`README.md:292`），和 Apache 条款的关系需要法务判断。
- **维护状态**：Roadmap 里 2025/10 的两项还没完成（`README.md:78-81`）。
- **适合的链路**：级联链路里对首包敏感的 TTS 节点，或用来验证"细粒度流式能把首包压到多低"的对照组。
- **接入建议**（推断，未实测）：
  1. 用 `FireRedTTS2_Stream` 的独白模式，固定一个参考；
  2. 改造 `generate`，把参考 Segment 的 token 缓存起来，避免每次重新编码；
  3. 上游负责 TN 和短语切分；
  4. 多路并发需要多个实例，或者自己改 batch。

## 相关

- 机制页：[首音优化](../../03-mechanisms/first-audio.md)、[打断与截断](../../03-mechanisms/interruption.md)、[会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)、[评测](../../03-mechanisms/evaluation.md)
- 对比页：[模型对比矩阵](../../05-comparison/model-matrix.md)
