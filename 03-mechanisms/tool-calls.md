# 工具回合：委派、异步工具、执行期出声

> 状态：草稿
> 最后更新：2026-10-02

## 问题是什么

工具回合是"模型决定调用工具 → 执行 → 结果回到对话"的那一轮。它比普通回合慢，慢的地方通常**不在工具执行本身**，而在结果回传之后还要让模型再生成一轮：

```
用户说完 ─ 识别 ─ 模型给出 function call ─ 执行工具 ─ 回传结果 ─ 模型再生成 ─ 首个有内容的音频
                  └── 普通回合到这里已经在出声 ──┘            └──────── 多出来的一圈 ────────┘
```

一份实践笔记的 S2S 实测（从松键起算）：识别完成约 0.37 s，模型给出 function call 约 0.8 s，状态类工具执行 +1–50 ms，回传后模型再生成并合成出首个有内容的音频约 2.1 s。最后一段"再生成一轮"约 1.2 s，是大头。同一项目普通回合首音 0.87–0.96 s。所以 **S2S 普通回合最快、工具回合最慢**，见 [latency-budget](../01-foundations/latency-budget.md) 2.3 节和 [s2s](../02-architectures/s2s.md) 第 3 节。

级联和半级联的工具回合结构上更快：工具调用以文本出现、先于任何声音；需要再组织语言时只付文本 LLM 的首 token，不付"再生成一段音频"；结果也可以不经模型直接念（[cascade](../02-architectures/cascade.md) 4.2 节）。

三条链路上，工具回合的额外代价落在不同地方：

| 链路 | 工具调用何时可见 | 结果怎么变成声音 | 额外代价 |
|---|---|---|---|
| [级联](../02-architectures/cascade.md) | 文本 LLM 流里，先于任何声音 | 本地直念，或文本 LLM 续写后走 TTS | 至多一次文本首 token + TTS 首包；一份实践笔记实测有内容 1.4 s（正文投机） |
| [半级联](../02-architectures/half-cascade.md) | 理解端的文本输出里 | 同级联；理解端是否自动续答看实现（Step-Audio2 不会） | 同级联；无实测 |
| [S2S](../02-architectures/s2s.md) | 上游事件流里，可能已经先说了一句 | 回传后上游再生成一段音频；或我们本地合成"自己的话" | 再生成约 1.2 s；有内容 2.1–2.3 s |

除了慢，工具回合还有几个正确性问题，不处理会出故障：

- **调用悬着**：一份实践笔记试过"只选工具、直接念结果、不回传"，首音降到 1.0–1.1 s，但悬着的调用让模型在后续几轮反复调同一个工具（闲聊时约 21%，打断后更高）。**每个调用都要收口**。
- **执行期间沉默**：秒级工具执行时没人出声，用户以为设备坏了。
- **和打断交织**：工具跑到一半用户插话，结果还回不回传、写操作撤不撤、过时答案会不会被念出来。

## 解法分类

### 1. 执行期间出声

让"有声音"不等工具。改善的只是"有声音"，见 [first-audio](first-audio.md) 第 5 节。

- **工具里主动说**：LiveKit `session.say(text, audio=..., add_to_chat_ctx=...)`，可传预合成音频帧。一轮带工具时，发言播完就把调度器让出来，所以工具执行期间 `say()` 能正常出声。Pipecat 常见写法是在 `on_function_calls_started` 里推 `TTSSpeakFrame`（`append_to_context` 默认 True）。
- **空闲触发的填充语**：LiveKit `RunContext.with_filler(source, delay, interval, max_steps)`，会话连续空闲满 `delay` 秒才播，短工具不触发。
- **模型在同一 response 里先说一句再发调用**：openai-realtime-agents 靠 prompt；小智服务端"LLM 在 tool_call 之前流出的文本会先播"；Step-Audio2 首轮带 `<tts_start>` 时模型可能先说一句再发调用（待确认）。
- **本地预合成提示语**：一份实践笔记在 function call 到达时本地播预合成句，不进模型、不进上下文，"有声音"提前到 0.87 s。
- **只给 UI 信号**：qwen-audio-agent 发 `voice.state=processing`；LiveKit 有 `tool_execution_updated` 事件给前端。

### 2. 跳过再生成（收口但不让模型再说）

把"回传结果"（为了收口）和"让模型再说一遍"（为了出声）拆开决定。结果能模板化时，由我们念，模型只收到结果、不再生成。

- **LiveKit**：`FunctionCallOutput.reply_required` 决定是否再生成，工具返回非 None 默认 True。关掉的三种办法：返回 `ToolResult(output, reply_required=False)`、`raise StopResponse()`、在 `function_tools_executed` 事件里 `cancel_tool_reply()`。结果总会写进上下文、总会回传，调用一定收口。"工具里 `say()` 念模板化结果 + `reply_required=False`"就是"跳过再生成、直接念"。
- **Pipecat**：`FunctionCallResultProperties.run_llm=False`；结果为空也不跑。
- **pipecat-flows**：边函数一律 `run_llm=False`，切到新节点后再由 `LLMRunFrame` 推理。注意这等于把"再生成"挪到了新节点，首音仍多一轮。
- **小智服务端**：工具返回值声明后续动作：`RESPONSE` 直接念不再过 LLM、`REQLLM` 写回历史并递归 `chat(depth+1)`、`RECORD` 只记历史、`NONE` 什么都不做。
- **qwen-audio-agent**：同一 response 里模型已经说过话、且工具不要求结果摘要（`needsToolResultSummary`），就不再 `response.create`。
- **S2S 上游的约束**：会在收到结果后自动续答的上游，要靠静默选项才能"收口不再生成"。LiveKit 把 `reply_required=False` 翻译成 Gemini 的 `FunctionResponseScheduling.SILENT`。qwen-audio-agent 对 Doubao、Google 声明 `automaticToolResponses`，运行时不再发 `response.create`。"GPT-Live"在两个项目里指的是两个不同的上游，结论不冲突：LiveKit 的 `GPTLiveModel` 连 `gpt-live-1`（`/live/sessions` 委派协议），上游不会因收到结果就自己续答，而是插件在所有调用都有结果后自己发 `response.create`；上游没有不续答就关闭调用的办法，没收口的调用会卡住后续工具调用，续答内容由语音模型说出，所以做不到静默收口，`reply_required=False` 只打 warning（`gpt_live_model.py:_append_items` / `_maybe_continue_response`，"GPT Live will answer it anyway"）。qwen-audio-agent 的 GPT-Live provider 实际连 OpenAI Realtime GA（`gpt-realtime-2.1`），写入 `function_call_output` 后不会自动续答，要客户端发 `response.create`，不发就是静默收口（`voice/realtime-provider.mjs:RealtimeFrontend.sendFunctionOutput`）。

### 3. 异步工具（先收口，结果晚到再补）

秒级以上的工具，先让调用收口、对话继续，结果晚到时再补进上下文并择机播报。

- **LiveKit**：工具第一次 `await ctx.update(msg)` 时，msg 立即作为这个 call_id 的正式 output 返回，原调用当场收口；工具继续在后台跑，后续 update 和最终结果合成新的 call_id（`{id}_update_{n}`、`{id}_final`）。`_ToolExecutor._deliver_reply` 等会话空闲（没在播、用户没说、判停不在进行）后合并，调一次 `generate_reply(tool_choice="none")`，并区分"结果仍在上下文末尾"和"后面已有新内容"两套指令去重。级联下还给仍在执行的工具注入占位结果，防止模型重复调用（`voice/generation.py:_inject_running_tool_calls`）。
- **Pipecat**：`register_function(..., cancel_on_interruption=False)`（1.0.0 起即异步工具）。不被打断取消；结果回来时对话已继续，就以 developer 消息追加 final 结果（`processors/aggregators/async_tool_messages.py`）；支持 `is_final=False` 的中间进度（S2S 服务丢弃并报错）；系统指令自动拼上 `ASYNC_TOOL_INSTRUCTIONS`。
- **pipecat-flows**：`FlowsFunctionSchema` 的 `cancel_on_interruption` 默认 False，等于默认异步；锁定 pipecat 1.4.x 上的确切行为待确认。
- **qwen-audio-agent**：`spawn_thinking` 立即回 `{status:'accepted', ...}` 收口，结果由后台完成后经播报管线回到对话（见下一节）。

### 4. 委派（前台对话、后台做事）

前台实时模型只负责对话和决定"要不要委派"，工具和组织答案交给后台文本模型或 agent。

- **openai-realtime-agents Chat-Supervisor**：前台 `chatAgent` 按 prompt 先说一句填充语，再在同一 response 里调 `getNextResponseFromSupervisor(relevantContextFromLastUserMessage)`。SDK 在 `response.output_item.done` 收到完整 function_call 就执行，不等 response 结束、也不等填充语播完。后台 `gpt-4.1` 非流式、`parallel_tool_calls: false` 地循环本地工具，返回 `{ nextResponse }`；SDK 发 `function_call_output` + `response.create`，前台再生成一轮念出来（prompt 要求逐字念，实际会改写措辞）。前台把"最后一句要点"复述进参数，是为了绕开用户转写晚到的竞态。README 自述填充语播完后约 2 s 才有内容。
- **qwen-audio-agent 后台 agent + 安全窗口注入**：前台调 `spawn_thinking(objective)`，`AgentTaskRuntime` 提交任务并立即回 `accepted`；后台按 owner FIFO 经 ACP / A2A 执行，前台的打断、休眠、断连都不取消已受理的任务。完成通知由 `SessionTaskCoordinator` 带租约领取，`AnnouncementManager` 合批，等 `AnnouncementWindow` 放行（用户没在说、当前回合已结束、没有已生成未播完的音频；被挡住每 1 s 重试），再经 `RealtimeFrontend.injectDelivery` 插入一条 role=user 文本（"以下是你先前异步执行工作的最终更新，不是用户的新请求……"）+ `response.create(tool_choice:'none')`。送达以客户端 `playback.started` 为准。
- **框架内置的委派**：LiveKit `GPTLiveModel(delegation="responses" | "client")`；Pipecat `OpenAILiveLLMService` 的 `ResponsesDelegation` / `ClientDelegation`，后台结果按 `prefers_spoken` 分两个通道：commentary（前台用自己的话说出来）和 thinking（只进前台私有上下文、不出声），委派失败时一定回一句"The delegated work could not be completed."（`services/openai/live/llm.py`）。

两种委派的时间线对比（推断，未实测）：

```
Chat-Supervisor（同步委派）
  response #1：填充语 "One moment." ─┬─ function_call ─ 后台 gpt-4.1（非流式，可能两次串行）─ output + response.create
                                     │                                                          └ response #2：前台再生成并念出 ◀ 有内容
  有声音 ≈ 普通回合首音               └─ 后台请求与填充语播放在时间上重叠

qwen-audio-agent（异步委派）
  response #1：function_call spawn_thinking ─ 立即 accepted ─ 前台再生成一句"自然确认" ◀ 有声音
  ……后台执行（秒级到分钟级），对话可继续……
  完成 ─ 等插话窗口 ─ 注入 user 条目 + response.create(tool_choice:none) ─ 前台再生成并念出 ◀ 有内容
```

委派的代价要看清：**它不省"回传 → 再生成"，反而可能出现两次**。qwen-audio-agent 的智能座舱 benchmark 测"语音结束 → 工具开始执行"，92 个需要工具的短指令回合，前台直连均值 1.317 s、后台委派 3.363 s；路线图因此规定延迟预算 < 2 s 的单步工具留在前台。委派改善的是"长任务期间对话不被占住"和"难题交给强模型"，不是工具回合首音。

### 5. 结果怎么回到对话

结果回到模型的方式各项目不同，决定了模型之后"知道什么"：

| 方式 | 项目 | 特点 |
|---|---|---|
| 标准 `function_call_output` | 多数项目；openai-realtime-agents、LiveKit、Pipecat | 调用和结果成对，最不容易让模型困惑 |
| 新 call id 的调用 / 结果对 | LiveKit 异步工具（`{id}_update_{n}`、`{id}_final`） | 原调用已收口，后续结果伪装成新调用 |
| developer 消息 | Pipecat 异步工具结果晚到时 | 不占工具调用位 |
| role=user 文本 + 声明"不是新请求" | qwen-audio-agent `injectDelivery` | 跨厂商可用；靠文字说明避免被当成用户输入 |
| commentary / thinking 双通道 | Pipecat、LiveKit 的 GPT-Live 委派 | 区分"要说出来的"和"只当背景的" |
| 带外 response，不进上下文 | qwen-audio-agent `buildSpeakResponse`（`conversation:'none'`） | 模型之后不知道自己说过 |
| `role: "input"` + `tool_call_id` | Step-Audio2 | 私有 vLLM 分支的格式，标准 `role: tool` 是否被接受待确认 |

回传的内容也是设计点。一份实践笔记的设计：output 是收口条目，不是给模型再念一遍的稿子；写一句摘要加**实际播出的原文**（按播放位置截断），不含 ID、URL；加约束说明"已经告诉用户了，不要重复"或"任务仍在进行，不要编造结果、不要再次调用"。LiveKit 异步工具的两套指令（结果仍在末尾 → 自然总结；后面已有新内容 → 说过就输出空）是同一类做法。

### 6. 回传给 S2S 上游的时序约束

S2S 上游大多同一时刻只能有一个在生成的 response，回传结果并请求再生成必须避开前导语音：

- **LiveKit**：回传工具结果前先等队列里所有发言播完，注释给的原因是 "most realtime models don't support generating multiple responses at the same time"（`_realtime_generation_task_impl`）。
- **Pipecat**：bot 正在说话时置 `_push_context_on_bot_stopped_speaking`，等 `BotStoppedSpeakingFrame` 再推 context（S2S 服务据此 diff 出新完成的工具结果发给上游）；用户正在说话时也不推。
- **qwen-audio-agent**：输出走串行队列，`injectDelivery` 等 `whenIdle()`；单槽位上游（DashScope）取消超时就断开重连。
- **openai-realtime-agents**：不等，工具执行完就 `function_call_output` + `response.create`；靠后台请求本身耗时（秒级）让前一个 response 自然结束。如果工具很快、前导填充语还在生成，会不会和上游冲突，代码里看不到处理，待确认。

代价：前导语音的时长整段串进有内容延迟。严格来说上游的限制是"同时只能生成一个 response"，不是"必须播完"；LiveKit、Pipecat 按播放结束来等，比上游约束更保守（推断，未实测两者差值）。一份实践笔记的设计是**结果立即回传、立即就绪，只在播放时排在提示语后面**，并要求提示语短（≤ 300 ms）。

### 7. 多工具并行与递归上限

- **并行**：Pipecat 每个调用一个 asyncio task，同一批并行调用（`group_parallel_tools=True`）只在最后一个完成时再推理一次；小智并行提交、逐个等待、单个超时 30 s（超时统一回"网络遇到点问题"）；pipecat-flows 等本轮并行调用全部结束才切节点（`_check_and_execute_transition`）。TEN 的 `LLMExec` 整个过程串行。openai-realtime-agents 后台和 Step-Audio2 示例都设 `parallel_tool_calls: False`（Step-Audio2 流式时只处理第一个调用，并行待确认）。
- **递归上限**（"工具 → 再生成 → 又调工具"的连续步数）：LiveKit `max_tool_steps` 默认 3，只在级联路径生效，实时路径有无等价限制待确认；小智最多 5 层，到顶后注入"请直接回答"并禁用工具；Pipecat 源码中未找到显式上限（按 `max_tool`、`max_function_call` 搜索无结果），待确认。
- **去重**：LiveKit `on_duplicate` 策略；qwen-audio-agent 同一用户回合只认第一个发起 spawn 的 response，其余回 `duplicate`。

### 8. 工具调用与打断

| 情况 | 做法 |
|---|---|
| 工具在跑时用户插话 | Pipecat：同步工具（默认）取消 task、context 写 `CANCELLED`、不推理；异步工具不取消。LiveKit：可在工具里 `RunContext.disallow_interruptions()`，但实时模型开着服务端判停时不成立 |
| 旧回合的调用晚到 | qwen-audio-agent `ToolCallHandler.closeStaleCall` 回 `{status:'superseded'}` 收口、不触发生成；Pipecat 每个调用一个 `settled` 位，已收口的再回传被拒绝 |
| 委派进行中被打断 | openai-realtime-agents 的 `fetch` 不取消，结果照样回传并 `response.create`，过时答案可能被念出来（推断）；qwen-audio-agent 不取消已受理任务，只有 `cancel_agent_task` 能取消 |
| 结果已回传、正在念时被打断 | 一份实践笔记补一个极短的 `{"interrupted": true}` 结果：补了 20/20 出声，不补 15/20；40 次打断卡死 0、重复调用 0 |
| 结果还没回传就被打断 | 同一份笔记：不再回传，本地置终态 |
| 结果播报被打断 | qwen-audio-agent 视为已送达、不重播 |
| 工具执行期间的用户输入 | Pipecat `FunctionCallUserMuteStrategy` 可在工具执行期间丢弃用户输入；`UserIdleController` 在工具进行中不计空闲 |
| 写操作 | 一份实践笔记：执行前再检查一次是否已被打断；执行完才被打断的，下一次空闲补一句告知。openai-realtime-agents 的退货、下单交给 LLM 判断，SDK 的 `needsApproval` 钩子没用上 |

把这些情况归纳起来，一份实践笔记的设计规范给每个调用定义了六个终态：`result`（正常完成，含业务失败）、`interrupted`（完成后被打断）、`cancelled`（执行中被取消或超时）、`progress`（长工具首条进度即收口）、`superseded`（会话重建或被新调用替换）、`duplicate`（重复调用）。不变式是：每个收到的调用在超时加余量内必然进入终态；终态之后的任何结果、进度一律拒绝。只有"回传再生成"路线、需要向用户确认的重复调用、长工具的承接句会触发再生成，每个调用最多一轮。

打断本身的触发、截断见 [interruption](interruption.md)；播报和提示语的仲裁、代际见 [floor-control](../02-architectures/floor-control.md)。

## 各解法的代价

| 手段 | 改善什么 | 量级 | 复杂度 | 对上游的依赖 | 失败模式 |
|---|---|---|---|---|---|
| 执行期出声（预合成 / `say`） | 有声音 | 一份实践笔记：2.1 s → 0.87 s | 低到中 | 能在本地插入音频；`supports_say` 的上游会把 `say()` 交给模型念 | 长提示语推迟正文；进了上下文被模型模仿；音色不一致 |
| prompt 让模型先说 | 有声音 | 与普通回合首音相当 | 低 | 模型听话 | 模型跳过就干等；同一 response 里调用要等再生成 |
| 跳过再生成、本地直念 | 有内容 | 省掉约 1.2 s 的再生成（S2S 实测量级）；一份实践笔记的估算是有内容约 1.1 s | 中：模板、本地 TTS、仲裁 | 级联无依赖；S2S 需要静默收口选项 | 上游没有静默选项时照样续答（LiveKit 的 gpt-live-1 委派）；模板话术机械；回传内容与实际播出不一致时模型指代出错 |
| 异步工具 | 对话不被长工具占住 | 工具耗时移出关键路径 | 中高：去重、占位、插话窗口 | 上游要能接受中途注入；S2S 上中途插入条目常不被模型使用（见 [s2s](../02-architectures/s2s.md) 4.2 节） | 结果插进别人的话里；重复播报；模型重复调用 |
| 委派后台 | 难题与长任务 | 短工具反而更慢：1.317 s → 3.363 s（qwen-audio-agent 自测） | 高：两份上下文、取消、转写竞态 | 前台要支持工具与对话项注入（MiniCPM-o 不行） | 过时答案被念出；前台改写后台答案；后台拿不到最后一句 |
| 等前导语音播完再回传 | 避免上游冲突 | 前导语音时长串进延迟 | 低 | 单 response 槽位的上游 | 提示语越长，有内容越晚 |
| 递归上限 | 防止循环 | — | 低 | 无 | 到顶时答非所问 |

## 各项目怎么做

| 项目 | 做法 | 代码路径 | 备注 |
|---|---|---|---|
| [livekit-agents](../04-projects/frameworks/livekit-agents.md) | 工具与前导语并行；`say()` / `with_filler` 执行期出声；`reply_required` 三种关法；`ctx.update()` 异步工具；S2S 回传前等发言播完；`max_tool_steps=3`；GPT-Live 委派 | `voice/agent_session.py:AgentSession.say`、`llm/tool_context.py:ToolResult`、`voice/events.py:RunContext.update`、`voice/generation.py:make_tool_output` | gpt-live-1 委派做不到静默收口（OpenAI Realtime GA 可以）；实时路径的步数上限待确认 |
| [pipecat](../04-projects/frameworks/pipecat.md) | 每调用一个 task；`TTSSpeakFrame` 出声；`run_llm=False`；并行结果合并一次推理、bot 说话时延后；`cancel_on_interruption=False` 异步工具；`ResponsesDelegation` / `ClientDelegation` | `services/llm_service.py:LLMService.run_function_calls`、`llm_response_universal.py:LLMAssistantAggregator._handle_function_call_result`、`services/openai/live/llm.py` | 递归上限未找到，待确认 |
| [pipecat-flows](../04-projects/frameworks/pipecat-flows.md) | 节点函数 `run_llm=True`、边函数 `run_llm=False` + 等并行调用全部结束再切节点；异常回 `{"status":"error"}` 收口 | `manager.py:FlowManager._create_transition_func`、`_check_and_execute_transition` | `cancel_on_interruption` 默认 False |
| [openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md) | Chat-Supervisor：prompt 填充语 + 委派工具同步请求 `gpt-4.1`；完整 function_call 一到就执行，完成即 output + `response.create` | `agentConfigs/chatSupervisor/supervisorAgent.ts:getNextResponseFromSupervisor`；SDK `realtimeSession.mjs:RealtimeSession.#handleFunctionToolCall` | 无异步工具、无不再生成开关；委派被打断不取消 |
| [qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md) | `spawn_thinking` 立即 `accepted` 收口；后台 ACP / A2A；`AnnouncementWindow` 放行后注入 user 条目 + `response.create(tool_choice:'none')`；过期调用 `superseded` | `frontend/tools/agent-task-runtime.mjs`、`frontend/tools/tool-call-handler.mjs:ToolCallHandler.flushDeferredToolResponse`、`voice/announcement/announcement-window.mjs` | 委派不省再生成；有前台直连 vs 委派的时延基准 |
| [ten-framework](../04-projects/frameworks/ten-framework.md) | 工具即扩展，`tool_register` 注册；`tool_call` cmd 串行等结果，写进上下文再调 LLM；S2S 版经 `v2v` 转发 | `LLMExec._handle_llm_response`；`send_client_function_call_output` | 无填充语、无异步工具；递归上限待确认 |
| [xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md) | `direct_answer` 虚拟工具；并行执行 + 30 s 超时；`RESPONSE / REQLLM / RECORD / NONE` 结果分类；最多 5 层递归 | `core/connection.py:ConnectionHandler.chat`、`plugins_func/register.py`、`core/providers/tools/device_mcp/mcp_handler.py` | 执行期无填充语，只有调用前流出的文本先播 |
| [xiaozhi-esp32](../04-projects/devices/xiaozhi-esp32.md) | 设备是 MCP server，经 `mcp` 消息承载 JSON-RPC 2.0；编排在服务端 | `main/mcp_server.cc:McpServer` | — |
| [step-audio2](../04-projects/e2e-models/step-audio2.md) | 原生工具解析器 `step_audio_2`；结果以 `role: "input"` 回注；续答由客户端再发请求 | `examples-vllm.py:tool_call_test`、`examples-vllm-stream.py` | 不自动续答，天然可"跳过再生成"；并行待确认 |
| [qwen3-omni](../04-projects/e2e-models/qwen3-omni.md) | 系统提示手写 `<tools>` / `<tool_call>` 模板，从文本输出解析 | `cookbooks/audio_function_call.ipynb` | 结果回注、续答、带工具时 Talker 行为均待确认 |
| [ultravox](../04-projects/e2e-models/ultravox.md) | 本仓库不涉及；vLLM 打开底座的工具解析器理论上可用 | `infer.py` | 工具保真度无评测，待确认 |
| [unmute](../04-projects/full-duplex/unmute.md) | 不涉及；README 建议把工具藏在 LLM 服务端；"说再见挂断"用字符串匹配 | `check_for_bot_goodbye` | — |
| [moshi](../04-projects/full-duplex/moshi.md) | 不涉及：协议无工具事件，模型无工具训练 | — | — |

ASR、TTS、VAD、音频前处理类项目（[funasr](../04-projects/asr/funasr.md)、[cosyvoice](../04-projects/tts/cosyvoice.md)、[smart-turn](../04-projects/turn-vad/smart-turn.md) 等 15 个）不涉及。

## 我们的判断

**原则**：每个调用都收口；收口和再生成分开决定；"有声音"和"有内容"分开优化。

按场景选：

| 场景 | 推荐做法 | 理由 |
|---|---|---|
| 结果可模板化、毫秒级（状态、位置、进度） | 本地直念 + 回传结果收口 + 不再生成（LiveKit `say()` + `ToolResult(reply_required=False)`；Pipecat `TTSSpeakFrame` + `run_llm=False`；小智 `RESPONSE`） | 省掉整圈再生成，是工具回合提速的主要来源 |
| 结果要组织语言，级联 / 半级联 | 回传再生成；工具超过约 300 ms 时先播预合成短提示语 | 只付文本首 token，代价可接受 |
| 结果要组织语言，S2S，延迟敏感 | 有自己的 TTS 时：委派后台文本模型流式出正文、本地 TTS 念，output 回传实际播出的文本、不再生成；没有自己的 TTS 时：接受再生成，用提示语顶住"有声音" | 前者是唯一能同时省掉再生成和保住组织能力的路；后者是上游限制下的兜底 |
| 秒级以上、或写操作要调后端 | 异步：立即回一个"已提交、正在处理"的结果收口，先念承接句，最终结果等空闲窗口作播报（照 LiveKit `ctx.update()` / qwen-audio-agent 的插话窗口） | 长工具不占住对话；窗口以播放回执为准 |
| 长任务、多步推理、需要强模型 | 委派后台（qwen-audio-agent 的方式），结果走安全窗口注入 | 只用于长任务；< 2 s 的单步工具留在前台，后台反而更慢 |
| 写操作 | 门槛和规则留在委派之前，不交给后台模型执行；执行前再查是否已被打断；同一回合只认第一个写调用 | 副作用撤不回 |

**S2S 上游先探能力再定路线**：上游收到结果后会不会自动续答、有没有静默选项（Gemini `SILENT` 有，OpenAI Realtime GA 不发 `response.create` 即可，LiveKit 的 gpt-live-1 委派没有），决定"跳过再生成"能不能成立。没有静默选项就退到"回传再生成 + 提示语"，或者改走[半级联](../02-architectures/half-cascade.md)。工具密集的产品，这一条可能决定链路选型。

**回传时机**：结果立即就绪，只在播放时排在提示语后面；提示语短（≤ 300 ms），工具在提示语开播前已完成就不播。框架默认"等前导语音播完再回传"时，要把提示语时长计入有内容延迟。

**打断**：只读工具直接取消；已向后端发出的写操作不撤回，完成后下一次空闲补一句；已回传正在念时被打断，补一个极短的"被打断"结果；过期调用一律收口为 superseded，不触发生成。

**递归上限**：设 3–5 步，到顶后禁用工具、要求直接回答（照小智）。

**还没有数据支撑的**：跳过再生成在 S2S 上的有内容首音（一份实践笔记估算约 1.1 s，未实测）；委派后台流式加投机起跑的收益；回传内容写"完整结果"还是"实际播出原文"哪种指代更准；"等播完"和"等生成完"再回传的延迟差。

## 相关

- 架构层：[S2S](../02-architectures/s2s.md)（第 3、5 节：工具回合最慢、把智力搬出 S2S）、[级联](../02-architectures/cascade.md)、[半级联](../02-architectures/half-cascade.md)、[话筒归属](../02-architectures/floor-control.md)（提示语、播报、插话窗口）、[状态与上下文](../02-architectures/state-and-context.md)
- 基础：[latency-budget](../01-foundations/latency-budget.md)、[tts](../01-foundations/tts.md)
- 其他机制：[首音优化](first-audio.md)、[打断与截断](interruption.md)、[会话恢复与上下文同步](session-recovery.md)、[评测](evaluation.md)
- 项目页：[livekit-agents](../04-projects/frameworks/livekit-agents.md)、[pipecat](../04-projects/frameworks/pipecat.md)、[pipecat-flows](../04-projects/frameworks/pipecat-flows.md)、[openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md)、[qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md)、[xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md)
