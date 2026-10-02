# pipecat

> 仓库：https://github.com/pipecat-ai/pipecat
> 许可：BSD-2-Clause（见仓库 LICENSE 文件）
> 分析基于：commit 422ad13（CHANGELOG 最新版本 1.12.0）
> 状态：草稿
> 最后更新：2026-10-02

下文路径默认相对 `src/pipecat/`（所以 `evals/*.py` 指评测框架 `src/pipecat/evals/`）；示例相对 `examples/`；评测场景集 `evals/release/`、`evals/turn-completion/` 和测试目录 `tests/test_*.py` 在仓库根。引用写到"文件:类.方法"一级。标"推断"的是读代码推出来、没有跑过的结论。

## 定位

Pipecat 是一个 Python 写的帧驱动 pipeline 框架，用来搭实时语音和多模态 agent。所有东西都是 `FrameProcessor`，数据和控制信号都是 `Frame`，在处理器链上按方向（下行 / 上行）逐个传递。它面向想自己拼链路的工程师：STT、LLM、TTS、实时模型（S2S）、传输都是可替换的 processor。`services/` 下有 73 个厂商目录，`transports/` 下有 Daily、SmallWebRTC、WebSocket、LiveKit 等传输，`serializers/` 下有 Twilio、Telnyx、Plivo 等电话协议。

和同类项目比：

- **调度单位是帧，不是"发言"**。打断就是一个高优先级的 `InterruptionFrame` 双向广播，每个 processor 收到后清掉自己队列里可丢的帧。对照 [livekit-agents](livekit-agents.md) 用 `SpeechHandle` 加优先队列授权出声。
- **级联和 S2S 共用一套外壳**。工具执行器（`services/llm_service.py:LLMService.run_function_calls`）和上下文聚合器（`processors/aggregators/llm_response_universal.py`）两条路径共用；S2S 服务只负责把本地 context 的变化翻译给上游。
- **判停、打断、静音都拆成可组合的策略**（`turns/`），默认判停是本地 smart-turn v3 模型。
- **自带评测框架** `pipecat eval`（`evals/`），能脚本化插话、断言时延预算。
- Flows（结构化对话流程）从 1.5.0 起并入本仓库的 `flows/`，见 [pipecat-flows](pipecat-flows.md)。

代价是抽象层多、行为分散：同一个问题（例如重连后已回传工具的登记）在各 S2S 服务里写法不一致。

## 整体架构

### 核心抽象

| 抽象 | 位置 | 作用 |
|---|---|---|
| `Frame` / `SystemFrame` / `DataFrame` / `ControlFrame` | `frames/frames.py` | 帧基类与三大类，见下节 |
| `FrameProcessor` | `processors/frame_processor.py:FrameProcessor` | 每个 processor 有一个优先输入队列和一个处理 task；`push_frame` 把帧交给相邻 processor |
| `Pipeline` | `pipeline/pipeline.py:Pipeline` | 把 processor 串成链，首尾是 `PipelineSource` / `PipelineSink` |
| `PipelineWorker` / `PipelineTask` | `pipeline/worker.py:PipelineWorker`、`PipelineTask` | 运行一条 pipeline：心跳、空闲超时、observer、RTVI、错误策略；`PipelineTask` 是它的子类 |
| `LLMContext` + `LLMContextAggregatorPair` | `processors/aggregators/llm_context.py`、`llm_response_universal.py:LLMContextAggregatorPair` | user 聚合器写用户消息并触发推理（也是判停和打断的发起者）；assistant 聚合器写助手文本、工具调用和结果 |
| `UserTurnStrategies` | `turns/user_turn_strategies.py:UserTurnStrategies` | 用户回合的 start / stop 策略列表 |
| `LLMService` | `services/llm_service.py:LLMService` | 文本 LLM 和 S2S 服务的共同基类，含工具注册与执行 |
| `TTSService` | `services/tts_service.py:TTSService` | 文本聚合（分句）、词级时间戳、音频 context 管理 |
| `BaseOutputTransport` | `transports/base_output.py:BaseOutputTransport` | 按设备节奏写音频；按 pts 释放文本帧；判定 bot 是否在说话 |

### 帧类型与优先级

- `SystemFrame`：高优先级，"不受用户打断影响"（`frames/frames.py:SystemFrame` 的 docstring）。包括 `InterruptionFrame`、`UserStarted/StoppedSpeakingFrame`、`BotStarted/StoppedSpeakingFrame`、`InputAudioRawFrame`、`FunctionCallsStartedFrame`、`FunctionCallCancelFrame`、`CancelFrame` 等。
- `DataFrame`：按序处理，打断时被丢。如 `TextFrame`、`LLMTextFrame`、`TTSAudioRawFrame`、`TTSSpeakFrame`、`LLMMessagesAppendFrame`、`FunctionCallResultFrame`。
- `ControlFrame`：按序处理，打断时被丢。如 `LLMFullResponseStart/EndFrame`、`TTSStarted/StoppedFrame`、`EndFrame`、`FunctionCallInProgressFrame`。
- `LLMContextFrame` 直接继承 `Frame`，走普通队列。

**逐帧的"可打断"标志**：`Frame.interruptible` 默认 True，类可以声明 `interruptible: bool = field(default=False, init=False)`（`frames/frames.py:Frame`）。不可打断的有 `FunctionCallResultFrame`、`FunctionCallInProgressFrame`、`EndFrame`、`StopFrame`、`LLMContextSummaryResultFrame`、`ServiceUpdateSettingsFrame` 等。旧的 `UninterruptibleFrame` mixin 自 1.11.0 起废弃。

**队列**：`processors/frame_processor.py:FrameProcessorQueue` 是三级优先队列：`StartFrame`（1）> `SystemFrame`（10）> 其他（20），同级按到达顺序。输入 task（`__input_frame_task_handler`）直接处理 SystemFrame，其余帧转进处理队列，由处理 task（`__process_frame_task_handler`）逐个处理。所以打断帧能越过所有排队的数据帧。

### 数据流

级联（以 `examples/function-calling/` 下示例为准）：

```
transport.input() → STT → user_aggregator → LLM → TTS → transport.output() → assistant_aggregator
                           │  VAD + 判停 + 打断                    │ 按 pts 释放词级 TTSTextFrame
                           │                                      ▼
                           └────── LLMContextFrame（上行，工具结果写入后触发再推理）◄──┘
```

S2S（`examples/realtime/`）：去掉 STT 和 TTS，LLM 换成 `OpenAIRealtimeLLMService` / `GeminiLiveLLMService` 等，其余不变。S2S 服务把上游音频 delta 推成 `TTSAudioRawFrame`，把上游转写推成 `TTSTextFrame`，所以输出 transport 和 assistant 聚合器的逻辑照常工作。

### 判停与回合策略

判停、打断门槛、静音都挂在 user 聚合器上（`LLMUserAggregatorParams`）：`vad_analyzer`、`user_turn_strategies`、`user_mute_strategies`、`user_idle_timeout`、`empty_user_turn`。也可以用独立的 `turns/user_turn_processor.py:UserTurnProcessor` 放在 pipeline 前部，多个聚合器共用。

| 类别 | 可选策略（`turns/`） | 说明 |
|---|---|---|
| 回合开始 | `VADUserTurnStartStrategy`、`TranscriptionUserTurnStartStrategy`（默认两者）、`MinWordsUserTurnStartStrategy`、`WakePhraseUserTurnStartStrategy`、`KrispVivaIPUserTurnStartStrategy`、`ExternalUserTurnStartStrategy` | 回合开始即打断 bot；`MinWords` 要求转写出 N 个词才算开始，用来过滤附和和噪声 |
| 回合结束 | `TurnAnalyzerUserTurnStopStrategy`（默认，smart-turn v3）、`SpeechTimeoutUserTurnStopStrategy`、`LLMTurnCompletionUserTurnStopStrategy`、`EagerUserTurnStopStrategy`、`ExternalUserTurnStopStrategy` | 可用 `deferred()` 包装成"只触发推理、不收回合" |
| 静音用户 | `AlwaysUserMuteStrategy`、`FirstSpeechUserMuteStrategy`、`FunctionCallUserMuteStrategy`、`MuteUntilFirstBotCompleteUserMuteStrategy` | `FunctionCall` 版在工具执行期间丢弃用户输入 |

默认判停链路（`TurnAnalyzerUserTurnStopStrategy`）：

1. VAD（Silero，`confidence 0.7`、`start_secs 0.2`、`stop_secs 0.2`，见 `audio/vad/vad_analyzer.py:VADParams`）判出"停止说话"。
2. `_handle_vad_user_stopped_speaking` 调 smart-turn 的 `analyze_end_of_turn()`。模型只看最后 8 s 的 16 kHz 音频（`LocalSmartTurnAnalyzerV3._predict_endpoint`），pre-speech 默认补 500 ms 加 VAD 的 `start_secs`。另有兜底：持续静音超过 `SmartTurnParams.stop_secs`（默认 3 s）直接判完（`audio/turn/smart_turn/base_smart_turn.py:BaseSmartTurn.append_audio`）。
3. 模型判"完成"后，再等 finalized 转写，或从"实际停说时刻"起算的 STT P99 时延到期（`_timeout_handler`）。所以 STT 的 `ttfs_p99_latency` 直接进入判停延迟；代码会对 `stop_secs` 不是 0.2 打警告，提示重新跑 stt-benchmark。
4. S2S 模式下聚合器把 `wait_for_transcript` 置 False，转写不在关键路径上（`LLMUserAggregator._apply_realtime_mode_strategy_mutations`）。

空转写回合（咳嗽、噪声）由 `turns/empty_user_turn.py:EmptyUserTurnConfig` 处理：打断了 bot 的空回合默认让 LLM 跑一次（请用户重说或接着讲），bot 空闲时的空回合默认不回应。`user_idle_timeout` 到期触发 `on_user_turn_idle` 事件，内容由应用自己决定（例如"还在吗"）。

### 打断如何传播

1. user 聚合器的回合控制器判定"用户开始说话"，`llm_response_universal.py:LLMUserAggregator._on_user_turn_started` 在 `enable_interruptions` 为真时调用 `broadcast_interruption()`。
2. `processors/frame_processor.py:FrameProcessor.broadcast_interruption` 先重置自己的处理 task，再向上下游各广播一个 `InterruptionFrame`。
3. 每个 processor 在 `FrameProcessor.process_frame` 里遇到 `InterruptionFrame` 就调 `_start_interruption()`：当前在处理的帧可打断，就取消并重建处理 task（队列里不可打断的帧保留）；当前帧不可打断，只清队列。
4. 各服务再做自己的清理：
   - LLM：流式推理就跑在处理 task 里（`services/openai/base_llm.py:BaseOpenAILLMService.process_frame` → `_process_context`），task 被取消，生成随之中止。`LLMService._handle_interruptions` 再取消 `cancel_on_interruption=True` 的工具 task。
   - TTS：`TTSService._handle_interruption` 清空分句器、词时间戳、待发序列，对每个音频 context 调 `on_audio_context_interrupted`（由厂商子类发 cancel）。`InterruptibleTTSService._handle_interruption` 对不支持取消的 WebSocket TTS 直接断开重连。
   - 输出 transport：`BaseOutputTransport.MediaSender.handle_interruptions` 取消时钟 task（没到时间的词级文本帧永远不会到达 assistant 聚合器），清掉音频队列里可打断的块，再发 `BotStoppedSpeakingFrame`。
   - assistant 聚合器：`LLMAssistantAggregator._handle_interruptions` 把已收到的文本写入 context，标记 `interrupted=True`。
   - S2S：OpenAI Realtime 发 `conversation.item.truncate`（见下）；Gemini Live 结束当前回复状态。

### 工具回合的帧序列

1. LLM 产出调用 → `services/llm_service.py:LLMService.run_function_calls`。投机推理中（见首音）遇到工具调用直接撤销本次投机。
2. 触发 `on_function_calls_started` 事件，并广播 `FunctionCallsStartedFrame`。
3. 每个调用一个 asyncio task（`_run_function_call`）。先广播 `FunctionCallInProgressFrame`，assistant 聚合器往 context 写 `tool_calls` 加占位：同步工具占位是 `IN_PROGRESS`，异步工具是 "started" 消息（`llm_response_universal.py:LLMAssistantAggregator._handle_function_call_in_progress`）。
4. handler 调 `params.result_callback(result, properties=...)`。每个调用有一个 `settled` 位，已收口的调用再回传会被拒绝。
5. 广播 `FunctionCallResultFrame`；聚合器替换占位，决定 `run_llm`。需要推理时上行推 `LLMContextFrame`（`_maybe_push_context_after_function_result`）。

`run_llm` 的决策（`LLMAssistantAggregator._handle_function_call_result`）：结果为空不跑；handler 在 `FunctionCallResultProperties.run_llm` 显式指定的优先；否则同一批并行调用（`group_parallel_tools=True` 时共用 `group_id`）只在最后一个完成时跑一次。即使要跑，用户正在说话也不跑；队列里还有别的结果帧就延后合并；bot 正在说话就置 `_push_context_on_bot_stopped_speaking`，等 `BotStoppedSpeakingFrame` 再推。

**同步 / 异步工具**由 `register_function(..., cancel_on_interruption=...)` 或 `@tool_options` 决定（`LLMService.register_function`）：

| | `True`（默认，同步） | `False`（异步，1.0.0 起） |
|---|---|---|
| 打断时 | 取消 task，context 写 `CANCELLED`，不触发推理（`_handle_function_call_cancel`） | 不取消 |
| 结果回来时对话已继续 | — | 以 developer 消息追加 final 结果（`processors/aggregators/async_tool_messages.py`）；判断见 `LLMAssistantAggregator._is_deferred` |
| 中间进度 | 不支持 | `is_final=False` 推中间结果（S2S 服务丢弃并报错） |
| 其他 | — | 系统指令自动拼上 `ASYNC_TOOL_INSTRUCTIONS`（`LLMService._compose_system_instruction`）；`cancellable_by_llm=True` 时自动注册取消工具 |

超时（`function_call_timeout_secs` 或每工具 `timeout_secs`）走 `LLMService._timeout_function_call`：取消后让模型告知用户。

### S2S 服务如何封装上游会话

- **本地 context 是镜像，不是同步源**。OpenAI Realtime 的 `services/openai/realtime/llm.py:OpenAIRealtimeLLMService._handle_context`：第一次收到 context 时登记已完成的工具调用并 `_create_response()`（首次会把历史灌给上游，见 `adapters/services/open_ai_realtime_adapter.py`）；之后再收到 context，只用 `_process_completed_function_calls(send_new_results=True)` diff 出新完成的工具结果，`_send_tool_result` 后再 `response.create`。其他变化不同步。Gemini Live 同理（`services/google/gemini_live/llm.py:GeminiLiveLLMService._handle_context`，注释写明"假设新 context 里只有上游已知的消息和需要告诉上游的工具结果"）。
- **半级联**：`examples/realtime/realtime-openai-text.py` 把 OpenAI Realtime 设为 `output_modalities=["text"]`，下游接 `CartesiaTTSService`；`realtime-ultravox-text.py` 同样用 Ultravox 只出文本再接本地 TTS。上游只出文本、自己的 TTS 出声这条路径是现成的，没有专门的首音优化。
- **中途追加消息**：OpenAI Realtime 的 `_handle_messages_append` 只有一行 `logger.error("!!! NEED TO IMPLEMENT MESSAGES APPEND")`。
- **本地 context 的内容来自上游转写**：聚合器检测到 realtime 服务后自动进入 realtime 模式（`LLMUserAggregator._handle_llm_service_metadata`），用户消息在助手开始回复时才写入。
- **判停归属**：服务通过 `LLMServiceMetadataFrame` 声明自己是否发回合帧。OpenAI Realtime 开着服务端 VAD 时推荐 `ExternalUserTurnStrategies`，服务端 `speech_started` 变成 `ProposedUserStartedSpeakingFrame`，由聚合器广播打断（`OpenAIRealtimeLLMService._handle_evt_speech_started`）。`turn_detection=False` 时进入手动模式：本地判停后 `input_audio_buffer.commit` + `response.create`（`_handle_user_stopped_speaking`），打断时先 clear、回放 pre-roll、`response.cancel`（`_handle_interruption`）。Gemini Live 不发回合帧，要求本地 VAD 跟踪回合；`GeminiVADParams(disabled=True)` 时用 `activity_start` / `activity_end` 手动划窗（`GeminiLiveLLMService._handle_user_started_speaking` / `_handle_user_stopped_speaking`）。
- **truncate**：`OpenAIRealtimeLLMService._truncate_current_audio_response` 发 `conversation.item.truncate`，`audio_end_ms = min(墙钟经过时间, 已收音频时长)`，起点是收到首个音频 delta 的时刻（`_handle_evt_audio_delta`）。这是"已收到"的近似，没有扣除播放缓冲。Gemini Live 代码里没有 truncate，依赖服务端 `interrupted` 信号。
- **迟到音频**：`_handle_evt_audio_delta` 记录了 `response_id`，但不和已取消的 response 比对，打断后迟到的 delta 会被当成新回复播放（推断，取决于服务端 cancel 后是否还发 delta，待确认）。

### 首音相关的环节

- **用户消息何时推给 LLM**：级联下判停一结束，`LLMUserAggregator._on_user_turn_inference_triggered` 就 `push_aggregation()` 写 context 并推 `LLMContextFrame`。`deferred` 包装的策略可以在回合收口前先触发推理。
- **抢先生成**：`EagerUserTurnStrategies` 只适用于自带回合预测的 STT。预测"说完了"时，聚合器用 eager 转写构造一份临时 context（`LLMUserAggregator._run_speculative_inference`，不改真 context），LLM 服务里的 `SpeculationGate` 扣住输出；回合确认且转写匹配（`NormalizedMatch` / `ExactMatch`）就放行，否则丢弃重跑。
- **TTS 分句**：`TextAggregationMode.SENTENCE`（默认）用 `SimpleTextAggregator` 攒到句末；docstring 估计每句多 200–300 ms。`TOKEN` 模式逐 token 送，适合支持流式输入的 TTS。另有 `skip_aggregator_types`、`text_transforms`、`text_filters`（如 `utils/text/markdown_text_filter.py`）在送 TTS 前改写文本。
- **TTS 连接**：WebSocket TTS 默认在同一 LLM 回合内复用 context id（`reuse_context_id_within_turn=True`），句子作为同一音频 context 的增量发送；不支持取消的服务打断后要重连（`InterruptibleTTSService`），下一句首音会多一次建连（推断）。
- **度量**：每个 processor 有 TTFB、processing、TTS 首音频（`FrameProcessor.process_ttfa_metrics`）等指标；`observers/user_bot_latency_observer.py` 测"用户停说 → bot 出声"，开 `enable_metrics=True` 时再按各服务 TTFB、文本聚合、函数调用耗时拆段，没有服务认领的空档也单列。仓库没有给出参考数字。

### 评测与测试工具

- **单元**：仓库根 `tests/` 下有 282 个 `test_*.py`。其中 53 个用框架自带的 `tests/utils.py:run_test`（即 `src/pipecat/tests/utils.py`），给单个 processor 喂帧并断言上下行帧类型序列，不需要真实服务；应用开发者也可以用它测自己的 processor。
- **行为评测**：`pipecat eval run`（`cli/commands/eval.py`）。bot 以 `-t eval` 启动，harness 作为 RTVI 客户端连上去扮演用户（`evals/client.py`、`evals/harness.py`）。场景两类（`evals/scenario.py`）：
  - 脚本（`turns:`）：每轮写用户话和期望事件。事件有 `function_call`、`function_call_stopped`（区分取消）、`llm_response`、`tts_response`、`response`、`bot_interrupted`、VAD 和回合事件、`llm_marker`（`evals/events.py`）。期望可带 `within_ms` 时延预算、`absent: true`（预算内不应出现）、`eval:`（judge 标准）；用户轮可带 `send_after: {event, delay_ms}` 做插话（`evals/script.py`）。
  - 模拟（`persona:` + `goal:`）：LLM 扮演有目标的来电者，judge 读整段对话，按逐轮二元标准打分（`evals/simulation.py`、`evals/judge.py`）。
  - 模态：文本（RTVI `send-text`）或音频（用户话用 Kokoro 合成或播录音，bot 话用 Moonshine 转写后给 judge，`evals/audio.py`、`evals/tts.py`）。
  - `--repeat N` 给出每个场景的通过率，失败按类型聚合（`evals/suite.py`、`evals/results.py`）。
- **场景集**：`evals/release/`（插话、空转写、异步工具取消、订餐厅模拟等，`run.sh` 发布前手动跑）；`evals/turn-completion/`（逐模型检查 ●/◐/○ 判停标记）。

### 进程与并发模型

单进程 asyncio。每个 processor 至少两个 task（输入、处理），输出 transport 另有音频、时钟、视频 task。一个 `PipelineWorker` 跑一个会话；多 worker 之间可以通过 bus 交接（`pipeline/worker.py:PipelineWorker.on_bus_message`，示例 `examples/multi-worker/`）。部署和并发调度由外部 runner 负责，本仓库不含 LiveKit 那种进程池。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 帧定义、优先级分类 | `frames/frames.py:Frame`、`SystemFrame`、`DataFrame`、`ControlFrame` | `interruptible` 字段逐帧可改 |
| 三级优先队列 | `processors/frame_processor.py:FrameProcessorQueue` | Start > System > 其他 |
| 打断广播与处理 | `processors/frame_processor.py:FrameProcessor.broadcast_interruption`、`_start_interruption` | 重建处理 task，保留不可打断帧 |
| 用户回合控制 | `processors/aggregators/llm_response_universal.py:LLMUserAggregator._on_user_turn_started`、`_on_user_turn_stopped` | 发回合帧、广播打断、写用户消息、触发推理 |
| 默认判停策略 | `turns/user_turn_strategies.py:default_user_turn_stop_strategies` | `TurnAnalyzerUserTurnStopStrategy(LocalSmartTurnAnalyzerV3)` |
| smart-turn 接入 | `turns/user_stop/turn_analyzer_user_turn_stop_strategy.py:TurnAnalyzerUserTurnStopStrategy` | VAD 停 → 模型判完 → 等转写或 STT P99 超时 |
| smart-turn 推理 | `audio/turn/smart_turn/local_smart_turn_v3.py:LocalSmartTurnAnalyzerV3` | ONNX，取最后 8 s、16 kHz；捆绑 `smart-turn-v3.2-cpu.onnx` |
| VAD | `audio/vad/vad_analyzer.py:VADParams`、`audio/vad/vad_controller.py:VADController` | 默认 confidence 0.7、start 0.2 s、stop 0.2 s；挂在 user 聚合器的 `vad_analyzer` 参数上 |
| 用户空闲 | `turns/user_idle_controller.py:UserIdleController` | bot 说完后计时，工具进行中或用户回合中不计 |
| 抢先生成 | `turns/user_stop/eager_user_turn_stop_strategy.py:EagerUserTurnStopStrategy`、`turns/speculation_gate.py:SpeculationGate` | 依赖 STT 的 eager 转写；遇工具调用撤销 |
| LLM 判停标记 | `turns/user_turn_completion_mixin.py:UserTurnCompletionLLMServiceMixin`、`FilterIncompleteUserTurnStrategies` | 模型输出 ●/◐/○，未完成则不回复 |
| 工具执行 | `services/llm_service.py:LLMService.run_function_calls`、`_run_function_call` | 每调用一个 task；`settled` 拒绝迟到结果 |
| 打断取消工具 | `services/llm_service.py:LLMService._handle_interruptions`、`_cancel_function_call_tasks` | 只取消同步工具 |
| 结果入 context、决定再推理 | `llm_response_universal.py:LLMAssistantAggregator._handle_function_call_result`、`_maybe_push_context_after_function_result` | 合并并行结果；bot 在说话时延后 |
| 异步工具协议 | `processors/aggregators/async_tool_messages.py`；`LLMAssistantAggregator._is_deferred` | started / intermediate / final / cancelled 消息 |
| TTS 分句 | `utils/text/simple_text_aggregator.py:SimpleTextAggregator`；`services/tts_service.py:TextAggregationMode` | 默认 SENTENCE；含中文句末标点；可切 TOKEN |
| TTS 打断 | `services/tts_service.py:TTSService._handle_interruption`、`InterruptibleTTSService._handle_interruption` | 清缓冲、取消音频 context 或重连 |
| 词级时间戳 | `services/tts_service.py:TTSService._add_word_timestamps` | 给 `TTSTextFrame` 打 pts |
| 播放端丢弃 | `transports/base_output.py:BaseOutputTransport.MediaSender.handle_interruptions` | 取消时钟 task，清可打断音频 |
| 输入降噪 | `transports/base_input.py:BaseInputTransport`（`audio_in_filter`）；`audio/filters/` | RNNoise、Koala、Krisp VIVA、ai-coustics |
| OpenAI Realtime 同步 / 截断 | `services/openai/realtime/llm.py:OpenAIRealtimeLLMService._handle_context`、`_process_completed_function_calls`、`_truncate_current_audio_response` | |
| OpenAI Realtime 重建 | `services/openai/realtime/llm.py:OpenAIRealtimeLLMService.reset_conversation` | 断开、重建工具登记、重连、下次重灌 |
| Gemini Live 同步 / 重连 | `services/google/gemini_live/llm.py:GeminiLiveLLMService._handle_context`、`_handle_connection_error`、`_reconnect` | 连续 3 次失败视为致命；带 resumption handle |
| Nova Sonic 主动换会话 | `services/aws/nova_sonic/session_continuation.py:SessionContinuationHelper` | 默认 360 s 后预建下一个会话 |
| 前台委派后台 | `services/openai/live/llm.py:OpenAILiveLLMService`、`ResponsesDelegation`、`ClientDelegation`；`workers/llm/backend_llm_worker.py:BackendLLMWorker` | commentary / thinking 双通道 |
| 上下文摘要 | `processors/aggregators/llm_context_summarizer.py:LLMContextSummarizer` | 不切断未完成的工具调用 |
| 故障转移 | `pipeline/service_switcher.py:ServiceSwitcherStrategyFailover` | 按 `is_usable` 切服务 |
| 评测框架 | `evals/`（`scenario.py`、`script.py`、`simulation.py`、`judge.py`、`suite.py`）；CLI 在 `cli/main.py` 注册 `eval` | |
| 单元测试工具 | `tests/utils.py:run_test` | 给单个 processor 喂帧、断言上下行帧序列 |
| 延迟观测 | `observers/user_bot_latency_observer.py`、`observers/turn_tracking_observer.py` | TTFB 分解、函数调用耗时 |

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：判停由 user 聚合器里的 `UserTurnController` 按策略列表执行，默认 start 是 VAD + 转写，stop 是本地 smart-turn v3（`turns/user_turn_strategies.py:UserTurnStrategies`）；`TurnAnalyzerUserTurnStopStrategy` 在 VAD 判停后跑模型，判完还要等 finalized 转写或 STT P99 超时，S2S 下聚合器把 `wait_for_transcript` 置 False。可替换为 `SpeechTimeoutUserTurnStopStrategy`（纯静音超时）、`FilterIncompleteUserTurnStrategies`（LLM 输出 ●/◐/○ 标记）、`ExternalUserTurnStrategies`（服务端判停），用户空闲由 `turns/user_idle_controller.py:UserIdleController` 在 bot 说完后计时、工具进行中不计。
- [打断与截断](../../03-mechanisms/interruption.md)：用户回合开始即广播 `InterruptionFrame`（SystemFrame），各 processor 在 `FrameProcessor._start_interruption` 中重建处理 task、丢掉可打断帧，LLM 流随 task 取消，TTS 在 `TTSService._handle_interruption` 清缓冲并取消音频 context，输出端在 `MediaSender.handle_interruptions` 清音频队列和时钟队列。级联的截断是词级的：只有按 pts 播到的 `TTSTextFrame` 才进 context；OpenAI Realtime 用 `_truncate_current_audio_response` 按"收到首个 delta 起的墙钟时间"发 `conversation.item.truncate`，Gemini Live 不截断。是否打断可用 `MinWordsUserTurnStartStrategy` 等 start 策略设门槛，旧输出靠"打断瞬间清空队列"丢弃，没有代际或回合 id 过滤迟到帧。
- [首音优化](../../03-mechanisms/first-audio.md)：TTS 默认按句聚合（`SimpleTextAggregator`，含中文句末标点，遇到句末标点后要等到下一个非空白字符才确认断句），可设 `TextAggregationMode.TOKEN` 逐 token 送 TTS；抢先生成用 `EagerUserTurnStopStrategy` + `SpeculationGate`，依赖 STT 给 eager 转写（Deepgram Flux、Cartesia），确认前扣住输出，遇到工具调用直接撤销。仓库里没有首音的实测数字，待确认。
- [工具回合](../../03-mechanisms/tool-calls.md)：`LLMService.run_function_calls` 为每个调用开 task，执行期间 pipeline 不阻塞；出声的常见写法是在 `on_function_calls_started` 里推 `TTSSpeakFrame`，注意它的 `append_to_context` 默认 True。`FunctionCallResultProperties.run_llm=False` 可跳过再推理，并行结果合并为一次推理，bot 正在说话时再推理会延后到它说完；`cancel_on_interruption=False` 的异步工具不被打断取消，结果晚到时以 developer 消息补进 context。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：S2S 服务只把新完成的工具结果从本地 context diff 给上游，其余变化不同步；重建方式各家不同：OpenAI Realtime 不自动重连，靠 `reset_conversation` 手动重建并重灌，Gemini Live 连续失败 3 次内自动重连并带 resumption handle，Nova Sonic 用 `SessionContinuationHelper` 主动换会话。长会话可挂 `LLMContextSummarizer`，但摘要只改本地 context，要生效需重建会话（仓库里没有自动接线）。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：输入 transport 支持 `audio_in_filter`（`audio/filters/` 下有 RNNoise、Koala、Krisp VIVA、ai-coustics），VAD 有 Silero、Krisp VIVA、ai-coustics Quail。代码中未找到回声消除实现（grep `echo` / `AEC` 无结果），推断交给客户端或传输服务，待确认。
- [评测](../../03-mechanisms/evaluation.md)：`pipecat eval` 以 RTVI 客户端身份驱动 bot，支持脚本（`turns:`）和 LLM 模拟用户（`persona:`），可断言 `function_call`、`bot_interrupted`、`within_ms` 时延预算、`absent` 和 `send_after` 插话，文本或音频（Kokoro 合成、Moonshine 转写）两种模态，`--repeat` 给通过率。`evals/release/` 是发布前手动跑的场景集，`evals/turn-completion/` 测 LLM 判停标记；单元层有 `tests/utils.py:run_test`，`.github/workflows` 中未见自动跑 `pipecat eval`。

## 取舍与局限

- **打断是"空间式"冲刷**：打断时把各队列里可打断的帧清掉。它不防迟到帧：上游 WebSocket 在打断之后才到的音频会重新进入流水线（OpenAI Realtime 不按 `response_id` 过滤，推断）。好处是简单，逐帧 `interruptible` 也给了细粒度控制。
- **TTS 产生的音频帧不继承 `TTSSpeakFrame` 的可打断属性**：想让某句提示语不被打断，要自己处理（推断，`tts_service.py` 中未见相关代码）。
- **工具回合在 S2S 上偏慢**：结果帧（`FunctionCallResultFrame`，DataFrame）要经过输出 transport，排在已排队音频之后才到 assistant 聚合器；聚合器在 bot 说话时再延后推 context；S2S 服务收到 context 才 diff 出结果发给上游。也就是说，模型自己的前导语音播完之前，结果发不回上游（推断，未实测）。OpenAI Live 服务改为在 `push_frame` 里直接拦截结果帧发送。
- **`run_llm=False` 在 S2S 上的语义**：结果不推 context，也就暂不发上游，要等下一次 context 推送时才补发并 `response.create`（推断，可能与服务端 VAD 自动发起的 response 冲突，待确认）。
- **S2S 策略写在各服务里**：历史灌入格式（OpenAI 打包成一条 user 消息，Gemini 把工具调用转成文字，Nova 丢工具消息）、配置更新时机、重连次数、截断支持各不相同，`reset_conversation` 不在基类里。适合当"厂商能力和坑的清单"读，不宜照搬分层。
- **Gemini 重连可能重发旧工具结果**：`_disconnect` 清空已完成工具登记，重连后没有像 OpenAI 那样用 `send_new_results=False` 重建（推断，未实测）。
- **抢先生成对工具回合无效**：投机中出现工具调用就撤销。
- **填充语默认进 context**：`TTSSpeakFrame.append_to_context=True`，要"提示语不进模型"需显式关掉；S2S pipeline 没有 TTS，`TTSSpeakFrame` 无人合成，框架没有现成的本地提示语机制。
- **服务端判停关不掉的上游没有覆盖**：Nova Sonic 只能调 `endpointing_sensitivity`；仓库也没有按键说话的现成实现（可用 `ExternalUserTurnStrategies` 组合，推断）。
- **版本演进快**：大量 deprecated 参数（`aggregate_sentences`、`UninterruptibleFrame`、`filter_incomplete_user_turns` 等），读旧示例时要注意。

## 相关

- 机制页：[判停](../../03-mechanisms/turn-detection.md)、[打断与截断](../../03-mechanisms/interruption.md)、[首音优化](../../03-mechanisms/first-audio.md)、[工具回合](../../03-mechanisms/tool-calls.md)、[会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)、[音频前处理](../../03-mechanisms/audio-preprocessing.md)、[评测](../../03-mechanisms/evaluation.md)
- 项目页：[pipecat-flows](pipecat-flows.md)、[livekit-agents](livekit-agents.md)、[smart-turn](../turn-vad/smart-turn.md)、[openai-realtime-agents](openai-realtime-agents.md)
- 对比页：[framework-matrix](../../05-comparison/framework-matrix.md)、[decision-guide](../../05-comparison/decision-guide.md)
