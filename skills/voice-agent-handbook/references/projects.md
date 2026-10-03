# 项目索引：28 个开源项目速查

来源：手册 `04-projects/` 下 28 个项目页与 `04-projects/README.md`，`05-comparison/framework-matrix.md`、`05-comparison/model-matrix.md`。本文件只做索引和摘抄，不下新结论；数字、默认值、代码路径都照项目页抄，项目页标"待确认"的照写，标"推断"的照写。手册页面路径一律相对手册根目录。

## 目录

1. 项目总表（8 组）
2. 框架速查（pipecat、pipecat-flows、livekit-agents、ten-framework、openai-realtime-agents、qwen-audio-agent、unmute、xiaozhi-esp32-server）
3. 模型速查（ASR、TTS、端到端）
4. 判停 / VAD、音频处理、设备小表
5. 查找方法："某框架怎么做某机制"

---

## 1. 项目总表

许可证是代码许可（仓库 LICENSE），权重许可多数待确认。commit 是项目页"分析基于"所记版本，引用代码路径时以它为准。

### frameworks：编排框架

| 项目 | 一句话定位 | 许可证 | 分析 commit | 手册页 |
|---|---|---|---|---|
| pipecat | 帧驱动 Python pipeline 框架，打断靠高优先级帧广播 + 清各处理器队列，级联与 S2S 共用工具执行和上下文聚合 | BSD-2-Clause | 422ad13（CHANGELOG 1.12.0） | 04-projects/frameworks/pipecat.md |
| pipecat-flows | 叠在 pipecat 上的节点图对话流程，每节点限定可见函数；独立包已冻结，1.5.0 起并入 pipecat | BSD-2-Clause | 96223f4（1.4.0，2026-07-05） | 04-projects/frameworks/pipecat-flows.md |
| livekit-agents | 以 SpeechHandle 调度为核心的 Python 语音运行时，级联和实时模型共用会话、工具、打断 API | Apache-2.0（Krisp 插件商业授权） | e7e7783（1.8.3） | 04-projects/frameworks/livekit-agents.md |
| ten-framework | C 内核 + 多语言扩展的图式实时框架，在 `property.json` 的 graph 里拼 RTC/ASR/LLM/TTS | Apache-2.0 加附加条款（不得部署在终端用户设备、不得与 Agora 竞争） | ca00160c（0.11.73） | 04-projects/frameworks/ten-framework.md |
| openai-realtime-agents | OpenAI 官方 Realtime 多 agent 示例（TS）：chat-supervisor 委派与 handoff | MIT | 94c9e91（2026-01-07，SDK 0.0.5） | 04-projects/frameworks/openai-realtime-agents.md |
| qwen-audio-agent | 实时模型做前台，长任务经 ACP/A2A 委派后台 agent，结果在安全窗口注入前台说出 | Apache-2.0 | f6dd0e3（v2.0.x） | 04-projects/frameworks/qwen-audio-agent.md |

### full-duplex：开源全双工

| 项目 | 一句话定位 | 许可证 | 分析 commit | 手册页 |
|---|---|---|---|---|
| unmute | Kyutai 流式 STT/TTS + 任意文本 LLM，服务端状态机组装的近全双工 | MIT | e348e56 | 04-projects/full-duplex/unmute.md |
| moshi | 原生全双工语音语言模型，文本流 + 双音频流，Mimi codec | 权重 CC-BY 4.0；代码 Python MIT、Rust Apache-2.0 | e6a55d2 | 04-projects/full-duplex/moshi.md |

### e2e-models：端到端模型

| 项目 | 一句话定位 | 许可证 | 分析 commit | 手册页 |
|---|---|---|---|---|
| qwen3-omni | 全模态 Thinker-Talker，开源版请求响应式，能出文本和语音 | 代码 Apache 2.0；权重待确认 | e423585 | 04-projects/e2e-models/qwen3-omni.md |
| step-audio2 | 7B 级端到端音频模型，原生工具调用，每回合可选出文本或语音 | 权重 Apache 2.0（mini 系列） | 76e272b | 04-projects/e2e-models/step-audio2.md |
| ultravox | 语音进文本出的音频 LLM，适合当半级联理解端 | 代码 MIT；权重随底座，待确认 | 69ddc63 | 04-projects/e2e-models/ultravox.md |

### asr

| 项目 | 一句话定位 | 许可证 | 分析 commit | 手册页 |
|---|---|---|---|---|
| funasr | 阿里中文 ASR 工具箱，C++ 2pass websocket：流式 partial + VAD 断句后离线模型定稿 | 代码 MIT；权重待确认 | 12e417f | 04-projects/asr/funasr.md |
| sensevoice | 234M 非流式多语种整句模型，内置标点 / ITN，带情感和事件标签 | 代码 MIT；权重待确认 | ea15219 | 04-projects/asr/sensevoice.md |
| fireredasr | 1.1B AED / 8.3B LLM 非流式中文模型，适合当准确率参照 | 代码 Apache-2.0；权重待确认 | 834635e | 04-projects/asr/fireredasr.md |
| sherpa-onnx | 跨平台本地语音推理运行时（不是模型），承载流式 / 非流式 ASR、VAD、唤醒等 | Apache-2.0（模型随来源） | 040afe3 | 04-projects/asr/sherpa-onnx.md |

### tts

| 项目 | 一句话定位 | 许可证 | 分析 commit | 手册页 |
|---|---|---|---|---|
| cosyvoice | LLM + flow matching 零样本 TTS，文本流入 + 音频流出，有 vLLM / TRT-LLM 部署 | 代码 Apache-2.0；权重待确认 | 074ca6d | 04-projects/tts/cosyvoice.md |
| fish-speech | Dual-AR 高表现力多语种 TTS，本仓库无句内流式 | Fish Audio Research License，商用需另签 | 214da3c | 04-projects/tts/fish-speech.md |
| index-tts | 情感与音色分离、可控性最强的零样本 TTS，官方路径整段输出 | bilibili Model Use License | ee40fa7 | 04-projects/tts/index-tts.md |
| spark-tts | Qwen2.5 + BiCodec 极简 TTS，16 kHz，流式只在 Triton 路径 | 代码 Apache-2.0；权重待确认 | 2f1ea90 | 04-projects/tts/spark-tts.md |
| fireredtts2 | 面向多人长对话的逐帧（80 ms）流式 TTS | 代码 Apache-2.0；权重待确认 | 404f3f6 | 04-projects/tts/fireredtts2.md |
| voxcpm | 连续表征扩散自回归 TTS，48 kHz，可凭描述设计音色 | 代码和权重 Apache-2.0 | f772e49 | 04-projects/tts/voxcpm.md |

### turn-vad：判停与 VAD

| 项目 | 一句话定位 | 许可证 | 分析 commit | 手册页 |
|---|---|---|---|---|
| smart-turn | 只看音频的语义判停模型，pipecat 默认判停 | BSD 2-clause | 4786657（v3.2） | 04-projects/turn-vad/smart-turn.md |
| ten-vad | 轻量帧级 VAD，句尾检测比 Silero 快 | Apache 2.0 加附加条款（禁止与 Agora 竞争的部署） | 22a3bcd | 04-projects/turn-vad/ten-vad.md |

### audio-processing：音频前处理与传输

| 项目 | 一句话定位 | 许可证 | 分析 commit | 手册页 |
|---|---|---|---|---|
| rnnoise | 开源 RNN 单通道降噪（48 kHz / 10 ms） | BSD 风格 | 70f1d25 | 04-projects/audio-processing/rnnoise.md |
| webrtc-audio-processing | WebRTC APM 独立打包：AEC / NS / AGC | BSD 风格加专利授权 | d0569cf（2.1，WebRTC M131） | 04-projects/audio-processing/webrtc-audio-processing.md |
| aiortc | 纯 Python WebRTC 协议栈，pipecat SmallWebRTC 的底层 | BSD-3-Clause | 8a28646 | 04-projects/audio-processing/aiortc.md |

### devices：端侧

| 项目 | 一句话定位 | 许可证 | 分析 commit | 手册页 |
|---|---|---|---|---|
| xiaozhi-esp32 | ESP32 语音硬件固件，哑终端 + 公开端云协议 | MIT（项目页未记，取自仓库 LICENSE） | 8ce50d2 | 04-projects/devices/xiaozhi-esp32.md |
| xiaozhi-esp32-server | 小智开源服务端：插件化级联 VAD/ASR/LLM/TTS | MIT | 87c6df77 | 04-projects/devices/xiaozhi-esp32-server.md |

---

## 2. 框架速查

每节先给路径前缀约定（项目页的缩写），后面的路径按该前缀理解。"推断"= 项目页读代码推出、未实测。

### pipecat

路径相对 `src/pipecat/`；示例相对 `examples/`。识别特征：`FrameProcessor`、`Pipeline`、`PipelineTask`、`InterruptionFrame`、`TTSSpeakFrame`。

- 链路：级联、S2S（OpenAI Realtime / Gemini Live / Nova Sonic 等）、半级联（矩阵依据 `examples/realtime/realtime-openai-text.py`，项目页未写）、前台委派（`services/openai/live/llm.py:OpenAILiveLLMService`）。
- 判停默认：Silero VAD（confidence 0.7、start 0.2 s、stop 0.2 s，`audio/vad/vad_analyzer.py:VADParams`）+ 本地 smart-turn v3（`turns/user_turn_strategies.py:default_user_turn_stop_strategies`），判完再等定稿转写或 STT P99 超时（`turns/user_stop/turn_analyzer_user_turn_stop_strategy.py:TurnAnalyzerUserTurnStopStrategy`）；可换 `SpeechTimeoutUserTurnStopStrategy`、`FilterIncompleteUserTurnStrategies`（LLM 输出 ●/◐/○）、`ExternalUserTurnStrategies`。S2S 外置：OpenAI Realtime 可关服务端 VAD 手动 commit，Gemini Live 用 `activity_start/end`，Nova Sonic 只能调灵敏度。
- 打断与截断：用户回合开始即广播 `InterruptionFrame`（`processors/frame_processor.py:FrameProcessor.broadcast_interruption`、`_start_interruption`），TTS 清缓冲（`services/tts_service.py:TTSService._handle_interruption`），输出端清队列（`transports/base_output.py:BaseOutputTransport.MediaSender.handle_interruptions`）。级联词级截断：按 pts 播到的 `TTSTextFrame` 才进 context（`TTSService._add_word_timestamps`）；OpenAI Realtime 按"首个 delta 起的墙钟"发 truncate，未扣播放缓冲（`services/openai/realtime/llm.py:OpenAIRealtimeLLMService._truncate_current_audio_response`）；Gemini Live 不截断。门槛用 `MinWordsUserTurnStartStrategy` 等 start 策略。
- 工具回合：每调用一个 task（`services/llm_service.py:LLMService.run_function_calls`）；异步工具 `cancel_on_interruption=False`（1.0.0 起），晚到结果以 developer 消息补进 context（`processors/aggregators/async_tool_messages.py`）；跳过再生成 `FunctionCallResultProperties.run_llm=False`，并行结果合并一次推理（`llm_response_universal.py:LLMAssistantAggregator._maybe_push_context_after_function_result`）；执行期出声：`on_function_calls_started` 里推 `TTSSpeakFrame`，默认进 context；委派：`OpenAILiveLLMService` 的 `ResponsesDelegation` / `ClientDelegation`。
- S2S 封装与重连：本地 context 是镜像，只 diff 新完成的工具结果发上游（`OpenAIRealtimeLLMService._handle_context`、`_process_completed_function_calls`）；OpenAI Realtime 不自动重连，手动 `reset_conversation`；Gemini Live 3 次内自动重连带 resumption handle（`services/google/gemini_live/llm.py:GeminiLiveLLMService._reconnect`）；Nova Sonic 默认 360 s 主动换会话（`services/aws/nova_sonic/session_continuation.py:SessionContinuationHelper`）。
- 评测：`pipecat eval`（`evals/`：`scenario.py`、`script.py`、`simulation.py`、`judge.py`；CLI 在 `cli/main.py`），脚本或 LLM 模拟用户，可断言工具调用、插话、`within_ms`，默认文本模态可切音频；单元工具 `tests/utils.py:run_test`（即 `src/pipecat/tests/utils.py`）；`evals/turn-completion/` 测 LLM 判停标记。
- 并发：单进程 asyncio，一个 `PipelineWorker` 一个会话（`pipeline/worker.py:PipelineWorker`），部署和调度交给外部 runner。
- 已知坑：打断只在瞬间清队列，无代际，迟到上游音频会被当新回复播（推断）；S2S 下工具结果排在已排队音频之后才回上游（推断）；`TTSSpeakFrame.append_to_context` 默认 True；S2S pipeline 无本地提示语机制；Gemini 重连可能重发旧工具结果（推断）；抢先生成（`EagerUserTurnStopStrategy`）遇工具调用撤销；deprecated 参数多，读旧示例要注意。

### pipecat-flows

路径相对 `src/pipecat_flows/`。识别特征：`FlowManager`、`NodeConfig`、`FlowsFunctionSchema`、`pre_actions` / `post_actions`。新项目应改用 pipecat 本体的 `pipecat.flows`。

- 链路：级联；S2S 无示例，节点切换能否生效待确认（OpenAI Realtime 对 `LLMMessagesAppendFrame` 未实现，Gemini Live 对 `LLMSetToolsFrame` 无动作）。
- 判停默认：沿用 pipecat；只用 `respond_immediately=False` 决定进节点后等用户先说。
- 打断与截断：沿用 pipecat；函数 `cancel_on_interruption` 默认 False（`types.py:FlowsFunctionSchema`），即异步语义，插话不取消函数。
- 工具回合：异步为默认；边函数 `run_llm=False` + `on_context_updated=_check_and_execute_transition`，等并行调用全部结束才切节点（`manager.py:FlowManager._create_transition_func`、`_check_and_execute_transition`）；执行期出声用节点 `pre_actions: tts_say`（`actions.py:ActionManager._handle_tts_action`，`append_text_to_context` 默认 True）；不委派其他模型，multi-worker bus 可在自由对话和 Flows worker 间交接。
- S2S 封装与重连：沿用 pipecat；context 策略 APPEND / RESET（`manager.py:FlowManager._update_llm_context`），`RESET_WITH_SUMMARY` 已废弃；`state` 只在内存。
- 评测：`evals/manifest.yaml`、`evals/scenarios/*.yaml`，基于 `pipecat eval` 对 8 个示例，默认文本模态。
- 并发：同 pipecat。
- 已知坑：每次经边函数切节点要两次推理，首音多一轮；RESET 丢指代；函数默认不随打断取消，有副作用的 handler 需自己判断（推断）；独立包已冻结。

### livekit-agents

路径相对 `livekit-agents/livekit/agents/`，插件相对 `livekit-plugins/`。识别特征：`AgentSession`、`Agent`、`@function_tool`、`RunContext`、`JobContext`、`livekit.plugins.*`。

- 链路：级联、S2S（`RealtimeModel`）、半级联（文本模态 + 会话 TTS，`_realtime_generation_task_impl` 内 `_process_one_message`）；`DuplexModel`（GPT-Live）被包成 RealtimeModel。
- 判停默认：VAD + 可选 turn detector（新版只看音频，本地 `v1-mini` / 云端 `v1`，`inference/eot/detector.py:TurnDetector`，`zh` 阈值 0.355）+ 端点等待默认 min 0.5 / max 3.0 s（`voice/turn.py:EndpointingOptions`，可动态学习 `voice/endpointing.py:DynamicEndpointing`），主逻辑 `voice/audio_recognition.py:AudioRecognition._run_eou_detection`。外置取决于上游 `can_disable_turn_detection`：OpenAI 可关，Gemini / AWS / Ultravox 不能，会话开始定一次（`voice/agent_activity.py:AgentActivity._resolve_rt_turn_detection_enabled`）；`manual` 模式做按键说话。
- 打断与截断：入口 `AgentActivity.interrupt`、`_interrupt_by_audio_activity`、`_on_input_speech_started`；门槛 `voice/turn.py:InterruptionOptions`（`min_duration` 0.5 s、可选 `min_words`、误打断 2 s 后恢复播放、`mode="adaptive"`）；`aec_warmup_duration` 默认 3.0 s。截断按播放位置：`voice/generation.py:forward_generation` 记 `playback_position` 和估算的 `synchronized_transcript`（中文按单字）；实时模型需上游声明 `message_truncation`（OpenAI 实现 `llm/_realtime/openai.py:RealtimeSession.truncate`），Gemini / AWS 只改本地。
- 工具回合：异步 = 首次 `ctx.update()` 即收口，结果等会话空闲合并回帖（`voice/events.py:RunContext.update`、`voice/tool_executor.py:_ToolExecutor._deliver_reply`）；跳过再生成 `reply_required=False`（`voice/generation.py:make_tool_output`）、`StopResponse`、`cancel_tool_reply()`；执行期出声 `session.say()`（`voice/agent_session.py:AgentSession.say`，可不进上下文、可传预合成音频）、`RunContext.with_filler`；委派 GPT-Live 插件 `delegation="responses" | "client"`。
- S2S 封装与重连：能力位 `RealtimeCapabilities`；OpenAI 维护服务端镜像（`llm/remote_chat_context.py:RemoteChatContext`）+ LCS diff（`llm/utils.py:compute_chat_ctx_diff`），重连重放镜像且排除工具调用（`RealtimeSession._main_task` 内 `_reconnect`）；Gemini 用 resumption handle；跨模型兜底 `llm/realtime_fallback_adapter.py:RealtimeModelFallbackAdapter`。
- 评测：`AgentSession.run` → `voice/run_result.py:RunResult` 断言与 judge（注入的是文本）；整段 judge `evals/judge.py`、`evals/evaluation.py:JudgeGroup`；仿真 `simulation.py:SimulationContext`；无判停或首音基准。
- 并发：worker 管预热进程池，一进程一个 job（`ipc/job_proc_executor.py:ProcJobExecutor.launch_job`），按 CPU 负载接单（`worker.py:_DefaultLoadCalc`，阈值 0.7）；turn detector 在 worker 级共享推理进程。
- 已知坑：很多行为只在级联成立（抢先生成、`max_tool_steps`、占位结果）；实时路径要等所有排队发言播完才回传工具结果，同步工具被打断后拖住下一轮最长约 5 s（`voice/speech_handle.py:INTERRUPTION_TIMEOUT`）；OpenAI 镜像未处理 `conversation.item.truncated`，重连可能把截断回复以全文重放（推断）；`say()` 只写本地上下文；部署绑定 LiveKit 房间和 worker。

### ten-framework

缩写：`rt/` = `core/src/ten_runtime/`；`ext/` = `ai_agents/agents/ten_packages/extension/`；`ex/` = `ai_agents/agents/examples/`。识别特征：`property.json` 里的 graph、`AsyncExtension`、`tman`、`main_control` 扩展。

- 链路：级联、S2S（`v2v` 节点，`ext/openai_mllm_python/extension.py:OpenAIRealtime2Extension`）。
- 判停默认：直接用 ASR final（`main_control._on_asr_result`）；可接 TEN Turn Detection，每个 final 远程调一次 LLM（`ext/ten_turn_detection/extension.py:TENTurnDetectorExtension`）；ten-vad 示例里只用于打断（`ext/ten_vad_python/extension.py:TENVADPythonExtension`，120 ms 判开始、1000 ms 判结束）；S2S 用上游服务端 VAD，未提供外置路径。
- 打断与截断：部分转写超过 2 个字符，或 VAD `start_of_sentence`，或判停扩展发 `flush` 触发；`ex/voice-assistant/.../main_python/extension.py:MainControlExtension._interrupt` 下发 flush_llm、`tts_flush`、RTC flush；TTS 按 `request_id` 丢迟到文本（`ext/elevenlabs_tts2_python/extension.py:ElevenLabsTTS2Extension.cancel_tts`）；无上下文截断；S2S 的 truncate 代码在 `start_connection` 里被注释掉。
- 工具回合：无异步工具，`await` 结果后串行再调 LLM（`ex/voice-assistant/.../main_python/agent/llm_exec.py:LLMExec`）；结果类型 `llmresult` 时再调 LLM，其他类型待确认；无填充语或进度播报；无委派。
- S2S 封装与重连：断线 1 s 后重连，无退避无上限（`OpenAIRealtime2Extension._handle_reconnect`）；`_resume_context` 逐条重发；无镜像、无 diff、无错误分级。
- 评测：扩展级单测 `rt/binding/python/interface/ten_runtime/async_test.py:AsyncExtensionTester`；ASR / TTS 协议守护测试 `ai_agents/agents/integration_tests/asr_guarder`、`tts_guarder`；无对话 judge 或延迟基准。
- 并发：Go 服务端每 channel 起一个 worker 进程（`ai_agents/server/internal/worker_linux.go:Worker.start`、`http_server.go:handlerStart`），`WORKERS_MAX` 限流，无负载计算、无预热。
- 已知坑：graph 是布线不是仲裁，无优先级无代际；迟到上游音频无代际过滤（推断）；判停模型是外部服务，延迟成本另算；`agora_rtc`、`ten_ai_base` 不在仓库；许可附加条款限制端侧部署。

### openai-realtime-agents

路径相对 `src/app/`；标"SDK"的来自 `@openai/agents-realtime@0.0.5` 的 `dist/*.mjs`。识别特征：`RealtimeAgent`、`RealtimeSession`、`@openai/agents-realtime`、`agentConfigs/`。

- 链路：S2S（浏览器 WebRTC 直连 Realtime API）；chat-supervisor 委派后台文本模型；handoff。
- 判停默认：服务端 `server_vad`（threshold 0.9、静音 500 ms，`App.tsx:updateSession`）；只能按键说话外置：`turn_detection: null`，松开时 commit + `response.create`（`App.tsx:handleTalkButtonUp`）。
- 打断与截断：服务端 VAD；应用层按键或发文本时 `interrupt()`；WebRTC 下只发 `response.cancel` + `output_audio_buffer.clear`，不发 truncate（SDK `openaiRealtimeWebRtc.mjs:OpenAIRealtimeWebRTC.interrupt`）；服务端是否自动截断待确认；WebSocket 版 SDK 会自算 `audio_end_ms`，本仓库未用。
- 工具回合：无异步工具；无 `run_llm` 类开关，执行完总是 output + `response.create`（SDK `realtimeSession.mjs:RealtimeSession.#handleFunctionToolCall`）；执行期出声靠 prompt 要求前台先说填充语，代码无兜底；委派：`agentConfigs/chatSupervisor/supervisorAgent.ts:getNextResponseFromSupervisor` 同步调 `gpt-4.1`（非流式）；handoff 换 instructions 和 tools（SDK `RealtimeSession.#handleHandoff`）。
- S2S 封装与重连：以服务端为准，SDK 重建本地 history 镜像只用于 UI 和后台；无重连逻辑；UI 换 agent 断开重连、历史全丢（`App.tsx:handleSelectedAgentChange`）。
- 评测：无。
- 并发：浏览器端运行，工具也在浏览器执行；服务端只发临时密钥（`api/session/route.ts:GET`）、代理 Responses API（`api/responses/route.ts:POST`）。
- 已知坑：委派进行中被打断时 fetch 不取消，过时答案可能被念出（推断）；README 自述填充语播完后约 2 s 才有内容；输出护栏每 100 字符分类一次，命中时大部分已播出；SDK 锁 0.0.5，示例和 README 有漂移；代码模型名与 README 不一致。

### qwen-audio-agent

路径相对 `server/src/`（纯 JS ESM）。识别特征：`spawn_thinking`、`AnnouncementWindow`、`RealtimeFrontend`、`BackendPort`、ACP / A2A。

- 链路：S2S 前台 + 后台 agent（ACP / A2A）；不提供 STT / LLM / TTS 管线；Provider 适配 DashScope、GPT-Live、Google Live、Doubao、StepFun、HF s2s、MiniCPM-o（`voice/providers/*.mjs`）。
- 判停默认：全交给上游（`smart_turn` / `semantic_vad` / `server_vad`，默认值在仓库根 `shared/realtime-model-catalog.mjs`），本地无 VAD，不可外置。
- 打断与截断：服务端 `speech_started`、手动文本、客户端 `interrupt`（`voice/realtime-input-runtime.mjs:RealtimeInputRuntime.#startSpeech`）；回合代际 +1、清客户端播放、`response.cancel`；单槽位上游超时则重连（`voice/realtime-response-slot.mjs:RealtimeResponseSlot.recover`）；不按播放位置截断，本地只记"有没有开始播放"（以客户端 `playback.started` 为准）。
- 工具回合：异步 = `spawn_thinking` 立即回 `accepted`（`frontend/tools/agent-task-runtime.mjs:AgentTaskRuntime.executeSpawnThinkingToolCall`），后台完成后在安全窗口注入 + `response.create`（`voice/realtime-provider.mjs:RealtimeFrontend.injectDelivery`、`voice/announcement/announcement-window.mjs:AnnouncementWindow.isBlocked`）；跳过再生成：同一 response 已说过话且工具不要求摘要时不再生成（`frontend/tools/tool-call-handler.mjs:ToolCallHandler.flushDeferredToolResponse`）；执行期刻意不填充，进度播报首条 ≥ 60 s（`voice/announcement/progress-announcement-manager.mjs:ProgressAnnouncementManager`）；委派是核心能力。
- S2S 封装与重连：Provider + Protocol 声明能力位；指数退避 500 ms 起、上限 10 s、带抖动（`voice/reconnect-backoff.mjs:ReconnectBackoff`）；重连后一条 `<restored_context>` user 条目恢复近期历史（`RealtimeFrontend.restoreRecentConversation`）；内容安全拒绝时隔离出错回合再重建（`voice/realtime-recovery-context.mjs:RealtimeRecoveryContext`）。
- 评测：智能座舱延迟基准（`examples/smart-cockpit/bench/`，前台直连 1.317 s vs 委派 3.363 s）；tau2-bench 改编任务完成率（文本口径）；provider 行为测试 `server/test/realtime-provider-behavior.test.mjs`。
- 并发：客户端一条 WebSocket 连 Gateway；任务按 owner FIFO，通知领取带租约防多连接重复播报（`SessionTaskCoordinator.claimPendingNotifications`）。
- 已知坑：委派不省再生成，短工具走后台更慢；被打断的播报可能被记为已送达（`AnnouncementManager.dismissActive`）；无统一仲裁器；判停、打断粒度全由上游决定；MiniCPM-o 这类不支持工具的前台无法委派。

### unmute

识别特征：`UnmuteHandler`、`moshi-server`、`stt-1b-en_fr` / `tts-1.6b-en_fr`、FastRTC。

- 链路：级联（流式 STT / TTS + OpenAI 兼容文本 LLM），服务端状态机做近全双工。
- 判停默认：STT 停顿预测头 `prs[2]` 经 EMA > 0.6（`unmute/unmute_handler.py:UnmuteHandler.determine_pause`，来源 `unmute/stt/speech_to_text.py:SpeechToText.__aiter__`），再灌零帧冲刷约 0.5 s 延迟（`UnmuteHandler.receive`）；不可外置，协议无 commit。
- 打断与截断：bot 说话时 STT 出任何新词（`UnmuteHandler._stt_loop`），或停顿概率 < 0.4 且会话已过 3 s；动作 `UnmuteHandler.interrupt_bot`（替换输出队列、取消 tts/llm quest）；词级截断：历史只保留 TTS 按 `start_s` 释放的词（`unmute/tts/text_to_speech.py:TextToSpeech.__aiter__`），打断符送 LLM 前去掉（`unmute/llm/llm_utils.py:preprocess_messages_for_llm`）。
- 工具回合：不涉及，`VLLMStream.chat_completion` 只读 `delta.content`；README 建议把工具藏在 LLM 服务端。
- S2S 封装与重连：不涉及；断线即丢历史。
- 评测：压测客户端分段延迟 `unmute/loadtest/loadtest_client.py`、`loadtest_result.py`（stt / vad / llm / tts_start）；Prometheus + Grafana（`unmute/metrics.py`）。
- 并发：Docker Compose 五个服务；每连接一个 `UnmuteHandler`（`unmute/main_websocket.py:websocket_route`），每后端进程最多 4 个客户端；STT / TTS batch 满了拒绝新连接。
- 已知坑：只支持英语和法语，`rechunk_to_words` 按空白切词对中文无效；"语义 VAD"绑定 Kyutai STT；截断时间以服务端为准，未扣网络和客户端缓冲；历史无上限；不支持附和与重叠说话。

### xiaozhi-esp32-server

`srv/` = `main/xiaozhi-server/`。识别特征：`ConnectionHandler`、`sentence_id`、`client_abort`、`plugins_func`、`config.yaml` 的 provider 选择。

- 链路：级联（设备经 WebSocket / MQTT 网关接入）。
- 判停默认：manual 由设备松键；auto / realtime 用 Silero 双阈值 + 5 帧滑窗 + 默认 200 ms 静音（`srv/core/providers/vad/silero.py:VADProvider.is_vad`）；流式 ASR 以厂商端点为准；无语义判停。
- 打断与截断：设备 `abort`（`srv/core/handle/abortHandle.py:handleAbortMessage`：置 `client_abort`、清队列、回 `tts stop`）；语音打断在判停 + ASR 完成之后（`srv/core/handle/receiveAudioHandle.py:startToChat`）；设备声明服务端 AEC 时 VAD 有声即打断；`sentence_id` 过滤残留音频；不截断，被打断的回复全文写入历史（设备不回传播放位置）。
- 工具回合：无异步工具，多调用并行执行、逐个等待，单个 30 s 超时，最多 5 层递归（`srv/core/connection.py:ConnectionHandler.chat`）；结果 `RESPONSE` 直接念，`RECORD` / `NONE` 不再生成（`srv/core/connection.py:_handle_function_result`）；只有 tool_call 之前流出的文本会先播，无填充语；无委派，`direct_answer` 虚拟工具。
- S2S 封装与重连：不涉及；断线不恢复，跨会话靠记忆模块；`Dialogue` 无长度上限（`srv/core/utils/dialogue.py:Dialogue`）。
- 评测：`srv/performance_tester.py` 测各 provider 耗时（只算均值）；provider 契约测试。
- 并发：一个 WebSocket 连接一个 `ConnectionHandler`（`srv/core/websocket_server.py:_handle_connection`）；asyncio 主循环 + 每连接线程池 + ASR / TTS 常驻线程，队列串联。
- 已知坑：打断延迟 = 判停 + 识别；`sentence_id` 不下发、ASR 为空不回消息；线程 + 共享标志竞态未系统验证；部分 TTS provider 无 `client_abort` 检查；服务端 AEC 是实验性近似（`srv/core/connection.py:_apply_aec`）。

---

## 3. 模型速查

数字一律是项目页引用的仓库自述，口径各异，不能直接比大小；括号是口径或出处。

### ASR

| 项目 | 流式 | 首字 / chunk | 端点 | 中文 CER（自述） | 部署 | 许可 |
|---|---|---|---|---|---|---|
| funasr | 是：2pass，在线 Paraformer 出 partial，离线模型定稿 | 块 600 ms，回看 / 前瞻各 300 ms；首字实测待确认 | FSMN-VAD，C++ 默认尾部静音 800 ms、最长段 15 s | Paraformer-zh AISHELL-1 1.95%（`README_zh.md`） | C++ ONNX websocket；GPU 走 Triton；端侧弱 | 代码 MIT；权重待确认 |
| sensevoice | 否，整句；`sensevoice-server` 每 400 ms 重编码伪流式 | 首字待确认 | 模型无；server 内置 FSMN-VAD，尾部静音约 1850 ms（推断） | 184 条长音频 7.81（fp32）/ 8.17（Q8）（`runtime/llama.cpp/BENCHMARKS.md`） | Python / ONNX / libtorch / llama.cpp GGUF；server 全局锁，默认 4 连接 | 代码 MIT；权重待确认 |
| fireredasr | 否，无 chunk 接口 | 不适用，只有整句 final | 无，需外接 | AED aishell1 0.55 / Average-4 3.18；LLM 0.76 / 3.05（`README.md`） | Python 按文件批量；AED 有 Triton + TensorRT | 代码 Apache-2.0；权重待确认 |
| sherpa-onnx | 运行时：流式 transducer / paraformer / CTC | 在线 paraformer 610 ms；zipformer 中文数值待确认；首字待确认 | 出字后静音 1.2 s、未出字 2.4 s、段长 20 s；另有 Silero / TEN VAD | 仓库不给，取决于模型 | C++ 核心，12 种语言绑定 + WASM，移动端 / NPU | Apache-2.0 |

要点：SenseVoice、FireRedASR 是整句模型，适合当定稿或二遍；FunASR 热词和时间戳只在离线那一遍；FireRedASR 无标点 / ITN / 时间戳。

### TTS

| 项目 | 文本流入 | 音频流出 | 首包（自述，口径） | 中文 TN | 部署 / 显存 | 许可 |
|---|---|---|---|---|---|---|
| cosyvoice | 支持（CV2 / 3 bistream），不能与 vLLM 同用 | 支持，首块先攒约 1.1–2.1 s 音频的 token（推断） | Triton + TRT-LLM，L20 并发 1：CV2 P50 218 ms，开说话人缓存 185 ms（客户端发请求到收到首块）；README"低至 150 ms"未说明口径 | 有；文本流入时跳过 | vLLM、TensorRT、Triton + TRT-LLM；显存待确认 | 代码 Apache-2.0；权重待确认 |
| fish-speech | 不支持 | 名义支持，实际整段出 | H200 约 100 ms，是外部 SGLang-Omni 的数字；本仓库路径待确认 | 无（`normalize` 未生效） | 本仓库无加速；建议 ≥ 24 GB | Research License，商用需另签 |
| index-tts | 不支持 | PyTorch 按文本段整段出；TRT 后端（仅 2.0）100 code 一块 | 无首包数字；RTF（4090，2.5 bf16）7 字 0.29，推断 7 字首包约 0.4 s | 有，支持拼音 | DeepSpeed / torch.compile / TRT；< 10 GB 进 low-VRAM | bilibili Model Use License |
| spark-tts | 不支持 | 本地整段；Triton + TRT-LLM 流式 | Triton，L20，首块 P50 并发 1 为 210 ms（客户端发请求到收到首块；流式行指向第三方 fork） | 无 | Triton + TRT-LLM；显存待确认；16 kHz | 代码 Apache-2.0；权重待确认 |
| fireredtts2 | 不支持 | 逐帧，每块 80 ms | README"低至 140 ms"（L20），未说明口径 | 无 | 无 vLLM / TRT；fp32 约 14 GB、bf16 约 9 GB | 代码 Apache-2.0；权重待确认；克隆仅限学术研究 |
| voxcpm | 不支持 | 每步一个 patch，约 160 ms（推断） | 无首包数字；RTF（4090）VoxCPM2 约 0.30，Nano-vLLM 约 0.13 | 有（wetext），默认关 | torch.compile；外部 Nano-vLLM / vLLM-Omni；VoxCPM2 约 8 GB | 代码和权重 Apache-2.0，可商用 |

要点：只有 CosyVoice CV2 / 3 训练了文本流入；六个都没有显式取消接口；CosyVoice 218 / 185 ms 与 Spark-TTS 210 ms 口径最接近，其余宣传值起止点不明。

### 端到端（含 moshi）

| 项目 | 输入 / 输出 | 流式 | 首包 / 延迟 | 中文 | 工具调用 | 部署 / 显存 | 许可 |
|---|---|---|---|---|---|---|---|
| qwen3-omni | 文本、图像、音频、视频 → 文本 + 语音（`vllm serve` 只出文本） | 开源仓库不能，整段进整段出 | 待确认 | 含中文；WenetSpeech net / meeting 4.69 / 5.89（页标 WER） | 手写 `<tools>` 模板单次触发示例；回注续答待确认 | 30B-A3B MoE；15 s 视频输入 78.85 GB；纯音频待确认 | 代码 Apache 2.0；权重待确认 |
| step-audio2 | 音频 + 文本 → 文本，或文本 + 语音 | 输出可流式；输入整段（25 s 切块） | 待确认 | mini AISHELL 0.78，中文平均 3.19（页标 CER） | 原生 parser + 完整回注示例；并行调用待确认 | 7B 级；24 GB 是否够待确认 | 权重 Apache 2.0 |
| ultravox | 音频 + 文本 → 只出文本 | 输出文本流式；输入整段 | 待确认 | 有 wenetspeech 训练数据；无评测数字，待确认 | 本仓库无，继承底座待确认 | 随底座：8B 单卡，70B 多卡 | 代码 MIT；权重随底座，待确认 |
| moshi | 用户音频流 → 音频 + 自身文本 | 原生全双工，每 80 ms 一步 | README 理论 160 ms，L4 实测约 200 ms | 不支持，只说英语 | 无 | PyTorch bf16 约 24 GB；Rust int8、MLX int4 / int8 | 权重 CC-BY 4.0；代码 MIT / Apache-2.0 |

要点：只有 moshi 是流式进流式出，判停和打断在模型内；其余三家开源形态是请求-响应，判停打断由外层负责，接近半级联；四个都不产出用户转写。

---

## 4. 判停 / VAD、音频处理、设备

### 判停与 VAD

| 项目 | 类型 | 输入 | 输出 | 体量与速度（自述） | 中文 | 接入 | 关键路径 |
|---|---|---|---|---|---|---|---|
| smart-turn | 只看音频的语义判停 | 16 kHz，整轮最长 8 s，每次整轮重算 | P(complete)，阈值写死 0.5 | int8 8 MB；部分 CPU 低至 10 ms，多数云主机 < 100 ms | 23 种语言含中文，效果待确认 | pipecat 默认判停 | `inference.py:predict_endpoint`；pipecat `audio/turn/smart_turn/local_smart_turn_v3.py:LocalSmartTurnAnalyzerV3` |
| ten-vad | 帧级 VAD | 16 kHz int16，每次 160 或 256 样本 | 帧级概率 + 0/1，无起止状态机 | 库约 300 KB；RTF 0.0086（Silero 0.0127） | 中文待确认 | TEN 扩展、sherpa-onnx | `include/ten_vad.h:ten_vad_process`；`include/ten_vad.py:TenVad` |

两者不同层：ten-vad 给"静音开始了"，smart-turn 判"说完没有"。ten-vad 上层状态机默认值差异大：TEN 扩展 1000 ms 静音判结束，sherpa-onnx 0.5 s。

### 音频前处理与传输

| 项目 | 功能 | 输入规格 | 放在哪 | 框架接入 | 关键路径 |
|---|---|---|---|---|---|
| rnnoise | 单通道降噪，不做 AEC / AGC | 固定 48 kHz，480 样本一帧；算法延迟约 20 ms（推断） | AEC 之后、VAD 之前 | pipecat `RNNoiseFilter` | `src/denoise.c:rnnoise_process_frame` |
| webrtc-audio-processing | AEC3 / AECM、NS、AGC1 / AGC2 | 约 10 ms 一块，8–384 kHz | AEC 必须在端侧；NS、AGC 两端都可 | livekit 仅 console 模式用 libwebrtc 的 APM（非此打包） | `webrtc/api/audio/audio_processing.h:AudioProcessing` |
| aiortc | 纯 Python WebRTC 传输层 | Opus 固定 48 kHz 立体声 96 kbps；另有 G.722、PCMU、PCMA | 服务端传输层 | pipecat SmallWebRTC | `src/aiortc/rtcpeerconnection.py:RTCPeerConnection` |

aiortc 音频无 NACK、无 PLC、固定 16 包抖动缓冲，适合开发和一对一。框架侧前处理：pipecat、livekit-agents、unmute、qwen-audio-agent、openai-realtime-agents 服务端都不做 AEC，依赖客户端 / 浏览器。

### 设备

| 项目 | 判停 | 打断 | 工具 | 前处理 | 关键路径 |
|---|---|---|---|---|---|
| xiaozhi-esp32 | 端侧不判停：manual 松键发 `listen stop`；auto / realtime 交服务端 | 按键、点击、唤醒词发 `abort`；无回合 id、无播放位置回执；点击打断时已缓冲音频可能播完（推断） | 设备是 MCP server（`main/mcp_server.cc:McpServer`） | AEC 可选（ESP-SR AFE 或服务端，默认都关），NS / AGC 关 | `main/application.cc:Application::Run`；`main/protocols/protocol.h:Protocol`；`main/audio/audio_service.cc:AudioService` |
| xiaozhi-esp32-server | 见第 2 节 | 见第 2 节 | 见第 2 节 | 只有 MQTT 网关路径的实验性服务端 AEC | 见第 2 节 |

协议文档：xiaozhi-esp32 仓库 `docs/websocket_zh.md`、`docs/mqtt-udp_zh.md`、`docs/mcp-protocol_zh.md`。

---

## 5. 查找方法："某框架怎么做某机制"

1. **先看本文件第 2 节**对应框架的那一行（判停 / 打断截断 / 工具回合 / S2S / 评测 / 并发 / 坑），通常能直接给出默认行为和代码路径。注意路径前缀约定，引用时连同 commit 一起说明。
2. **不够细时**，读手册 `04-projects/<类别>/<项目>.md` 的"对各机制的回答"一节（按判停、打断与截断、首音优化、工具回合、会话恢复与上下文同步、音频前处理、评测逐条作答）；要入口函数时看同页"关键代码路径"表，要坑时看"取舍与局限"。
3. **要横比多个项目**，读手册 `03-mechanisms/<机制>.md` 的"各项目怎么做"一节（页面末尾、"我们的判断"之前的表），机制文件名：
   - 判停 `turn-detection.md`；打断与截断 `interruption.md`；首音优化 `first-audio.md`；工具回合 `tool-calls.md`；会话恢复与上下文同步 `session-recovery.md`；音频前处理 `audio-preprocessing.md`；评测 `evaluation.md`
4. **要选型对比**，读 `05-comparison/framework-matrix.md`（框架五张表 + 差异性质速查）和 `05-comparison/model-matrix.md`（模型表 + 口径说明），再看 `05-comparison/decision-guide.md`。
5. **项目页也不确定时**，去源码核对：`/home/wang/workspace/voice-agent/<repo>/`，以项目页记的 commit 为准；项目页写"待确认"的，回答时照说待确认，不要补。

识别用户用的是哪个框架（评审代码时）：看 import 和核心类名，第 2 节每个框架开头列了"识别特征"。smart-turn、ten-vad、rnnoise、aiortc 常以框架插件形式出现（pipecat `LocalSmartTurnAnalyzerV3`、`RNNoiseFilter`、SmallWebRTC；TEN `ten_vad_python`）。

口径提醒（回答时要带上）：
- "截断"各页含义不同：livekit-agents 按播放位置改上下文并通知上游；pipecat OpenAI Realtime 按收到音频的墙钟；qwen-audio-agent 只到"有没有开始播"；unmute 按 TTS 词 `start_s`（服务端时钟）。
- 判停默认延迟定义不同：pipecat VAD 0.2 s 后跑模型，livekit-agents 端点等待 0.5 s 起，xiaozhi-esp32-server 200 ms 静音，openai-realtime-agents 服务端 500 ms，不能直接比快慢。
- 评测"有"不等于测过语音链路：pipecat eval、livekit `AgentSession.run`、qwen-audio-agent 任务完成率默认都是文本口径。
- 并发能力没有任何项目给出"单机几路"实测，要自己压测。
