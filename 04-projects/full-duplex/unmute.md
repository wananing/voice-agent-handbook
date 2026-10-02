# Unmute

> 仓库：https://github.com/kyutai-labs/unmute
> 许可：MIT（见仓库 LICENSE 文件）
> 分析基于：commit e348e56
> 状态：草稿
> 最后更新：2026-10-01

## 定位

Unmute 是 Kyutai 的一个薄编排层，用途是"把任意文本 LLM 包成能实时对话的语音系统"。它由三部分拼成：Kyutai 流式 STT（`stt-1b-en_fr`）、任意 OpenAI 兼容的文本 LLM（compose 默认 vLLM 跑 `gemma-3-1b-it`）、Kyutai 流式 TTS（`tts-1.6b-en_fr`）。

它和 [moshi](moshi.md) 出自同一家，却是两种思路：

- **moshi**：一个模型同时听、同时说。判停和打断都在模型内部，外面看不到。
- **Unmute**：本质是级联，但 STT 和 TTS 都是流式模型，而且是"延迟流"架构（文本和音频按时间对齐，相差一个固定延迟）。在这个基础上，它用一个状态机在服务端把上行和下行同时跑起来，体验接近全双工。判停、打断、上下文都是显式代码，能换 LLM，也能改策略。

和 pipecat 这类通用框架相比，Unmute 没有 pipeline 抽象，没有工具调用，也没有历史管理。核心业务逻辑几乎全在一个 651 行的文件 `unmute/unmute_handler.py` 里。适合拿来读"一个能用的级联全双工最少需要哪些东西"，不适合直接当框架用。

语言只支持英语和法语。STT、TTS 模型都是 en_fr，系统提示词里也写死了 TTS 只支持这两种语言（`unmute/llm/system_prompt.py`）。

## 整体架构

```
浏览器 ──Opus/WebSocket(/v1/realtime, 类 OpenAI Realtime 事件)──► backend (FastAPI)
                                                               │  每个连接一个 UnmuteHandler
   receive_loop: Opus → 24 kHz PCM ──► handler.receive() ──────┤
                                                               ├─► STT (moshi-server, /api/asr-streaming)
                                                               │     会话级长连接，持续送帧（含静音）
                                                               │     ◄─ Word / Step(prs[] 停顿概率)
                                                               │
                         判停 → _generate_response()  ─────────┼─► LLM (OpenAI chat.completions, stream)
                                                               │     delta ─rechunk_to_words─► 逐词
                                                               ├─► TTS (moshi-server, /api/tts_streaming)
                                                               │     每轮新开连接，逐词送文本，轮末 Eos
                                                               │     ◄─ Audio(PCM) / Text(词 + start_s)
   emit_loop: output_queue → Opus ◄── RealtimeQueue 按实时节奏释放
```

**服务拆分**（`docker-compose.yml`）：frontend（Next.js）、backend（Python/FastAPI）、stt、tts（两者都由 moshi 仓库里的 Rust `moshi-server` 提供）、llm（vLLM）。STT 和 TTS 走 WebSocket + msgpack；LLM 走 OpenAI 流式接口。

**并发模型**：

- 每个 WebSocket 连接创建一个 `UnmuteHandler`。每个后端进程用信号量限制最多 4 个客户端（`unmute/main_websocket.py` 的 `MAX_CLIENTS = 4`）。
- 一个会话内部有两类循环：
  - 上行：`receive_loop` 解码 Opus 后调用 `handler.receive()`，把每一帧送进 STT，并在这里做判停和"声学打断"判断。
  - 下行：`emit_loop` 从 `output_queue` 取 PCM 编码成 Opus 下发。
- STT、LLM、TTS 各自是一个 `Quest`（init / run / close 三段生命周期，`unmute/quest_manager.py:Quest`），由 `QuestManager` 统一管理。往 `QuestManager` 里加一个同名 quest 时，会先取消旧的那个。

**会话状态机**只有三态，完全由 `chat_history` 的最后一条消息推出来（`unmute/llm/chatbot.py:Chatbot.conversation_state`）：

| 最后一条消息 | 状态 |
|---|---|
| assistant | `bot_speaking` |
| 非空 user | `user_speaking` |
| 其他（空 user、system） | `waiting_for_user` |

一轮回复结束时，系统会追加一条空 user 消息，表示"轮到用户了"。

**协议**：README 说协议基于 OpenAI Realtime API，但同时注明"not fully compatible yet"。实际差异：

- 客户端事件只有 `session.update` 和 `input_audio_buffer.append`，没有 `commit`、`response.create/cancel`，也没有 function call 事件（`unmute/openai_realtime_api_events.py`）。
- 音频是 base64 编码的 Opus。
- 另有一批 `unmute.*` 扩展事件。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| WebSocket 路由与收发循环 | `unmute/main_websocket.py:websocket_route` / `receive_loop` / `emit_loop` | Opus 和 PCM 互转；每连接一个 handler |
| 上行处理、判停、声学打断 | `unmute/unmute_handler.py:UnmuteHandler.receive` | 每帧送 STT；判停后灌零帧冲刷；机器人说话中停顿概率 < 0.4 视为插话 |
| 判停阈值 | `unmute/unmute_handler.py:UnmuteHandler.determine_pause` | `pause_prediction > 0.6` 且状态为 `user_speaking` |
| 停顿概率来源 | `unmute/stt/speech_to_text.py:SpeechToText.__aiter__` | 每个 80 ms 的 `Step` 消息取 `prs[2]`，经 EMA 平滑；前 12 步丢弃 |
| STT 词流、语义打断 | `unmute/unmute_handler.py:UnmuteHandler._stt_loop` | 机器人说话中出现任何新词就打断；新句首词把停顿概率清零 |
| 生成一轮回复 | `unmute/unmute_handler.py:UnmuteHandler._generate_response_task` | 先挂上 TTS quest（后台建连），再发 LLM 请求；逐词 `tts.send` |
| LLM 文本切词 | `unmute/llm/llm_utils.py:rechunk_to_words` | 按 `\s+` 切成整词再送 TTS |
| LLM 调用 | `unmute/llm/llm_utils.py:VLLMStream.chat_completion` | 只读 `delta.content` |
| TTS 建连与重试 | `unmute/unmute_handler.py:UnmuteHandler.start_up_tts` | `find_instance` 指数退避，最多 5 次 |
| TTS 下行与上下文回写 | `unmute/unmute_handler.py:UnmuteHandler._tts_loop` | 音频进 `output_queue`；TTS 回吐的词写进 assistant 消息 |
| 按实时节奏释放 | `unmute/tts/text_to_speech.py:TextToSpeech.__aiter__` + `unmute/tts/realtime_queue.py` | 音频最多提前实时 `AUDIO_BUFFER_SEC`（4 × 80 ms）释放；文本按 `start_s` 释放 |
| 打断 | `unmute/unmute_handler.py:UnmuteHandler.interrupt_bot` | 追加 `—`、清空并替换输出队列、取消 tts/llm quest |
| 长静音与结束对话 | `unmute/unmute_handler.py:detect_long_silence` / `check_for_bot_goodbye` | 用户 7 s 不说话就插入 `...`；助手以 `bye!` 结尾就关闭连接 |
| 发给 LLM 前的预处理 | `unmute/llm/llm_utils.py:preprocess_messages_for_llm` | 合并相邻同角色消息，去掉打断符等 |
| 压测与延迟分段 | `unmute/loadtest/loadtest_client.py`、`loadtest_result.py` | 统计 stt / vad / llm / tts_start 几段延迟 |

### 上行和下行怎么同时跑

上行永远不停：

- 无论机器人是否在说话，`receive()` 都会把每一帧（包括静音）送进 STT。
- STT 是会话级长连接（`start_up_stt`），它的时钟按收到的 `Step` 消息累加，每条 +80 ms。

下行是每轮一个 TTS quest：

- `_tts_loop` 持有它启动那一刻的 `output_queue` 引用。打断时 `interrupt_bot` 会把 `self.output_queue` 换成一个新队列，旧协程即使还在跑，也只能往旧队列里写，写的东西不会再被下发。
- 另外，`_generate_response_task` 和 `_tts_loop` 都记住了生成开始时的历史长度 `generating_message_i`。一旦 `chat_history` 变长（说明用户插话了），两边都会 `break`；`Chatbot.add_chat_message_delta` 也会拒绝写入。效果相当于一个不带显式编号的"代际"过滤。

### 何时开口

判停的依据是 Kyutai STT 模型附带的 extra heads：

- 配置见 `services/moshi-server/configs/stt.toml`，`extra_heads.num_heads = 4`。
- 后端取 `prs[2]` 作为停顿概率，系统提示词称之为 "semantic VAD"。`prs` 各维的具体含义仓库里没有说明，待确认。

判停之后还有一个"冲刷"步骤：

- 原因：STT 是延迟流模型，`asr_delay_in_tokens = 6`，约 0.48 s；后端把这个延迟写死为 `STT_DELAY_SEC = 0.5`。也就是说，判停那一刻，最后约 0.5 s 的话还没转写出来。
- 做法：`receive()` 一次性往 STT 灌 `ceil(0.5 / 0.08) + 1` 个零帧，等 STT 的时钟越过冲刷点后，再调用 `_generate_response()`。STT 处理零帧的速度快于实时，所以冲刷耗时短于 0.5 s，具体多少写在日志里（打印耗时和 RTF）。

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：用 STT 模型自带的停顿预测头（`prs[2]`）经 EMA 平滑，超过 0.6 判停（`unmute/unmute_handler.py:UnmuteHandler.determine_pause`）。判停后往 STT 灌零帧，冲刷掉约 0.5 s 的延迟尾巴再开始生成（`UnmuteHandler.receive`）。没有独立的 VAD 模型，也没有外部判停接口（协议里没有 `commit`）。
- [打断与截断](../../03-mechanisms/interruption.md)：
  - 两条触发路径：(1) 机器人说话期间 STT 吐出任何新词（`_stt_loop`）；(2) 机器人说话期间停顿概率降到 0.4 以下，且会话已超过 3 s（`receive`，开头 3 s 给回声消除收敛）。
  - 打断动作在 `interrupt_bot`：清空 FastRTC 队列、替换输出队列、取消 tts 和 llm 两个 quest。
  - 上下文截断：assistant 消息由 TTS 按播放时刻释放的词组成（`TextToSpeech.__aiter__` 用 `start_s` 排队），所以历史里只保留"已开始播放的词"。打断符 `—` 在送进 LLM 前会被去掉（`preprocess_messages_for_llm`），模型看不到自己被打断了。
- [首音优化](../../03-mechanisms/first-audio.md)：
  - TTS 建连和 LLM 首 token 并行：先挂 TTS quest，拿到第一个词时才 `await quest.get()`（`_generate_response_task`）。
  - LLM 输出不攒句子，按空白切成整词后逐词送 TTS（`rechunk_to_words`），依赖 Kyutai TTS "文本流入、音频流出"的能力。
  - README 给出的 TTS 延迟：单张 L40S 上全部服务同机约 750 ms，分卡部署约 450 ms。口径未说明，待确认。
  - 局限：按空格切词对中文无效。
- [工具回合](../../03-mechanisms/tool-calls.md)：不涉及。代码里没有 `tools` 或 `tool_calls`，`VLLMStream.chat_completion` 只读 `delta.content`。README 建议把工具调用藏在 LLM 服务端那一层，对 Unmute 透明。"说再见就挂断"用的是字符串匹配（`check_for_bot_goodbye`），注释里承认用 function calling 会更稳。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：
  - 会话恢复不涉及：历史只存在 `UnmuteHandler.chatbot` 的内存里，断线即丢，每个新连接从空历史开始。
  - 上下文同步：每轮把完整 `chat_history` 发给 LLM（`Chatbot.preprocessed_messages`），没有截断或摘要。compose 里 vLLM 的 `--max-model-len=1536`（`docker-compose.yml`），超长时的行为待确认。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：服务端不做降噪或 AEC，依赖浏览器 `getUserMedia` 的 `echoCancellation` 等约束（`frontend/src/app/useAudioProcessor.ts`）。服务端只做 Opus 解码，并在开头 3 s 屏蔽声学打断，以应对 AEC 尚未收敛（`UNINTERRUPTIBLE_BY_VAD_TIME_SEC`）。
- [评测](../../03-mechanisms/evaluation.md)：
  - 有压测客户端和分段延迟统计：`loadtest_result.py` 计算 stt / vad（判停 + 冲刷）/ llm / tts_start 几段延迟，以及 TTS 实时率。
  - 运行期有 Prometheus 指标（`unmute/metrics.py`，含 STT/TTS/vLLM 各自的 TTFT）和 Grafana 面板。
  - 没有对话质量或判停准确率的评测；仓库也没有公开端到端延迟数字。

## 取舍与局限

- **可控性换延迟**：判停、打断、上下文都是显式代码，能换 LLM、能改策略。代价是首音要经过"判停 + 冲刷 + LLM 首词 + TTS 首包"四段串行。相比之下，moshi 每 80 ms 就可能出声。
- **"语义 VAD"绑定 Kyutai STT**：停顿概率是这个 STT 模型特有的输出。换成别的 ASR，这套判停就没了，得另接 VAD 或判停模型。
- **只记听到的**：上下文由 TTS 回吐的、按时间戳释放的词构成，这是本项目最值得借鉴的做法。但时间以服务端为准，没有扣除网络和客户端缓冲，精度到词级，实际偏差待确认。
- **轻协议**：没有手动 commit，没有 response.cancel，没有工具事件；前端忽略 `unmute.interrupted_by_vad`，依靠服务端浅缓冲（只提前约 0.32 s 释放）让打断自然生效。
- **语言**：只支持英语和法语；`rechunk_to_words` 按空白切词，换成中文 TTS 必须重写切分。
- **扩展性**：本地默认 STT `batch_size = 1`、TTS `batch_size = 2`；生产配置（`stt-prod.toml`、`tts-prod.toml`）分别为 64 和 16。batch 满了直接拒绝新连接（`service_discovery.py`）。
- **历史无上限**，系统提示放在 `chat_history[0]`，并且可以被 `session.update` 改写，前缀缓存是否稳定要看使用方式。

### 和 moshi 原生全双工的差别

| 维度 | Unmute | moshi |
|---|---|---|
| 结构 | STT → 文本 LLM → TTS 三个模型，服务端状态机 | 单个语音语言模型，双音频流 + 文本流 |
| 何时开口 | 停顿概率 > 0.6 且冲刷完成，显式事件 | 模型每步自己决定输出填充 token 还是说话，没有事件 |
| 打断 | 显式：检测插话 → 取消 LLM/TTS → 截断上下文 | 没有打断事件；模型同时听到用户，自己决定是否停嘴 |
| 换 LLM / 加知识 | 任意 OpenAI 兼容 LLM，系统提示可改 | 不能；没有系统提示，换人设需要微调 |
| 上下文 | 文本 chat history，完全自持 | 模型 KV/上下文窗口，外部不可编辑 |
| 首音 | 判停 + 冲刷 + LLM TTFT + TTS 首包（README：TTS 一段约 450–750 ms） | README 称理论 160 ms，L4 上实测约 200 ms |
| 附和与重叠说话 | 不支持（机器人说话中只要有人声就打断） | 模型层面可以 |

## 相关

- 机制页：[判停](../../03-mechanisms/turn-detection.md)、[打断与截断](../../03-mechanisms/interruption.md)、[首音优化](../../03-mechanisms/first-audio.md)、[工具回合](../../03-mechanisms/tool-calls.md)、[会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)、[音频前处理](../../03-mechanisms/audio-preprocessing.md)、[评测](../../03-mechanisms/evaluation.md)
- 项目页：[moshi](moshi.md)
- 架构页：[全双工](../../02-architectures/full-duplex.md)、[级联](../../02-architectures/cascade.md)
- 对比页：[model-matrix](../../05-comparison/model-matrix.md)、[decision-guide](../../05-comparison/decision-guide.md)
