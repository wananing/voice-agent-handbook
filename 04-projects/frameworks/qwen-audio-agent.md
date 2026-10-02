# qwen-audio-agent

> 仓库：https://github.com/QwenAudio/qwen-audio-agent（README 徽章与协议文档所指；本地 clone 的 remote 是 fork `github.com/wananing/qwen-audio-agent`）
> 许可：Apache-2.0（见仓库 LICENSE 文件）
> 分析基于：commit f6dd0e3（v2.0.x）
> 状态：草稿
> 最后更新：2026-10-02

纯 JavaScript ESM（`.mjs`）项目。下文路径默认相对 `server/src/`。

## 定位

一个"前台实时语音 Agent + 编排运行时 + 后台办事 Agent"的语音运行时，论文为 arXiv 2609.25195（本文未读，只依据代码和仓库文档）。分工如下：

- **前台**：某个 Realtime 模型，默认 Qwen Audio 3.0 Realtime，负责聊天和轻量工具。
- **后台**：遇到需要操作环境、耗时长或要产出交付物的请求，前台用一个工具 `spawn_thinking` 交出去。执行方是通过 ACP 接入的编码 agent（Qwen Code、OpenCode、Claude Code 等，见 `docs/backends/overview.zh.md`）或远程 A2A Agent。
- **编排运行时**：前台交出任务后立刻拿到"已受理"回执，继续对话。后台完成后，运行时在"安全窗口"把结果注入前台，由前台模型用自己的声音说出来。

它要解决的问题是 README 说的 "Agent Presence"：后台在干活时，对话不卡住。和 [livekit-agents](livekit-agents.md)、[pipecat](pipecat.md) 不同，它不提供 STT/LLM/TTS 管线，假定前台就是一个端到端实时模型。它的重点在任务、播报和上下文投递的编排。和 [openai-realtime-agents](openai-realtime-agents.md) 的 chat-supervisor 模式相比，它的后台是独立长期运行的 agent 进程，不是同步调用的文本模型；结果异步回流。

## 整体架构

```
Client（web / desktop / tui / mobile）── Gateway 协议（一条 WebSocket；另有 WebRTC 示例）
   │  上行音频 / 文本；回传 playback.started / ended / cancelled
   ▼
Gateway（Node.js）
   ├─ RealtimeFrontend ──► 上游 Realtime（服务端判停 + 模型）
   │     ├─ 直接回答 → 音频 → Client 播放
   │     └─ function_call spawn_thinking(objective)
   │           → AgentTaskRuntime：TaskOperations.submit → 回 {status:'accepted'}
   │           → 工具批次结束 → 前台再生成一句"自然确认"
   ├─ TaskOperations / TaskManager（任务唯一权威，按 owner FIFO）
   │     → BackendPort（ACP / A2A / 自定义 Adapter）→ 后台 Agent 执行
   │     → 完成通知
   └─ SessionTaskCoordinator（领取通知，带租约）
         → AnnouncementManager 等 AnnouncementWindow 放行
         → 注入 role=user 结果条目 + response.create(tool_choice:none)
         → 前台模型说结果 → Client playback.started → 确认送达
```

**核心抽象**

| 抽象 | 位置 | 职责 |
|---|---|---|
| `RealtimeFrontend` | `voice/realtime-provider.mjs:RealtimeFrontend` | 一条上游连接：会话配置、对话项、`response.create/cancel`、串行输出队列、响应代际 |
| Provider + Protocol | `voice/providers/*.mjs`，契约校验在 `voice/providers/provider-registry.mjs` | 各厂商方言适配和能力位：DashScope、GPT-Live、Google Live、Doubao、StepFun、HF s2s、MiniCPM-o |
| `ToolCallHandler` / `AgentTaskRuntime` | `frontend/tools/tool-call-handler.mjs`、`frontend/tools/agent-task-runtime.mjs` | 前台工具执行、回执，以及工具批次结束后要不要再生成 |
| `TaskOperations` | `orchestration/task-operations.mjs:TaskOperations` | 提交、取消、权限、补充输入 |
| `SessionTaskCoordinator` | `orchestration/session-task-coordinator.mjs:SessionTaskCoordinator` | 每条前台连接订阅任务事件，领取通知，分发给结果播报、进度播报、权限 |
| `AnnouncementManager` / `ProgressAnnouncementManager` / `AnnouncementWindow` | `voice/announcement/` | 结果播报队列、进度节流、插话窗口 |
| `AgentDelivery` | `delivery/agent-delivery.mjs:createAgentDelivery` | 与厂商无关的"给前台模型的输入"，四种模式 `handle / context / respond / interrupt` |
| `BackendPort` | `backend/backend-port.mjs:BACKEND_PORT_METHODS` | 后台协议隔离：`submit / status / cancel / respondAuthorization / respondInput / subscribe` 等 |

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 委派入口 | `frontend/tools/spawn-thinking-tool.mjs` | 只有 `objective`（必须自包含）和可选 `input_refs` 两个参数 |
| 受理与去重 | `frontend/tools/agent-task-runtime.mjs:AgentTaskRuntime.executeSpawnThinkingToolCall` | 回 `accepted` / `duplicate`；同一用户回合只认第一个发起 spawn 的 response |
| 工具批次收尾 | `frontend/tools/tool-call-handler.mjs:ToolCallHandler.flushDeferredToolResponse` | 全部调用完成后决定要不要 `ensureResponse({afterToolResults:true})` |
| 过期调用收口 | `ToolCallHandler.closeStaleCall` | 旧回合的工具调用回 `superseded`，不触发生成 |
| 结果注入 | `voice/realtime-provider.mjs:RealtimeFrontend.injectDelivery` | 进串行队列，等 `whenIdle()`，写条目再发 `response.create` |
| 注入内容 | `voice/announcement/announcement-manager.mjs:formatWorkResults`；`voice/providers/dashscope.mjs` 的 `buildResultInjection` | role=user 文本 + `tool_choice:'none'` + 专用指令 |
| 插话窗口 | `voice/announcement/announcement-window.mjs:AnnouncementWindow.isBlocked` | 用户在说 ∨ 回合未结束 ∨ 有已生成未播完的音频 |
| 送达确认 | `voice/realtime-presentation-runtime.mjs:RealtimePresentationRuntime.startPlayback` | 以客户端 `playback.started` 为准 |
| 进度播报 | `voice/announcement/progress-announcement-manager.mjs:ProgressAnnouncementManager` | 首条 ≥60 s、间隔 ≥60 s、新覆盖旧、被打断不重播 |
| 打断 | `voice/realtime-input-runtime.mjs:RealtimeInputRuntime.#startSpeech` | 回合代际 +1，清客户端播放，`frontend.cancel()` |
| 取消与单槽位恢复 | `RealtimeFrontend.cancel`、`voice/realtime-response-slot.mjs:RealtimeResponseSlot.recover` | 取消超时就断开重连 |
| 近期历史恢复 | `RealtimeFrontend.restoreRecentConversation` | 建连后插入一条 `<restored_context>` user 条目 |
| ACP 后台会话 | `backend/adapters/acp/backend-adapter.mjs`、`backend-session-utils.mjs` | 每个 owner 一个协调 Session（key 为 `<protocol>:<owner>:backend`，`backend-session-utils.mjs:coordinatorKey`），用 `resumeSession` 接续 |
| 提供方行为测试 | `server/test/realtime-provider-behavior.test.mjs` | 本地 WebSocket 模拟各厂商协议 |

## 对各机制的回答

### [判停](../../03-mechanisms/turn-detection.md)

- 完全交给上游服务端。Provider 在 `buildSession` 里写 `session.turn_detection`，默认值来自模型目录（仓库根 `shared/realtime-model-catalog.mjs`）：Qwen Audio 系列是 `{type:'smart_turn'}`，Qwen Omni 系列是 `{type:'semantic_vad'}`，部分模型是 `server_vad`。这几个类型在服务端的具体含义，待确认。
- 仓库里没有本地 VAD 或判停模型，搜不到 silero 或 ten-vad。客户端只采集和上传音频。

### [打断与截断](../../03-mechanisms/interruption.md)

**入口与动作**

- 打断入口有三个：服务端 `speech_started`（`RealtimeInputRuntime.#startSpeech`）、手动文本输入、客户端显式 `interrupt`。
- 三者动作一致：
  1. 回合代际 +1；
  2. `announcementWindow.beginTurn()`；
  3. `announcements.dismissActive()`；
  4. 给客户端发 `PLAYBACK_CLEAR`；
  5. `frontend.cancel()`：`responseQueueGeneration` +1，作废所有排队和 pending 的 response，并发送 `response.cancel`。

**单槽位上游**：DashScope 这类上游声明了 `singleResponseSlot`。取消先发 `response.cancel`，超过 `responseCancelGraceMs` 还没释放槽位，就断开重连（`RealtimeResponseSlot.recover`）。

**迟到事件**：取消期间到达的 response 事件会被标记为 suppressed；过期回合的工具调用回 `{status:'superseded'}` 收口。

**截断：不按播放位置截断上游上下文。** 仓库里搜不到 `conversation.item.truncate` 或 `audio_end_ms`。本地记录的粒度是"有没有开始播"：

- 助手转写先缓存，收到 `playback.started` 才写入会话记录；
- 被取消的 response 清空待写转写；
- 已开始播放后被打断的，最终转写仍完整写入。

**播报被打断时**：结果播报被用户打断，视为已送达，不重播。注意 `dismissActive()` 在用户开口时就会把当前批次确认为已送达，这包括"上下文已注入、但还没开始播"的情况（`announcement-manager.mjs:AnnouncementManager.dismissActive`）。

### [首音优化](../../03-mechanisms/first-audio.md)

- 普通回合的首音取决于上游实时模型，运行时没有额外优化。
- 委派回合不做首音优化，而且刻意不说话：前台 Prompt 要求"调用 `spawn_thinking` 前不要口头回应"，收到回执后"只作一次自然确认……不要用话语填补等待"（仓库根 `config/frontend-agent/PROMPT.md`）。
- 工具执行期间没有本地提示音或填充语，只给客户端发 `voice.state=processing` 驱动 UI 动画（`RealtimePresentationRuntime.markFunctionCall`）。各客户端是否另有本地提示音，按关键词没搜到，待确认。
- 它的 benchmark 显示了委派的代价。智能座舱示例对 92 个需要工具的短指令回合，测"语音结束 → 工具开始执行"：前台直连均值 1.317 s，后台委派 3.363 s（仓库根 `examples/smart-cockpit/bench/results/voice-surface-short-20260911.json.md`）。所以路线图规定：延迟预算 < 2 s 的单步工具留在前台（`docs/frontend-agent-roadmap.md`）。

### [工具回合](../../03-mechanisms/tool-calls.md)

**委派回合的时序**

1. 前台模型发 `function_call spawn_thinking(objective)`。
2. `AgentTaskRuntime.executeSpawnThinkingToolCall` 提交任务，立刻回 `{status:'accepted', task_id, message:'工作已受理，请自然确认一次，不要再次调用工具。'}` 作为 function_call_output。调用当场收口。
3. 同一 response 的所有工具都完成后，`ToolCallHandler.flushDeferredToolResponse` 决定是否 `response.create`。如果模型在发出调用的同一 response 里已经说过话，而且该工具不要求结果摘要（spawn、权限、取消都不要求，见 `needsToolResultSummary`），就不再生成第二轮。
4. 后台按 owner FIFO 执行，经 `BackendPort` 走 ACP 或 A2A。前台的打断、休眠、断连都不会取消已受理的任务，只有 `cancel_agent_task` 会取消。

**结果回到对话**

1. 完成通知由 `SessionTaskCoordinator.claimPendingNotifications` 领取，带可续期租约，防止多连接重复播报。
2. `AnnouncementManager` 把多个结果合并成一批，等 `AnnouncementWindow` 放行：用户没在说话、当前回合已结束（包括不在等工具续答）、没有已生成但未播完的音频。被挡住时每 1 s 重试一次。
3. 放行后经 `RealtimeFrontend.injectDelivery` 插入一条 role=user 文本，开头是"以下是你先前异步执行工作的最终更新，不是用户的新请求……"，然后发 `response.create(tool_choice:'none')`，由前台模型重新生成并说出。
4. 送达以客户端 `playback.started` 为准；`response.done` 后迟迟收不到播放回执就重试，有次数上限。
5. 另一条路径：配置关掉注入上下文时改用 `buildSpeakResponse`（`conversation:'none'` 的带外 response），结果不进上下文。仓库里没有"本地 TTS 直接念结果"的通道。

**其他工具回合**

- 后台要追问（权限、补充输入）：以标签注入前台，并临时暴露 `respond_permission` / `respond_agent_input` 工具。权限请求的上下文立即写入，出声排队（`RealtimeFrontend.injectPermission`）。
- 前台同步工具（`web_search`、`memory`、MCP 等）走普通的"回传 → 再生成"。
- 工具结果回传后由服务端自动续答的上游（Doubao、Google）声明 `automaticToolResponses`，运行时不再发 `response.create`。
- 本项目的 GPT-Live provider 连的是 OpenAI Realtime GA 接口（`wss://api.openai.com/v1/realtime`，默认模型 `gpt-realtime-2.1`，见 `shared/realtime-provider-definitions.mjs`、`shared/realtime-model-catalog.mjs`），走 `gaRealtimeProtocol`；`voice/providers/gpt-live.mjs` 没声明 `automaticToolResponses`（默认 false，`voice/realtime-provider.mjs`），所以 `sendFunctionOutput` 写入 `function_call_output` 后要自己发 `response.create`，传 `createResponse:false` 就能静默收口。这和 livekit-agents 项目页说的"GPT-Live 做不到静默收口"不矛盾：那边是另一个上游 `gpt-live-1`（`/live/sessions` 委派协议），见 [livekit-agents](livekit-agents.md)。

### [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)

- **状态走 instructions**：固定 Prompt + 人设 + 记忆 + `<runtime_context>`。
- **尽量不刷新会话**：`RealtimeFrontend.updateAgentContext(patch, {refreshSession:false})` 只更新缓存，不重发 `session.update`。注释给的理由是 instructions 属于 prompt 前缀，重发会让前缀缓存失效。
- **近期历史**：建连并收到 `session.updated` 后，`restoreRecentConversation` 把本地记录的近期对话拼成一条 `<restored_context>` user 条目写入，写完才标记 ready。上游声明 `restoreConversationContext=false` 时跳过，适用于注入历史会被当成实时输入的厂商。这种做法的效果，仓库里没有评测，待确认。
- **断线重连**：指数退避，基数 500 ms，上限 10 s，带抖动（`voice/reconnect-backoff.mjs:ReconnectBackoff`）。连接稳定一段时间后计数清零（`voice/realtime-provider-session.mjs`）。
- **内容安全拒绝**：把出错的 turn / task 从可回放历史里隔离（`voice/realtime-recovery-context.mjs:RealtimeRecoveryContext`），重建会话，再注入一条 `realtime.content_rejected` 系统事件让模型说明情况。逻辑在 `voice/realtime-session-runtime.mjs:createRealtimeSessionRuntime` 的错误处理分支里。
- **后台上下文是另一条线**：ACP 协调 Session 按 owner 固定，跨语音会话复用（`resumeSession`）。前台历史不转发给后台，只转发自包含的 `objective`。

### [音频前处理](../../03-mechanisms/audio-preprocessing.md)

- 服务端不做前处理。Web 客户端靠浏览器 `getUserMedia` 的 `echoCancellation`、`noiseSuppression`、`autoGainControl` 三个约束，全部为 true（仓库根 `web/src/realtime/useRealtimeVoice.js`）。
- 桌面端、移动端怎么处理，待确认。

### [评测](../../03-mechanisms/evaluation.md)

- **延迟基准**：`examples/smart-cockpit/bench/`，含用例、runner、evaluator、结果，比较前台直连和后台委派的工具时延。
- **任务完成率**：`examples/customer-service/benchmark/`，接 tau2-bench，比较 realtime-only、realtime + 后台、纯后台三种模式。改编版 EVA Airline 50 条的结果是 44% / 62% / 68%（`EVA_AIRLINE_RESULTS.md`）。文档注明是文本输入输出，ASR、TTS 和音频不在评测范围内，也不是官方榜单结果。
- **行为测试**：`server/test/realtime-provider-behavior.test.mjs` 用本地 WebSocket 模拟各厂商原生协议，覆盖上下文投递、工具续答、取消、异步播报排队、重连。另有 announcement、backend adapter 一致性等单测。
- 缺口：没有判停准确率，也没有首音的端到端评测。

## 取舍与局限

- **委派不省再生成**：委派回合是"function call → `accepted` → 再生成一句确认"，结果回来又是"注入 user 条目 → 再生成一轮"。"回传 → 再生成"这一段出现两次。它改善的是"长任务期间对话不被占住"，不是工具回合首音；短工具走后台反而更慢，有前面 1.317 s 对 3.363 s 的实测。
- **播放回执驱动窗口是亮点**：以客户端 `playback.started` 确认送达，用"用户在说 / 回合未结束 / 音频未播完"三个条件挡住插话，再配上串行输出队列、响应代际、按 origin 取消和跨连接领取租约。这一套是这个项目最值得借鉴的部分。代价是依赖客户端回执可靠，所以要靠 1 s 自唤醒、回执超时、重试上限兜底。
- **没有统一仲裁器**：结果、进度、权限各管理器自己判断能不能出声，共用一条串行出口。优先级体现在阻塞条件的松紧上，不是显式的优先级数字。`AgentDelivery` 的 `interrupt` 模式已经预留，但仓库里没找到使用方。
- **上下文"说了什么"不精确**：不按播放位置截断；被打断的播报可能被记为已送达；历史恢复用单条 user 文本。
- **强依赖上游能力**：判停、打断粒度都由上游决定。不支持工具或对话项的前台（MiniCPM-o）无法委派，也无法注入结果。
- **结果的音色统一，但多一跳**：结果由前台模型重新生成，声音一致，但多了一次模型生成；没有"后台写话术、本地 TTS 直接出声"的低延迟通道。

## 相关

- 机制页：[判停](../../03-mechanisms/turn-detection.md)、[打断与截断](../../03-mechanisms/interruption.md)、[首音优化](../../03-mechanisms/first-audio.md)、[工具回合](../../03-mechanisms/tool-calls.md)、[会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)、[音频前处理](../../03-mechanisms/audio-preprocessing.md)、[评测](../../03-mechanisms/evaluation.md)
- 项目页：[openai-realtime-agents](openai-realtime-agents.md)、[livekit-agents](livekit-agents.md)
- 架构页：[S2S](../../02-architectures/s2s.md)、[发言权](../../02-architectures/floor-control.md)、[状态与上下文](../../02-architectures/state-and-context.md)
- 对比页：[framework-matrix](../../05-comparison/framework-matrix.md)、[decision-guide](../../05-comparison/decision-guide.md)
