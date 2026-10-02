# 框架对比矩阵

> 状态：草稿
> 最后更新：2026-10-02

对象：[04-projects](../04-projects/README.md) 中 frameworks 下 6 个、full-duplex 下 2 个，加 [xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md)。表中每格都取自对应项目页，版本以项目页标注的 commit / tag 为准；项目页没有依据的写"待确认"。本页不下新结论，只汇总和标注差异性质。

阅读提示：

- "不涉及"表示项目页明确说该项目不覆盖这一层；"待确认"表示项目页没有给出依据。
- 维度按主题拆成五张表，每张表后的说明标出哪些差异是本质的、哪些只是实现进度；先看下面的速查表可以跳过细节。
- moshi 是模型，不是编排框架，放进来是作为"全双工把编排问题吸收进模型"的参照；qwen-audio-agent、openai-realtime-agents 假定上游是实时模型，没有 STT / TTS 管线。

## 差异性质速查

各节说明里的判断汇总如下。"本质"指由架构选择或上游 / 模型结构决定、补代码也改不了的差异；"进度"指同类项目已经实现、这个项目还没做或做得浅的差异。

| 维度 | 本质差异 | 实现进度差异 |
|---|---|---|
| 调度单位 | 帧（pipecat）/ 发言（livekit-agents）/ 中心扩展（TEN）/ 状态机（unmute）/ 模型内（moshi） | — |
| 链路 | moshi 原生全双工；qwen-audio-agent、openai-realtime-agents 只做 S2S 前台 | pipecat-flows 的 S2S 待确认 |
| 并发 | 一会话一进程（livekit-agents、TEN）vs 进程内协程或线程（pipecat、xiaozhi-esp32-server）vs 浏览器（openai-realtime-agents） | TEN 无预热、无负载计算 |
| 判停 | 模型内（moshi）vs 绑定特定 STT（unmute）vs 模型外；S2S 下能否外置受上游约束 | TEN 默认只用 ASR final |
| 截断 | xiaozhi 设备协议没有播放回执 | TEN truncate 被注释、pipecat 未扣播放缓冲、qwen-audio-agent 未接 truncate |
| 工具 | moshi 无系统提示和工具训练 | TEN、openai-realtime-agents 无异步工具 |
| 上下文同步 | 本地是否持有真相：镜像 + diff / 只同步工具结果 / 以服务端为准 | 重连策略完整度 |
| 输出仲裁 | 发言级仲裁（livekit-agents）vs 帧级清队列（pipecat） | 多数项目没有多出声来源的仲裁 |
| 中文 | unmute、moshi 模型只训练了英语（法语） | 其余框架语言中立 |

## 一、形态

| 项目 | 语言与运行形态 | 支持的链路 | 部署形态与并发模型 |
|---|---|---|---|
| [pipecat](../04-projects/frameworks/pipecat.md) | Python，帧驱动 pipeline，`FrameProcessor` 串链 | 级联、S2S（OpenAI Realtime / Gemini Live / Nova Sonic 等）、半级联（`examples/realtime/realtime-openai-text.py`、`realtime-ultravox-text.py`：上游只出文本，接本地 TTS）；前台委派（`OpenAILiveLLMService`） | 单进程 asyncio，一个 `PipelineWorker` 跑一个会话；部署和并发调度交给外部 runner |
| [pipecat-flows](../04-projects/frameworks/pipecat-flows.md) | Python，叠在 pipecat 上的节点图状态机 | 级联；S2S 上没有示例，节点切换能否生效待确认 | 同 pipecat |
| [livekit-agents](../04-projects/frameworks/livekit-agents.md) | Python，以 `SpeechHandle` 调度的语音运行时，agent 以参与者身份进 LiveKit 房间 | 级联、S2S（`RealtimeModel`）、半级联（文本模态 + 会话 TTS）；`DuplexModel`（GPT-Live）被包成 RealtimeModel | worker 管预热进程池，一进程一个 job；按 CPU 负载接单（阈值 0.7）；turn detector 跑在 worker 级共享推理进程 |
| [ten-framework](../04-projects/frameworks/ten-framework.md) | C 运行时 + Python / Go / Node.js / C++ 绑定；拓扑写在 `property.json` 的 graph 里 | 级联、S2S（`v2v` 节点） | Go 服务端每个 channel 起一个 worker 进程；`WORKERS_MAX` 限流，无负载计算、无预热；Docker Compose |
| [openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md) | TypeScript / Next.js 示例，浏览器经 WebRTC 直连 Realtime API（Agents SDK 0.0.5） | S2S；chat-supervisor 委派后台文本模型；handoff | 浏览器端运行，工具也在浏览器执行；服务端只代发临时密钥、代理 Responses API |
| [qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md) | 纯 JavaScript ESM，Node.js Gateway + 编排运行时 | S2S 前台 + 后台 agent（ACP / A2A）；不提供 STT / LLM / TTS 管线 | 客户端经一条 WebSocket（另有 WebRTC 示例）连 Gateway；任务按 owner FIFO，通知领取带租约防多连接重复播报 |
| [unmute](../04-projects/full-duplex/unmute.md) | Python FastAPI 编排 + Rust `moshi-server`（STT / TTS）+ vLLM | 级联（流式 STT / TTS），服务端状态机做成近全双工 | Docker Compose 五个服务；每连接一个 `UnmuteHandler`，每个后端进程最多 4 个客户端；STT / TTS batch 满了拒绝新连接 |
| [moshi](../04-projects/full-duplex/moshi.md) | 模型 + 三套推理（PyTorch / MLX / Rust）+ Rust `moshi-server` | 原生全双工 | PyTorch 服务端全局锁、单会话；Rust 每连接一条推理线程，360 s 超时，不做跨会话批处理 |
| [xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md) | Python，asyncio 主循环 + 每连接线程池 + ASR / TTS 常驻线程，队列串联；全模块部署另有 Java 管理端和 Vue 前端 | 级联 | 一个 WebSocket 连接一个 `ConnectionHandler`；最简化（只跑 Python）或全模块（多用户多智能体）两种部署 |

**说明**

- **本质差异：调度单位。** pipecat 调度的是帧（打断 = 高优先级帧广播 + 各处理器清队列），livekit-agents 调度的是"发言"（优先队列，同一时刻只授权一个 `SpeechHandle`），TEN 的 graph 只是布线，编排写在中心扩展 `main_control` 里，unmute 是一个三态状态机，moshi 没有外部调度。后面打断、工具、仲裁各维度的差异大多从这里派生。
- **本质差异：链路覆盖。** moshi 是唯一的原生全双工；unmute 用流式 STT / TTS 在级联上逼近全双工。qwen-audio-agent 和 openai-realtime-agents 的前提是"前台就是实时模型"，不打算支持级联。
- **本质差异：并发模型。** livekit-agents、TEN 是一会话一进程（隔离好、单会话开销高），pipecat、xiaozhi-esp32-server 是进程内协程或线程，openai-realtime-agents 把会话放在浏览器。livekit-agents 有预热进程池，TEN 每次 `/start` 冷启动：这一条是实现进度，不是架构限制。
- pipecat 的半级联依据是 [半级联架构页](../02-architectures/half-cascade.md) 和 pipecat 422ad13 的 `examples/realtime/realtime-openai-text.py`（`output_modalities=["text"]` + Cartesia TTS），pipecat 项目页尚未写到这一点；pipecat-flows 的 S2S 支持项目页没有给出依据，表里标待确认。

## 二、判停与打断

| 项目 | 判停方式 | 判停可否外置 | 打断触发与门槛 | 截断粒度 |
|---|---|---|---|---|
| [pipecat](../04-projects/frameworks/pipecat.md) | 默认 Silero VAD（stop 0.2 s）+ 本地 smart-turn v3，判完再等定稿转写或 STT P99 超时；可换纯静音、LLM 标记、服务端判停 | 可以。OpenAI Realtime 可关服务端 VAD 手动 commit；Gemini Live 用 `activity_start/end` 手动划窗；Nova Sonic 只能调灵敏度 | 用户回合开始即广播 `InterruptionFrame`；`MinWords` 等 start 策略设门槛 | 级联词级（按 pts 播到的 `TTSTextFrame` 才进上下文）；OpenAI Realtime 按"收到首个 delta 起的墙钟"发 truncate，未扣播放缓冲；Gemini Live 不截断 |
| [pipecat-flows](../04-projects/frameworks/pipecat-flows.md) | 沿用 pipecat | 沿用 pipecat | 沿用 pipecat | 沿用 pipecat |
| [livekit-agents](../04-projects/frameworks/livekit-agents.md) | VAD + 可选 turn detector（新版只看音频，本地 `v1-mini` / 云端 `v1`）+ 端点等待（默认 0.5 / 3.0 s，可动态学习） | 取决于上游声明的 `can_disable_turn_detection`：OpenAI 可关，Gemini / AWS / Ultravox 不能；会话开始时定一次；`manual` 模式可做按键说话 | 本地 VAD / 自适应打断模型 / 服务端 `speech_started`；`min_duration` 0.5 s，可选 `min_words`，误打断 2 s 后恢复播放 | 按播放位置：记 `playback_position` 和估算的 `synchronized_transcript`（中文按单字）；实时模型需上游声明 `message_truncation`，Gemini / AWS 只改本地 |
| [ten-framework](../04-projects/frameworks/ten-framework.md) | 默认直接用 ASR final；可接 TEN Turn Detection（每个 final 远程调一次 LLM）；ten-vad 示例里只用于打断；S2S 用上游服务端 VAD | S2S 示例未提供外置路径 | 部分转写超过 2 个字符；或 VAD `start_of_sentence`；或判停扩展发 `flush` | 无上下文截断；TTS 按 `request_id` 丢迟到文本；S2S 的 truncate 代码被注释掉 |
| [openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md) | 服务端 `server_vad`（threshold 0.9、静音 500 ms） | 只有按键说话：`turn_detection: null`，松开时 commit + `response.create` | 服务端 VAD；应用层按键或发文本时调 `interrupt()` | WebRTC 下只发 `response.cancel` + 清输出缓冲，不发 truncate；服务端是否自动截断待确认 |
| [qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md) | 全交给上游（`smart_turn` / `semantic_vad` / `server_vad`），本地无 VAD | 否，仓库没有本地判停 | 服务端 `speech_started`、手动文本、客户端 `interrupt`；回合代际 +1、清客户端播放、`response.cancel`，单槽位上游超时则重连 | 不按播放位置截断；本地只记"有没有开始播放"（以客户端 `playback.started` 为准） |
| [unmute](../04-projects/full-duplex/unmute.md) | STT 自带停顿预测头 `prs[2]` 经 EMA > 0.6，再往 STT 灌零帧冲刷约 0.5 s 延迟 | 否，协议里没有 commit | bot 说话时 STT 出任何新词；或停顿概率 < 0.4 且会话已过 3 s | 词级：历史只保留 TTS 按 `start_s` 释放的词；打断符在送 LLM 前去掉 |
| [moshi](../04-projects/full-duplex/moshi.md) | 模型每 80 ms 自己采样填充 token 或开口，无 VAD、无回合事件 | 否；唯一旋钮是 Rust 端 `pad_mult` | 没有打断事件，模型同步听到用户后自己决定停不停 | 无截断概念，上下文就是模型内部缓存 |
| [xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md) | manual：设备松键；auto / realtime：Silero 双阈值 + 默认 200 ms 静音；流式 ASR 以厂商端点为准；无语义判停 | manual 模式由设备决定 | 设备 `abort`；语音打断发生在判停 + ASR 完成之后；设备声明服务端 AEC 时 VAD 有声即打断 | 不截断，被打断的回复全文写入历史；设备不回传播放位置 |

**说明**

- **本质差异：判停在模型内还是模型外。** moshi 把判停和打断交给模型，外部只能偏置；unmute 的"语义 VAD"是 Kyutai STT 特有的输出，换 ASR 就没了。其余项目判停都在模型外。
- **本质差异：S2S 下判停能否外置受上游约束。** pipecat 和 livekit-agents 都提供了外置路径，但能不能关服务端判停由上游决定（livekit-agents 用能力位声明，pipecat 写在各服务里）。qwen-audio-agent、openai-realtime-agents 选择全交给上游，这是设计取舍。
- **截断粒度大多是实现进度。** TEN 的 truncate 代码被注释掉、pipecat 的 OpenAI Realtime 截断不扣播放缓冲、qwen-audio-agent 没接 truncate，都属于"能做没做"。xiaozhi-esp32-server 不同：设备协议没有播放回执（见 [xiaozhi-esp32](../04-projects/devices/xiaozhi-esp32.md)），服务端拿不到截断依据，是协议层的限制。
- **迟到帧防线的做法不同。** 有显式代际的：qwen-audio-agent（回合代际 + 响应代际）、xiaozhi-esp32-server（`sentence_id`）、TEN（TTS 侧 `request_id`）；unmute 用替换输出队列 + 历史长度做隐式代际；pipecat 只在打断瞬间清队列，项目页推断迟到的上游音频会被当成新回复播放。
- 判停延迟的默认值口径不同：pipecat 是 VAD 0.2 s 后跑模型，livekit-agents 是端点等待 0.5 s 起，xiaozhi-esp32-server 是 200 ms 静音（按墙钟计），openai-realtime-agents 是服务端 500 ms。这些数字各自定义不同，不能直接比快慢。

## 三、工具回合

| 项目 | 异步工具 | 跳过再生成 | 执行期出声 | 委派 |
|---|---|---|---|---|
| [pipecat](../04-projects/frameworks/pipecat.md) | 有：`cancel_on_interruption=False`（1.0.0 起），结果晚到以 developer 消息补进上下文，支持中间进度 | `FunctionCallResultProperties.run_llm=False`；并行结果合并为一次推理 | 常见写法是 `on_function_calls_started` 里推 `TTSSpeakFrame`，默认进上下文；S2S 下没有现成本地提示语 | `OpenAILiveLLMService` 的 `ResponsesDelegation` / `ClientDelegation` |
| [pipecat-flows](../04-projects/frameworks/pipecat-flows.md) | 函数默认 `cancel_on_interruption=False`，即异步语义 | 边函数用 `run_llm=False` 回传，切节点后再推理一次 | 节点 `pre_actions: tts_say` 推理前先说固定句 | 不委派给其他模型；节点切换由 handler 代码决定；multi-worker bus 可在自由对话和 Flows worker 间交接 |
| [livekit-agents](../04-projects/frameworks/livekit-agents.md) | 有：首次 `ctx.update()` 即收口，后台续跑，结果等会话空闲后合并回帖 | `reply_required=False`、`StopResponse`、`cancel_tool_reply()` | `session.say()`（可不进上下文、可传预合成音频）、`with_filler`；工具执行期间调度器已让出 | GPT-Live 插件 `delegation="responses" \| "client"` |
| [ten-framework](../04-projects/frameworks/ten-framework.md) | 无，`await` 结果后串行再调 LLM | 结果类型为 `llmresult` 时再调 LLM，其他类型的行为待确认 | 无填充语或进度播报 | 无 |
| [openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md) | 无 | 无 `run_llm` 一类开关，执行完总是 output + `response.create` | 靠 prompt 要求前台先说填充语，代码无兜底 | chat-supervisor：前台把请求交给 `gpt-4.1`（同步、非流式）；handoff 换 instructions 和 tools |
| [qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md) | 有：`spawn_thinking` 立即回 `accepted`，后台完成后在安全窗口注入 + `response.create` | 同一 response 已说过话且工具不要求结果摘要时不再生成；可选带外 response 不进上下文 | 刻意不填充；进度播报首条 ≥ 60 s；UI 状态 `voice.state=processing` | 核心能力：经 `BackendPort` 走 ACP 或 A2A 交给后台 agent |
| [unmute](../04-projects/full-duplex/unmute.md) | 不涉及，代码里没有工具调用 | 不涉及 | 不涉及 | 不涉及；README 建议把工具藏在 LLM 服务端 |
| [moshi](../04-projects/full-duplex/moshi.md) | 不涉及，协议无工具事件，模型无工具训练 | 不涉及 | 不涉及 | 不涉及 |
| [xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md) | 无；多个调用并行执行、逐个等待，单个 30 s 超时 | 工具返回 `RESPONSE` 直接念结果，`RECORD` / `NONE` 不再生成 | 只有 tool_call 之前流出的文本会先播，无填充语 | 无；`direct_answer` 虚拟工具把"调不调"变成"调哪个" |

**说明**

- **异步工具的有无多半是实现进度。** pipecat、livekit-agents、qwen-audio-agent 都实现了"先收口、后回帖"，TEN 和 openai-realtime-agents 没有。三家的回帖条件不同：pipecat 在 bot 说完后推上下文，livekit-agents 等会话完全空闲，qwen-audio-agent 用三条件窗口加客户端播放回执。
- **本质差异：工具在谁手里。** unmute 和 moshi 没有工具层：unmute 是有意保持薄（可在 LLM 服务端补），moshi 是模型本身没有系统提示和工具训练，属于结构限制。
- **委派的形态不同，不是同一个功能。** openai-realtime-agents 是同步调用文本模型，结果由前台再生成；qwen-audio-agent 是长期运行的后台 agent，结果异步回流；livekit-agents 和 pipecat 的委派都依托 GPT-Live / OpenAI Live 一类前台模型。项目页给出的唯一实测数据来自 qwen-audio-agent：短指令"语音结束 → 工具开始执行"，前台直连 1.317 s，后台委派 3.363 s。
- "执行期出声"默认是否进上下文各家不同：pipecat 的 `TTSSpeakFrame` 默认进，livekit-agents 的 `say()` 可选不进，openai-realtime-agents 的填充语是模型生成的普通消息，必然进。

## 四、S2S 封装、输出仲裁与评测

| 项目 | S2S 上游封装与重连 | 输出仲裁 | 评测工具 |
|---|---|---|---|
| [pipecat](../04-projects/frameworks/pipecat.md) | 本地上下文是镜像，只 diff 新完成的工具结果发上游；OpenAI Realtime 不自动重连，靠 `reset_conversation` 手动重建；Gemini Live 3 次内自动重连带 resumption handle；Nova Sonic 默认 360 s 主动换会话 | 无发言级仲裁；三级帧优先级队列 + 打断清队列 | `pipecat eval`：脚本 / LLM 模拟用户，可断言工具调用、插话、`within_ms` 时延预算，文本或音频模态；`run_test` 单元工具 |
| [pipecat-flows](../04-projects/frameworks/pipecat-flows.md) | 沿用 pipecat；Flows 依赖的帧在 OpenAI Realtime / Gemini Live 上未实现或无动作 | 沿用 pipecat；动作由 `ActionManager` 串行 | 基于 `pipecat eval` 对 8 个示例跑行为评测，默认文本模态 |
| [livekit-agents](../04-projects/frameworks/livekit-agents.md) | 能力位 `RealtimeCapabilities` 决定行为；OpenAI 维护服务端镜像 + LCS diff，重连重放镜像（排除工具调用）；Gemini 用 resumption handle；`RealtimeModelFallbackAdapter` 跨模型兜底 | 有：`SpeechHandle` 优先队列（LOW / NORMAL / HIGH），串行授权 | `AgentSession.run` + `RunResult` 断言与 judge、整段 judge、仿真；无判停或首音基准 |
| [ten-framework](../04-projects/frameworks/ten-framework.md) | `openai_mllm_python`：断线 1 s 后重连，无退避无上限；`_resume_context` 逐条重发；无镜像、无 diff、无错误分级 | 无：graph 按到达顺序投递，无优先级无代际 | 扩展级单测 `AsyncExtensionTester`；ASR / TTS 协议守护测试；无对话 judge 或延迟基准 |
| [openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md) | SDK 由服务端事件重建本地 history 镜像，只用于 UI 和后台；无重连逻辑 | 无专门调度层，由服务端决定 | 无 |
| [qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md) | Provider + Protocol 适配 7 家上游并声明能力位；指数退避重连（500 ms 起、上限 10 s、带抖动）；重连后以一条 `<restored_context>` user 条目恢复近期历史；内容安全拒绝时隔离出错回合再重建 | 无统一仲裁器；串行输出队列 + 响应代际 + `AnnouncementWindow` 三条件放行 | 智能座舱延迟基准；tau2-bench 改编的任务完成率（文本口径）；provider 行为测试 |
| [unmute](../04-projects/full-duplex/unmute.md) | 不涉及；断线即丢历史 | 单路输出，打断时替换输出队列 | 压测客户端统计 stt / vad / llm / tts_start 分段延迟；Prometheus + Grafana |
| [moshi](../04-projects/full-duplex/moshi.md) | 不涉及；断线即重置，会话约 5 分钟硬上限（Rust / MLX） | 不涉及，模型内部决定 | 按步计时的性能基准和单元测试，无对话质量评测 |
| [xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md) | 不涉及；断线不恢复，跨会话靠记忆模块 | 无；线程 + 队列 + 共享标志，`sentence_id` 过滤 | `performance_tester` 测各 provider 耗时（只算均值）；provider 契约测试 |

**说明**

- **本质差异：上下文以谁为准。** livekit-agents 维护服务端镜像并做 diff，pipecat 只同步工具结果，qwen-audio-agent 尽量不刷新会话（避免 instructions 前缀缓存失效），openai-realtime-agents 完全以服务端为准。这是对"本地是否持有真相"的不同回答，不只是实现完整度。
- **重连策略的差距是实现进度。** 同为 OpenAI Realtime 封装，livekit-agents 自动重连并重放镜像，pipecat 要手动 `reset_conversation`，TEN 固定 1 s 重试无上限，openai-realtime-agents 没有重连。
- **输出仲裁只有 livekit-agents 做成了显式机制。** qwen-audio-agent 用阻塞条件的松紧表达优先级，其余项目要么靠队列清空，要么没有多个出声来源。多来源（工具填充语、异步结果、进度播报）越多，这一项越关键。
- **评测覆盖的层次不同。** pipecat 和 livekit-agents 测对话行为，qwen-audio-agent 测委派延迟和任务完成率，unmute 和 xiaozhi-esp32-server 只测延迟或耗时，TEN 测协议一致性。除 pipecat 的 `evals/turn-completion/`（检查 LLM 输出的判停标记）外，没有项目提供判停准确率基准。

## 五、许可、中文与成熟度

| 项目 | 许可证 | 中文支持 | 版本与活跃度（仓库可见） |
|---|---|---|---|
| [pipecat](../04-projects/frameworks/pipecat.md) | BSD-2-Clause | 分句器含中文句末标点；默认判停 smart-turn 声称支持中文，效果待确认 | commit 422ad13，CHANGELOG 最新 1.12.0；大量 deprecated 参数，演进快 |
| [pipecat-flows](../04-projects/frameworks/pipecat-flows.md) | BSD-2-Clause | 沿用 pipecat | 1.4.0（2026-07-05），独立包最后一版，已冻结；1.5.0 起并入 pipecat |
| [livekit-agents](../04-projects/frameworks/livekit-agents.md) | Apache-2.0；Krisp 降噪插件为商业授权 | turn detector 有 `zh` 阈值 0.355；中文准确率待确认 | commit e7e7783，1.8.3；75 个插件包 |
| [ten-framework](../04-projects/frameworks/ten-framework.md) | Apache-2.0 加附加条款（不得部署在终端用户设备、不得与 Agora 产品竞争） | 待确认 | commit ca00160c，0.11.73 |
| [openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md) | MIT | 待确认 | commit 94c9e91（2026-01-07）；SDK 锁 0.0.5，示例和 README 有漂移 |
| [qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md) | Apache-2.0 | 默认前台 Qwen Audio 3.0 Realtime；受理回执和结果注入指令为中文 | commit f6dd0e3，v2.0.x |
| [unmute](../04-projects/full-duplex/unmute.md) | MIT | 不支持，只有英语和法语；按空白切词对中文无效 | commit e348e56，项目页未记版本号 |
| [moshi](../04-projects/full-duplex/moshi.md) | 权重 CC-BY 4.0；代码 Python 部分 MIT、Rust 后端 Apache-2.0 | 不支持，只说英语 | commit e6a55d2，项目页未记版本号 |
| [xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md) | MIT | 以国内服务和中文模型为主（讯飞、豆包、阿里、FunASR、SenseVoice 等） | commit 87c6df77（2026-09-29） |

**说明**

- 许可证一列取自各仓库 LICENSE 文件（代码许可）。TEN 的附加条款对端侧部署有实质限制，选型时要单独看。
- **中文支持里本质的只有两条**：unmute 和 moshi 的模型只训练了英语（法语），换语言要换模型；其余框架对语言是中立的，中文能力取决于接的 ASR / TTS / 实时模型。框架层面跟中文有关的只是分句标点、判停模型阈值这类细节。
- 版本号的口径不统一：pipecat 写的是 CHANGELOG 最新版本，livekit-agents 写的是包版本，unmute、moshi 只有 commit。项目页没有记录提交频率或发布节奏，"活跃度"只能读出 pipecat-flows 已冻结、pipecat 弃用项多这类信号。
- 项目页给出的规模信号也可作为成熟度的旁证：livekit-agents 仅 `voice/agent_activity.py` 就有五千多行，pipecat `services/` 下有 73 个厂商目录、`tests/` 下 282 个测试文件；unmute 的核心逻辑集中在一个 651 行的文件；openai-realtime-agents 自述是参考配置而非运行时，仓库没有测试。
- 关键依赖不在仓库里的：TEN 的 `agora_rtc`、`ten_ai_base`，openai-realtime-agents 的 Agents SDK。这些项目的部分行为在项目页里只能从调用方推断。

## 项目页之间的口径差异

- **"截断"的含义不一致。** livekit-agents 页的截断指"按播放位置改写上下文并通知上游"；pipecat 页对 OpenAI Realtime 的截断按"收到音频的墙钟时间"计；qwen-audio-agent 页的截断只到"有没有开始播放"；unmute 页按 TTS 回吐词的 `start_s`，以服务端时钟为准，没有扣网络和客户端缓冲。表里照录，不能横向比精度。
- **"判停延迟"各页定义不同**，见第二节说明最后一条。
- **委派的时延**只有 qwen-audio-agent 页给出实测（"语音结束 → 工具开始执行"），openai-realtime-agents 页引 README 的"填充语播完到有内容约 2 s"，两者起止点不同。
- **版本标注**：有的写 CHANGELOG 版本，有的写包版本，有的只写 commit，见第五节。
- **并发能力没有可比数字。** unmute 页给的是每进程客户端上限（4）和 STT / TTS batch 配置，moshi 页给的是服务端结构（全局锁 / 每连接一线程），livekit-agents 页给的是接单负载阈值，TEN 页给的是 `WORKERS_MAX` 限流。没有一页给出"单机能撑几路"的实测，选型时要自己压测。
- **评测结果的模态不同。** qwen-audio-agent 页引用的任务完成率是文本输入输出口径，不含 ASR / TTS；pipecat、pipecat-flows 的 `pipecat eval` 默认也是文本模态，可切音频；livekit-agents 的 `AgentSession.run` 注入的是文本。表中"有评测"不等于"测过语音链路"。

## 相关

- 项目页：[pipecat](../04-projects/frameworks/pipecat.md)、[pipecat-flows](../04-projects/frameworks/pipecat-flows.md)、[livekit-agents](../04-projects/frameworks/livekit-agents.md)、[ten-framework](../04-projects/frameworks/ten-framework.md)、[openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md)、[qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md)、[unmute](../04-projects/full-duplex/unmute.md)、[moshi](../04-projects/full-duplex/moshi.md)、[xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md)
- 机制页：[判停](../03-mechanisms/turn-detection.md)、[打断与截断](../03-mechanisms/interruption.md)、[首音优化](../03-mechanisms/first-audio.md)、[工具回合](../03-mechanisms/tool-calls.md)、[会话恢复与上下文同步](../03-mechanisms/session-recovery.md)、[评测](../03-mechanisms/evaluation.md)
- 对比页：[model-matrix](model-matrix.md)、[decision-guide](decision-guide.md)
