# livekit-agents

> 仓库：https://github.com/livekit/agents
> 许可：Apache-2.0（Krisp 降噪插件为商业授权）（见仓库 LICENSE 文件）
> 分析基于：commit e7e7783（`livekit-agents` 1.8.3）
> 状态：草稿
> 最后更新：2026-10-02

下文路径默认相对 `livekit-agents/livekit/agents/`，插件路径相对 `livekit-plugins/`。标"推断"的是按代码路径推出、没有跑过的结论。

## 定位

LiveKit Agents 是一个 Python 语音 agent 运行时。agent 进程以参与者身份加入 LiveKit 房间（WebRTC），在服务端完成"听、判停、生成、说、被打断"的全过程。它面向两类人：一是要在 LiveKit 上做语音或电话（SIP）agent 的团队；二是想要一个已经把判停、打断、工具回合、实时模型封装都做好的运行时，自己只写 Agent 逻辑的人。

和同类项目比，它有三个特点：

- **一个会话、两条执行路径**。级联（STT + 文本 `llm.LLM` + TTS）和实时模型（`llm.RealtimeModel`，例如 OpenAI Realtime、Gemini Live）共用同一套 `AgentSession` / `Agent` / 工具 / 打断 API。第三类 `llm.DuplexModel`（GPT-Live）在进入会话时被 `DuplexRealtimeAdapter` 包成 RealtimeModel（`voice/agent_session.py:AgentSession.__init__`），所以运行期实际只有两条路径。
- **以"发言"为调度单位**。核心是 `SpeechHandle` 加优先队列：同一时刻只授权一个发言出声，工具、打断、`say()` 都围绕它展开。[pipecat](pipecat.md) 以帧管道为核心，两者在这一点上分道。
- **组件最全**。仓库里有 75 个 `livekit-plugins-*` 包（STT / TTS / LLM / 实时模型 / 数字人 / 降噪），自带语义判停模型、自适应打断模型、按用户停顿习惯学习的动态端点，还有进程池式的部署模型和测试、judge、仿真工具。

代价是绑定 LiveKit 的传输和部署形态（房间、worker 派单），以及代码量大：仅 `voice/agent_activity.py` 就有五千多行。

## 整体架构

```
LiveKit 房间 (WebRTC)
  │ 用户音轨
  ▼
room_io 输入（_ParticipantAudioInputStream：降噪帧处理器 / AGC）
  │
  ▼
AgentSession ── 当前 Agent 对应一个 AgentActivity
  │
  ├─ AudioRecognition：VAD → STT → 判停（turn detector + endpointing）
  │       ├─ on_preemptive_generation（转写未定稿，先跑 LLM，仅级联）
  │       └─ on_end_of_turn ──► _user_turn_completed_impl ──► _generate_reply
  │
  ├─ 级联：_pipeline_reply_task
  │       llm_node 流 ─┬─ 文本 → tts_node → 播放
  │                    └─ function call → perform_tool_executions（与播放并行）
  │       播完且工具完成 → 需要回复则再起一轮
  │
  ├─ 实时模型：_realtime_reply_task / _realtime_generation_task
  │       RealtimeSession 的 message_stream（音频或文本）→ 播放
  │       function_stream → 工具 → update_chat_ctx 回传 → 需要回复则 generate_reply
  │
  └─ 调度：_schedule_speech 优先队列 + _scheduling_task，同一时刻只授权一个 SpeechHandle
  ▼
room_io 输出（AudioSource）→ 房间
```

**核心抽象**

| 抽象 | 位置 | 作用 |
|---|---|---|
| `AgentSession` | `voice/agent_session.py:AgentSession` | 会话对象。持有 stt / vad / llm / tts / turn_detection 等组件和会话级选项，对外暴露 `say()`、`generate_reply()`、`interrupt()`、`run()` |
| `Agent` / `AgentTask` | `voice/agent.py:Agent`、`AgentTask` | 一个"角色"：instructions、tools、chat_ctx，以及可覆盖的 `stt_node` / `llm_node` / `tts_node` / `realtime_audio_output_node` / `on_user_turn_completed`。工具返回另一个 Agent 即为 handoff |
| `AgentActivity` | `voice/agent_activity.py:AgentActivity` | 一个 Agent 在运行期的实体：语音调度队列、打断、两条回复路径。实现 `RecognitionHooks` |
| `AudioRecognition` | `voice/audio_recognition.py:AudioRecognition` | VAD、STT、判停的汇合点，产出 end-of-turn 和抢先生成事件 |
| `SpeechHandle` | `voice/speech_handle.py:SpeechHandle` | "一次发言"的句柄。优先级 LOW 0 / NORMAL 5 / HIGH 10，可打断、可 await；一次发言可以跨多步（LLM → 工具 → LLM） |
| `RunContext` | `voice/events.py:RunContext` | 工具函数拿到的上下文：`update()`、`with_filler()`、`wait_for_playout()`、`disallow_interruptions()`、`foreground()` |
| `_ToolExecutor` | `voice/tool_executor.py:_ToolExecutor` | 在途工具的生命周期：去重、取消、异步回帖 |
| `RealtimeModel` / `RealtimeSession` / `RealtimeCapabilities` | `llm/realtime.py` | 实时模型的抽象接口和能力声明 |

**两条路径怎么统一。** 分流点在 `voice/agent_activity.py:AgentActivity._generate_reply`：`self.llm` 是 `llm.RealtimeModel` 就走 `_realtime_reply_task`，是 `llm.LLM` 就走 `_pipeline_reply_task`。两条路径共享以下东西：

- `SpeechHandle` 调度；
- 工具执行（`voice/generation.py:perform_tool_executions`）；
- 播放与"已播文本"回写（`voice/generation.py:forward_generation`）；
- 本地 `ChatContext`。

差异集中在两处。一是上下文怎么送到模型：级联每轮把完整 chat_ctx 发给 LLM；实时模型要和上游会话同步。二是谁来判停（见下文）。实时模型之间的行为差异不靠按厂商名分支，而是由插件声明 `RealtimeCapabilities`（`llm/realtime.py:RealtimeCapabilities`），框架据此决定行为。主要字段：`message_truncation`、`turn_detection`、`can_disable_turn_detection`、`auto_tool_reply_generation`、`audio_output`、`mutable_chat_context`、`mutable_instructions`、`mutable_tools`、`per_response_tool_choice`、`supports_say`。

**进程模型**：worker 管一个预热进程池，一个进程只跑一个 job（会话），`ipc/job_proc_executor.py:ProcJobExecutor.launch_job` 里有 "process already has a running job" 的检查。接单依据是 CPU 负载：`worker.py:_DefaultLoadCalc` 做滑动平均，生产默认阈值 `load_threshold=0.7`（`worker.py:ServerOptions`）。`num_idle_processes` 个空闲进程会提前跑好 `prewarm_fnc`（典型是加载 VAD 模型）。turn detector 这类本地推理放在 worker 级的共享推理进程里，同一 worker 的所有 job 共用。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 会话启动、组件装配 | `voice/agent_session.py:AgentSession.start` | DuplexModel 在 `__init__` 里被包成 RealtimeModel |
| 回复分流 | `voice/agent_activity.py:AgentActivity._generate_reply` | 按 `self.llm` 类型选级联或实时路径 |
| 语音调度 | `voice/agent_activity.py:AgentActivity._schedule_speech` / `_scheduling_task` | 优先队列加串行授权 |
| 判停 | `voice/audio_recognition.py:AudioRecognition._run_eou_detection` | VAD 静音后查 turn detector，按概率在 min/max delay 之间选择等待时长 |
| 端点参数 | `voice/turn.py:EndpointingOptions`、`voice/endpointing.py:DynamicEndpointing` | 默认 min 0.5 s / max 3.0 s；接流式检测器时为 0.3 / 2.5 |
| 语义判停模型 | `inference/eot/detector.py:TurnDetector` | 新版，只用音频；旧插件 `livekit-plugins-turn-detector` 已废弃 |
| 实时模型是否保留服务端判停 | `voice/agent_activity.py:AgentActivity._resolve_rt_turn_detection_enabled` | 会话开始时决定一次 |
| 抢先生成 | `voice/agent_activity.py:AgentActivity.on_preemptive_generation`；复用判断在 `_user_turn_completed_impl` | 仅级联 |
| 级联一轮 | `voice/agent_activity.py:AgentActivity._pipeline_reply_task_impl` | LLM 流、TTS、工具并行 |
| 实时一轮 | `voice/agent_activity.py:AgentActivity._realtime_generation_task_impl` | 消息流播放，工具结果回传，截断 |
| 半级联 | `_realtime_generation_task_impl` 内的 `_process_one_message` | 消息无音频模态且配了 TTS 时，把文本 tee 给 TTS |
| 打断入口 | `voice/agent_activity.py:AgentActivity.interrupt`、`_interrupt_by_audio_activity`、`_on_input_speech_started` | 本地 VAD / 自适应模型 / 服务端 speech_started 三个来源 |
| 播放位置与已播文本 | `voice/generation.py:forward_generation` | 记录 `playback_position` 和 `synchronized_transcript` |
| 工具执行 | `voice/generation.py:perform_tool_executions`、`make_tool_output` | `reply_required` 默认值在 `make_tool_output` 里定 |
| 异步工具 | `voice/events.py:RunContext.update`、`voice/tool_executor.py:_ToolExecutor._deliver_reply` | 首次 update 即收口，结果等空闲再回帖 |
| 工具里说话 | `voice/agent_session.py:AgentSession.say` → `AgentActivity.say` | 可传预合成音频，可选不进上下文 |
| OpenAI Realtime 封装 | `llm/_realtime/openai.py:RealtimeSession` | 插件 `livekit-plugins-openai/.../realtime/realtime_model.py` 只是转导出 |
| 上下文 diff | `llm/utils.py:compute_chat_ctx_diff` | 按 id 做 LCS |
| 重连 | `llm/_realtime/openai.py:RealtimeSession._main_task` 内的 `_reconnect` | 重发配置，并逐条重放服务端镜像 |
| 跨模型兜底 | `llm/realtime_fallback_adapter.py:RealtimeModelFallbackAdapter`（`_swap`、`restart_session`） | 用 agent 上下文重建新会话 |
| 测试 | `voice/agent_session.py:AgentSession.run` → `voice/run_result.py:RunResult` | 文本驱动，带断言和 judge |
| 整段 judge | `evals/judge.py`、`evals/evaluation.py:JudgeGroup` | 内置任务完成、工具使用、简洁等维度 |

## 对各机制的回答

### [判停](../../03-mechanisms/turn-detection.md)

**级联的判停分三层。**

1. **VAD**：默认 Silero 插件，或新的 `inference.VAD`。它决定"用户什么时候开始、停止说话"。
2. **turn detector**（可选）：VAD 判出说话结束后，`AudioRecognition._run_eou_detection` 向检测器要一个 end-of-turn 概率。概率低于该语言的 `unlikely_threshold` 时，等待时间从 `min_delay` 换成 `max_delay`（`voice/audio_recognition.py` 中 `_bounce_eou_task`）。等待期间用户再开口，这次等待作废。
3. **端点等待**：`voice/turn.py:EndpointingOptions` 默认 `min_delay=0.5` / `max_delay=3.0`，接流式检测器时为 0.3 / 2.5。`mode="dynamic"` 时由 `voice/endpointing.py:DynamicEndpointing` 学习两类停顿，把 min_delay 往上抬，上限是 max_delay：一是同一用户两段话之间的停顿，二是刚判完停就被用户打断时的停顿。

配置了 STT 时，EOU 检测要等拿到 final 转写才跑：`AudioRecognition._run_eou_detection` 在还没有 final 转写（`_audio_transcript` 为空）时直接返回，等 final 转写到达且用户已不在说话时再以 `trigger="stt"` 触发。手动 `commit_user_turn()` 时，若最近 0.5 s 内没收到 final，会等 final 最多 `transcript_timeout=2.0` s，超时就把 interim 当 final 用；音频输入已断开时还会先向 STT 灌 `stt_flush_duration=2.0` s 的静音冲刷（`voice/audio_recognition.py:AudioRecognition._commit_user_turn`，默认值见 `AgentSession.commit_user_turn`）。

**turn detector 有新旧两代。**

- **旧插件** `livekit-plugins-turn-detector`（`english` / `multilingual`）是文本模型，输入最近 6 轮对话、最多 128 token（`turn_detector/base.py` 的 `MAX_HISTORY_TURNS` / `MAX_HISTORY_TOKENS`），用 ONNX 在共享推理进程里跑。`__init__.py` 里已经标为 deprecated。
- **新版** `inference/eot/detector.py:TurnDetector`。`v1` 走云端，`v1-mini` 走本地。本地版常驻约 108 MB，只吃最后 1.2 s 的 16 kHz 音频（`inference/eot/transports.py:_CLIENT_BUFFER_SECONDS`）。各语言阈值见 `inference/eot/languages.py:LOCAL_LANGUAGES`，其中 `zh: 0.355`。
- 两代的训练数据和中文准确率，仓库里都没有，待确认。

**`turn_detection` 模式**：`voice/turn.py:TurnDetectionMode` 取 `"stt"`、`"vad"`、`"realtime_llm"`、`"manual"`，或者一个检测器对象。不传时按 realtime_llm → vad → stt → manual 的顺序自动选。`"manual"` 配合 `AgentSession.commit_user_turn()` / `clear_user_turn()`，可以做按键说话。

**实时模型能不能外置判停：能，但取决于上游。**

- 判定在 `AgentActivity._resolve_rt_turn_detection_enabled`，会话开始时决定一次，运行中不能切换。
- 只有上游声明了 `can_disable_turn_detection`，并且用户显式配了 VAD 加 turn detector（或 `"vad"` 模式、自适应打断），或者用了 `"manual"`，才会关掉服务端判停。关掉的方式是建会话时传 `turn_detection_disabled=True`（`llm/_realtime/openai.py:RealtimeModel.session`）。
- OpenAI 插件还支持一种"半关闭"：`create_response=False` 时，服务端仍然切段，但不自动回复，框架按客户端判停处理（`llm/_realtime/openai.py:_server_turn_taking_enabled`）。
- Gemini、AWS、Ultravox 等上游声明不能关。对这类上游，客户端配置的检测器会被忽略，只打一条 warning（`AgentActivity._validate_turn_detection`）。

### [打断与截断](../../03-mechanisms/interruption.md)

**触发**

- 级联：本地 VAD 检测到用户说话，走 `AgentActivity._interrupt_by_audio_activity`。
- 实时模型开着服务端判停时：由服务端的 `input_speech_started` 触发，走 `AgentActivity._on_input_speech_started`。这时本地的音频打断被直接忽略（`_interrupt_by_audio_activity` 里检查 `_rt_turn_detection_enabled`）。

**门槛**（`voice/turn.py:InterruptionOptions`）

- `min_duration=0.5`。
- 可选 `min_words`：有 STT 时，要转写出足够的词才算打断。
- `false_interruption_timeout=2.0` 加 `resume_false_interruption=True`：打断分两段。输出支持暂停（`audio_output.can_pause`，判定见 `AgentActivity._pause_enabled`）时，声学打断只 `pause()` 播放、把发言记进 `_paused_speech`，不真正打断（`_interrupt_by_audio_activity`）；拿到 final 转写或提交了要回复的用户回合，才由 `_cancel_speech_pause` 调 `handle.interrupt(source="user_turn")` 真正打断（`on_final_transcript`、`_user_turn_completed_task`）。用户说完（`on_end_of_speech`）起一个 2.0 s 计时器，到时没形成回合就 `resume()` 并发 `agent_false_interruption` 事件；若此时判停还在进行，等判停结果出来再决定（`_start_false_interruption_timer`，均在 `voice/agent_activity.py`）。输出不支持暂停时，声学打断直接 `interrupt()`。
- `mode="adaptive"`：用 `inference/interruption.py:AdaptiveInterruptionDetector` 模型区分"真打断"和附和、重叠说话。
- `aec_warmup_duration`：默认 3.0 s（`voice/agent_session.py:_DEFAULT_AEC_WARMUP_DURATION`）。会话第一次进入 speaking 时起一个一次性计时器（`AgentSession._update_agent_state`），期间忽略音频打断，等回声消除收敛（`_interrupt_by_audio_activity` 开头的检查）。

**不可打断**

- `allow_interruptions=False`，或在工具里调 `RunContext.disallow_interruptions()`。
- 实时模型开着服务端判停时，`allow_interruptions=False` 不成立，因为服务端会自己取消回复。`AgentActivity._generate_reply` 遇到这种情况直接抛错。

**按播放位置截断**

- `voice/generation.py:forward_generation` 在打断时先 `clear_buffer()`，再等播放器回报。确实播出过本段帧的，记为 `partial`，同时记录 `playback_position` 和 `synchronized_transcript`；一帧都没播出的记为 `skipped`。
- `synchronized_transcript` 是估算值：文本和音频同步器按语速匀速推进，中文按单字切（`voice/transcription/synchronizer.py`）。
- 级联：本地 assistant 消息只写已转发的文本，并标 `interrupted=True`。
- 实时模型：在 `_realtime_generation_task_impl` 里，`partial` 的消息调用 `rt_session.truncate(message_id, audio_end_ms=播放位置, audio_transcript=已播文本)`，前提是上游声明了 `message_truncation`。
  - OpenAI 实现（`llm/_realtime/openai.py:RealtimeSession.truncate`）：音频模态发 `conversation.item.truncate`，播放位置为 0 时改发 `conversation.item.delete`；文本模态则用已播文本"删了再建"这条 item。
  - `skipped` 的消息用 `update_chat_ctx(agent._chat_ctx)` 从服务端删掉。
- 待确认（推断）：OpenAI 插件没有处理 `conversation.item.truncated` 事件，服务端镜像 `RemoteChatContext` 里仍是完整文本。重连时重放的是镜像，被截断的回复可能以全文形式回到上游。
- Gemini、AWS 不支持截断，只在本地改写 assistant 消息，上游保留完整回复。

### [首音优化](../../03-mechanisms/first-audio.md)

- **抢先生成（preemptive generation），默认开启，仅级联。**
  - 触发：STT 给出 preflight 转写，或者 final 转写到达时判停还没完成，都会进 `AgentActivity.on_preemptive_generation`。它用当前转写调 `_generate_reply(..., schedule_speech=False)`：LLM 立即开跑，但发言不入队。
  - 采用：判停后在 `_user_turn_completed_impl` 里检查四个条件，转写等价（`_transcripts_equivalent`，忽略大小写和标点）、`on_user_turn_completed` 之后的 chat_ctx 等价、tools 相同、tool_choice 相同。全部满足就直接调度这次抢跑的结果，否则丢弃。
  - 默认只抢跑 LLM，`preemptive_tts=True` 时连 TTS 也抢跑。工具在发言被授权之后才执行（`_pipeline_reply_task_impl` 注释 "start to execute tools (only after play())"），所以抢跑没有副作用。
  - 上限：`max_speech_duration=10 s`，`max_retries=3`（`voice/turn.py:PreemptiveGenerationOptions`）。
  - 实时模型路径直接 return：`on_preemptive_generation` 里有 `not isinstance(self.llm, llm.LLM)` 的检查。
- 判停侧的手段：接流式检测器时 `min_delay` 降到 0.3 s；新检测器在静音满 200 ms 时就预取一次预测。
- TTS 流式输入，LLM 文本边出边送 `tts_node`。预热进程池加 `prewarm_fnc` 省掉模型加载时间。
- 首音的实测数字仓库里没有，待确认。

### [工具回合](../../03-mechanisms/tool-calls.md)

**执行时机**

- 级联：LLM 流里的 function call 交给 `perform_tool_executions`，和前导语播放并行。
- 实时模型：`function_stream` 和消息播放同时启动。
- 一轮带工具时，发言播完就把调度器让出来，自己转入后台等工具。所以工具执行期间，`say()` 和填充语可以正常出声。

**执行期间出声**

- `session.say(text, audio=..., add_to_chat_ctx=...)`（`voice/agent_session.py:AgentSession.say`）。以 NORMAL 优先级入队；可以传预合成的音频帧；`add_to_chat_ctx=False` 表示不进上下文。
  - 例外：上游声明了 `supports_say`（Phonic、Ultravox）时，`say()` 交给模型自己念，`add_to_chat_ctx=False` 会被忽略（`AgentActivity.say`）。
- `RunContext.with_filler(source, delay, ...)`：会话连续空闲满 `delay` 秒才播填充语（`voice/filler_scheduler.py:_FillerScheduler`）。
- 进度事件 `tool_execution_updated` 给前端显示用，不进上下文。

**要不要再生成**

- 由 `FunctionCallOutput.reply_required` 决定。工具返回非 None 时默认 True，返回 None 或纯 handoff 时为 False（`voice/generation.py:make_tool_output`）。
- 三种关掉的办法：
  1. 返回 `ToolResult(output, reply_required=False)`（`llm/tool_context.py:ToolResult`）；
  2. `raise StopResponse()`；
  3. 在 `function_tools_executed` 事件里调 `cancel_tool_reply()`。
- 结果本身总会写进上下文，也总会回传给模型，调用一定收口。
- "工具里 `say()` 模板化结果，再 `reply_required=False`"就是"跳过再生成、直接念"的写法。

**异步工具**

- 工具第一次调用 `await ctx.update(msg)` 时，msg 立即作为这个 call_id 的正式 output 返回，原调用当场收口（`voice/events.py:RunContext.update`）。
- 工具继续在后台跑。后续的 update 和最终结果合成新的 FunctionCall / Output 对，call_id 为 `{id}_update_{n}` 和 `{id}_final`。
- 这些结果由 `_ToolExecutor._deliver_reply` 等会话空闲（没有在播的发言、用户没在说话、判停不在进行）后合并，调一次 `generate_reply(tool_choice="none")`。指令有两套：结果仍在上下文末尾时用"自然总结"；后面已有新内容时用"已经说过就输出空"，用来去掉重复播报。
- 配套：`on_duplicate` 去重策略；可取消的后台任务（内置工具 `lk_agents_cancel_task`）；级联下给"仍在执行的工具"注入占位结果，防止模型重复调用（`voice/generation.py:_inject_running_tool_calls`）。

**实时模型的差异**

- 回传工具结果之前，先等队列里所有发言播完。`_realtime_generation_task_impl` 注释说明原因："most realtime models don't support generating multiple responses at the same time"。
- 结果通过 `update_chat_ctx` 推到上游。
- 不会自动回复的上游（OpenAI Realtime）：需要回复时由框架调 `generate_reply`。
- 会自动回复的上游：由插件把 `reply_required=False` 翻译成"静默收口"，例如 Gemini 的 `FunctionResponseScheduling.SILENT`。GPT-Live 做不到静默收口。注意这里的 GPT-Live 指 `GPTLiveModel`（`gpt-live-1`，连 `/live/sessions`，后台委派 Responses 模型），不是 OpenAI Realtime。它的上游并不是收到 `function_call_output` 就自己续答：插件用 `response.item.create` 把结果交给后台后，**由插件自己**在所有调用都有结果时发 `response.create`（`_append_items` → `_maybe_continue_response`）；之所以不能省掉这一步，是因为上游没有不续答就关闭调用的办法，没收口的调用会卡住之后所有工具调用，而续答出来的内容语音模型会自己说出来（同文件 `_append_items` 里的 warning 和文件开头的注释）。能力位 `auto_tool_reply_generation=True` 的意思是"插件负责续答，框架不必再调 `generate_reply`"。
- `max_tool_steps` 默认 3，只在级联路径生效（`voice/agent_session.py:AgentSession.__init__`）；实时路径有没有等价限制，待确认。

**委派**：GPT-Live 插件的 `GPTLiveModel(delegation="responses" | "client")`（`livekit-plugins-openai/.../realtime/gpt_live_model.py`）是"前台语音模型委派后台模型"的一等实现。OpenAI Realtime、Gemini 没有内置的委派模式。

### [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)

**条目映射**：本地 `ChatContext` 条目的 id 直接用作上游 item id。用户转写回来时用服务端的 `item_id` 建本地消息（`AgentActivity._on_input_audio_transcription_completed`）；服务端自己产生的条目通过 `remote_item_added` 补进本地（`_on_remote_item_added`）。

**同步（可变上游，OpenAI）**

- 插件维护服务端镜像 `llm/remote_chat_context.py:RemoteChatContext`，只在收到服务端确认事件时更新。
- `RealtimeSession.update_chat_ctx` 用 `llm/utils.py:compute_chat_ctx_diff` 做 LCS diff：不在 LCS 里的旧条目删除，新条目按 `previous_item_id` 插入，同 id 但文本不同的转成 delete + create。逐条发送，统一等待 5 s 确认。
- instructions、tools 的更新走 `session.update`。
- 框架触发同步的时机：agent 激活或 handoff、用户代码 `update_chat_ctx`、工具结果回传、打断后删除没播过的条目。

**同步（不可变或部分可变上游）**

- Gemini 不支持删除条目，改工具表就整条连接重建。
- AWS Nova Sonic 的历史只在建会话时一次性带入，本地超过 40 条就截断（`livekit-plugins-aws/.../experimental/realtime/realtime_model.py:RealtimeSession.initialize_streams`，`MAX_MESSAGES = 40`）。
- GPT-Live 的 instructions、历史在会话开始后不可变。

**断线重连**

- OpenAI：`RealtimeSession._main_task` 内的 `_reconnect` 重发 `session.update` 和 tools，再把服务端镜像逐条 `conversation.item.create` 重放。重放时排除了全部 function call 和 output（`exclude_function_call=True`）。正在等待的 `generate_reply` 以 `RealtimeError` 失败，然后发出 `session_reconnected`。`voice/` 下没有监听这个事件，被打断的那一轮不会自动重答。`max_session_duration`（默认 20 分钟）到点后走同一套重连。
- Gemini：有 `session_resumption` handle 时靠 handle 恢复，否则把本地上下文打包成一条 `send_client_content` 重发。收到 1007（上下文耗尽）判为致命，不重试，避免把同样超长的上下文再重放一遍（`livekit-plugins-google/livekit/plugins/google/realtime/realtime_api.py:RealtimeSession._main_task`）。
- 跨模型兜底：`llm/realtime_fallback_adapter.py:RealtimeModelFallbackAdapter._swap` 先打断，再用 agent 的 chat_ctx（用户实际听到的内容）建新会话，之前正在说话的话会自动重新生成（`regenerate_on_swap`）。`restart_session()` 可以在同一模型上主动换一个新会话。

**错误分级**

- OpenAI 只把配额、鉴权、计费四类错误列为致命（`llm/_realtime/openai.py:RealtimeSession._is_fatal_error`），其余都按可恢复处理。
- `AgentSession._on_error` 对 STT / LLM / TTS 按 `max_unrecoverable_errors=3` 计数。实时模型的不可恢复错误不计数，直接关闭会话。

**长会话**：框架层没有自动截断或摘要。可用的零件有 `llm/chat_context.py:ChatContext.truncate` 和 `ChatContext._summarize`；后者要求文本 LLM。

### [音频前处理](../../03-mechanisms/audio-preprocessing.md)

- 服务端挂点在 `voice/room_io/_input.py:_ParticipantAudioInputStream`：`noise_cancellation` 帧处理器（比如 Krisp 插件 `livekit-plugins-krisp`，商业授权）放在 VAD 之前；AGC 用 `rtc.AudioProcessingModule(auto_gain_control=True)`，默认是开的：没显式设 `auto_gain_control` 时，只要没配降噪，或降噪是按参与者选择的 callable，就开 AGC；直接配了降噪处理器时默认关（`voice/room_io/room_io.py:131-136`，说明见 `voice/room_io/types.py` 的 `auto_gain_control`）。
- Silero 插件只在推理支路做降采样，送 STT 的主路不受影响。
- 服务端不做 AEC。只有本地 console 模式能同时拿到扬声器参考信号，开了 APM 回声消除（`cli/_legacy.py`）。房间模式下 AEC 由客户端 WebRTC 完成，服务端只用 `aec_warmup_duration` 屏蔽开头一段时间的打断。

### [评测](../../03-mechanisms/evaluation.md)

- **单轮测试**：`AgentSession.run(user_input=...)` 进程内驱动，不连房间。注入的是文本，`input_modality` 只是一个标签。返回的 `voice/run_result.py:RunResult` 支持按事件断言，包括 `ChatMessageAssert`、`FunctionCallAssert`、`AgentHandoffAssert` 和 `ChatMessageAssert.judge(llm, intent=...)` 这种 LLM 判分。`mock_tools` 可以替换工具实现。示例见 `examples/drive_thru/test_agent.py`。
- **整段对话 judge**：`evals/judge.py` 内置任务完成、handoff、准确性、工具使用、安全、相关、连贯、简洁等 judge；`evals/evaluation.py:JudgeGroup` 负责组合。
- **仿真**：`simulation.py:SimulationContext` 接 LiveKit 的仿真调度，有文本和音频两种模式，裁决是"模拟用户的 LLM 判定"和"用户自己的检查"两者取与。
- `testing.py:fake_job_context` 让 agent 不依赖 worker 运行。
- 缺口：没有音频层的噪声注入，也没有判停准确率或首音延迟的基准。运行期指标（`metrics/`、OTel）可以作为线上评测的数据源。

## 取舍与局限

- **统一抽象的代价**：两条路径共享 API，但很多行为只在一侧成立，比如抢先生成、`max_tool_steps`、占位结果只在级联，截断只在支持它的上游。读文档时容易以为"都支持"，要看 `RealtimeCapabilities` 才能确定。
- **实时模型下受制于上游**：能不能外置判停、能不能截断、能不能静默收口、历史能不能改，全看上游声明。框架提供的是"能力位 + 降级"，没法越过上游的限制。上游不能关判停时，本地 turn detector 直接失效。
- **工具结果回传偏保守**：实时路径要等所有排队发言播完才回传。同步工具在打断后会拖住下一轮，最长约 5 s（`voice/speech_handle.py:INTERRUPTION_TIMEOUT`）。
- **上下文一致性有缝**：`say()` 走 TTS 时只写本地上下文，要等下一次全量 diff 才同步到上游。重连重放的是服务端镜像而不是"用户听到的"，被截断的回复和工具调用都可能丢或失真（推断）。
- **往音频会话里灌文本历史有风险**：框架专门有一条报错，说文本上下文同步到上游后，模型可能改用文本回复（`_process_one_message` 里的 "Text message received from Realtime API with audio modality"）。
- **部署绑定 LiveKit**：输入输出、派单都围绕 LiveKit 房间和 worker 协议。一会话一进程，隔离好，但每个会话的资源开销比协程模型高。
- **半级联可用但没有专门优化**：OpenAI 插件只能在 audio 和 text 中二选一（`llm/_realtime/openai.py`，注释 "they do not support both text and audio modalities"）。`modalities=["text"]` 加会话 TTS 时，框架把文本流 tee 给 TTS，模型的话和 `say()` 用同一个音色。这条路径和级联共用 `perform_tts_inference`，没有额外的首音优化，延迟数字待确认。

## 相关

- 机制页：[判停](../../03-mechanisms/turn-detection.md)、[打断与截断](../../03-mechanisms/interruption.md)、[首音优化](../../03-mechanisms/first-audio.md)、[工具回合](../../03-mechanisms/tool-calls.md)、[会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)、[音频前处理](../../03-mechanisms/audio-preprocessing.md)、[评测](../../03-mechanisms/evaluation.md)
- 项目页：[pipecat](pipecat.md)、[ten-framework](ten-framework.md)、[openai-realtime-agents](openai-realtime-agents.md)、[qwen-audio-agent](qwen-audio-agent.md)
- 架构页：[级联](../../02-architectures/cascade.md)、[半级联](../../02-architectures/half-cascade.md)、[S2S](../../02-architectures/s2s.md)、[发言权](../../02-architectures/floor-control.md)、[状态与上下文](../../02-architectures/state-and-context.md)
- 对比页：[framework-matrix](../../05-comparison/framework-matrix.md)、[decision-guide](../../05-comparison/decision-guide.md)
