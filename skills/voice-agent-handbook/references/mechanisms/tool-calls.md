# 工具回合（tool calls）

手册：03-mechanisms/tool-calls.md

## 问题

工具回合慢的地方通常不在工具执行，而在结果回传后模型还要再生成一轮。实践笔记 S2S 实测（从松键起算）：识别完成约 0.37 s，function call 约 0.8 s，状态类工具执行 +1–50 ms，回传后再生成到首个有内容音频约 2.1 s，再生成这段约 1.2 s 是大头；同项目普通回合 0.87–0.96 s。S2S 普通回合最快、工具回合最慢。级联 / 半级联工具调用以文本出现、先于声音，再组织语言只付文本首 token，结果还能不经模型直念。

正确性问题：
- 调用悬着：实践笔记试过"只选工具、直接念、不回传"，首音降到 1.0–1.1 s，但模型后续反复调同一工具（闲聊约 21%，打断后更高）。每个调用都要收口。
- 执行期间沉默：用户以为设备坏了。
- 和打断交织：结果回不回传、写操作撤不撤、过时答案会不会被念。

## 解法分类

**1. 执行期间出声（只改善有声音）。**
- 工具里主动说：LiveKit `session.say(text, audio=..., add_to_chat_ctx=...)`；Pipecat 在 `on_function_calls_started` 推 `TTSSpeakFrame`。
- 空闲触发：LiveKit `RunContext.with_filler`，空闲满 `delay` 才播，短工具不触发。
- 模型同一 response 先说一句再调用：openai-realtime-agents 靠 prompt，无兜底。
- 本地预合成提示语：不进模型、不进上下文，实践笔记实测有声音 2.1 s → 0.87 s。
- 只给 UI：qwen-audio-agent `voice.state=processing`。
- 代价：长提示语推迟正文；进上下文被模仿；音色不一致。

**2. 跳过再生成（收口但不让模型再说，改善有内容）。** 把"回传结果"（为收口）和"让模型再说"（为出声）分开决定。
- LiveKit：`ToolResult(output, reply_required=False)`、`raise StopResponse()` 或 `cancel_tool_reply()`；结果总会回传，调用一定收口。
- Pipecat：`FunctionCallResultProperties.run_llm=False`。
- 小智服务端：工具返回 `RESPONSE`（直接念）/ `REQLLM`（递归 `chat(depth+1)`）/ `RECORD` / `NONE`。
- S2S 上游约束：会自动续答的上游要靠静默选项。Gemini `FunctionResponseScheduling.SILENT` 可以；OpenAI Realtime GA 写 `function_call_output` 后不发 `response.create` 即静默收口；LiveKit 的 gpt-live-1 委派做不到，`reply_required=False` 只打 warning。
- 收益：省掉约 1.2 s 再生成（实践笔记估算有内容约 1.1 s，需实测）。代价：模板话术机械；回传内容与实际播出不一致时模型指代出错。

**3. 异步工具（先收口，结果晚到再补）。**
- LiveKit：首次 `await ctx.update(msg)` 即作为原 call_id 的 output 收口，后续用新 call_id（`{id}_update_{n}`、`{id}_final`）；等会话空闲合并后 `generate_reply(tool_choice="none")`；级联下给仍在执行的工具注入占位结果防重复调用。
- Pipecat：`register_function(..., cancel_on_interruption=False)`，结果晚到以 developer 消息追加。
- qwen-audio-agent：`spawn_thinking` 立即回 `{status:'accepted'}`，结果经播报管线回来。
- 代价：结果插进别人的话里、重复播报；S2S 上中途插入条目常不被模型使用。

**4. 委派（前台对话、后台做事）。**
- openai-realtime-agents Chat-Supervisor：前台说填充语并同一 response 调 `getNextResponseFromSupervisor`，后台 `gpt-4.1` 非流式循环工具，回传后前台再生成念出（会改写措辞）。README 自述填充语后约 2 s 才有内容。
- qwen-audio-agent：后台 ACP / A2A 执行，打断 / 休眠 / 断连不取消已受理任务；完成后等 `AnnouncementWindow` 放行（用户没说、回合已结束、没有未播完音频，每 1 s 重试），注入 role=user 文本（声明"不是用户的新请求"）+ `response.create(tool_choice:'none')`。
- 框架内置：LiveKit `GPTLiveModel(delegation=...)`；Pipecat `ResponsesDelegation` / `ClientDelegation`，commentary（说出来）与 thinking（只进上下文）双通道。
- 代价：**不省再生成，反而可能两次**。qwen-audio-agent 座舱 benchmark（92 个短指令）语音结束 → 工具开始执行：前台直连均值 1.317 s、后台委派 3.363 s。委派改善的是长任务期间对话不被占住和难题交给强模型。

**5. 结果回到对话的方式。** 标准 `function_call_output`（最不易困惑）；新 call id 对（LiveKit 异步）；developer 消息（Pipecat 异步）；role=user 文本 + 声明（qwen 跨厂商）；commentary / thinking 双通道；带外 response 不进上下文（模型不知道自己说过）。回传内容（实践笔记设计）：一句摘要 + 实际播出的原文（按播放位置截断），不含 ID、URL，附约束"已告诉用户，不要重复"或"任务进行中，不要编造、不要再次调用"。

**6. 回传 S2S 的时序。** 上游同时只能有一个生成中的 response。LiveKit 回传前等所有发言播完；Pipecat 等 `BotStoppedSpeakingFrame` 再推 context；qwen 等 `whenIdle()`；openai-realtime-agents 不等。代价：前导语音时长整段串进有内容延迟；上游约束其实是"同时只生成一个"，按播放结束等更保守（推断）。

**7. 并行与递归上限。** Pipecat 同批并行调用最后一个完成才推理一次；小智并行提交、单个超时 30 s；TEN 串行。递归上限：LiveKit `max_tool_steps` 默认 3（仅级联）；小智 5 层，到顶注入"请直接回答"并禁用工具；Pipecat 未找到上限。去重：LiveKit `on_duplicate`；qwen 同一回合只认第一个 spawn，其余回 `duplicate`。

**8. 与打断交织。** Pipecat 同步工具被打断取消、写 `CANCELLED`；qwen 过期调用 `ToolCallHandler.closeStaleCall` 回 `superseded`；openai-realtime-agents 委派中被打断 `fetch` 不取消，过时答案可能被念（推断）。实践笔记实测：已回传正在念时被打断补 `{"interrupted": true}`，出声 20/20 对不补 15/20，40 次打断卡死 0、重复调用 0；未回传就被打断则不再回传、本地置终态。

六个终态（实践笔记规范）：`result`、`interrupted`、`cancelled`、`progress`（长工具首条进度即收口）、`superseded`、`duplicate`。不变式：每个调用在超时加余量内必进终态；终态后的任何结果一律拒绝；每个调用最多触发一轮再生成。

## 推荐

原则：每个调用都收口；收口和再生成分开决定；有声音和有内容分开优化。

| 场景 | 做法 | 理由 |
|---|---|---|
| 结果可模板化、毫秒级（状态、位置、进度） | 本地直念 + 回传收口 + 不再生成（LiveKit `say()` + `reply_required=False`；Pipecat `TTSSpeakFrame` + `run_llm=False`；小智 `RESPONSE`） | 省掉整圈再生成，是工具回合提速的主要来源 |
| 要组织语言，级联 / 半级联 | 回传再生成；工具超过约 300 ms 先播预合成短提示语 | 只付文本首 token |
| 要组织语言，S2S，延迟敏感 | 有自己的 TTS：委派后台文本模型流式出正文、本地 TTS 念，output 回传实际播出文本、不再生成；没有：接受再生成，用提示语顶有声音 | 前者是唯一同时省再生成又保住组织能力的路 |
| 秒级以上、或写操作调后端 | 异步：立即回"已提交、处理中"收口，先念承接句，最终结果等空闲窗口播报（以播放回执为准） | 长工具不占住对话 |
| 长任务、多步推理、需强模型 | 委派后台，结果走安全窗口注入 | 只用于长任务；< 2 s 单步工具留前台 |
| 写操作 | 门槛和规则留在委派之前；执行前再查是否已被打断；同一回合只认第一个写调用 | 副作用撤不回 |

- **S2S 先探上游能力再定路线**：收到结果会不会自动续答、有没有静默选项，决定"跳过再生成"能否成立。没有就退到"回传再生成 + 提示语"或改走半级联。工具密集的产品这条可能决定链路选型。
- **回传时机**：结果立即就绪，只在播放时排在提示语后；提示语 ≤ 300 ms，工具在提示语开播前已完成就不播。框架默认等前导语音播完再回传时，把提示语时长计入有内容延迟。
- **打断**：只读工具直接取消；已发出的写操作不撤回，完成后下次空闲补一句；正在念时被打断补极短"被打断"结果；过期调用收口为 superseded，不触发生成。
- **递归上限**：3–5 步，到顶禁用工具、要求直接回答。
- 无数据支撑、需实测：S2S 跳过再生成的有内容首音；回传"完整结果"还是"实际播出原文"指代更准；"等播完"与"等生成完"再回传的差值。

## 各项目怎么做

- livekit-agents：`say()` / `with_filler` 执行期出声；`reply_required` 三种关法；`ctx.update()` 异步；S2S 回传前等发言播完；`max_tool_steps=3`。`voice/agent_session.py:AgentSession.say`、`llm/tool_context.py:ToolResult`、`voice/events.py:RunContext.update`、`voice/generation.py:make_tool_output`。
- pipecat：每调用一个 task；`run_llm=False`；并行结果合并一次推理、bot 说话时延后推 context；`cancel_on_interruption=False` 异步；委派双通道。`services/llm_service.py:LLMService.run_function_calls`、`llm_response_universal.py:LLMAssistantAggregator._handle_function_call_result`、`services/openai/live/llm.py`。
- pipecat-flows：边函数 `run_llm=False`，等并行调用全部结束再切节点（再生成挪到新节点，首音仍多一轮）。`manager.py:FlowManager._create_transition_func`、`_check_and_execute_transition`。
- openai-realtime-agents：Chat-Supervisor 同步委派，完整 function_call 一到就执行。`agentConfigs/chatSupervisor/supervisorAgent.ts:getNextResponseFromSupervisor`、SDK `realtimeSession.mjs:RealtimeSession.#handleFunctionToolCall`。
- qwen-audio-agent：`spawn_thinking` 立即 accepted；窗口放行后注入；过期 `superseded`。`frontend/tools/agent-task-runtime.mjs`、`frontend/tools/tool-call-handler.mjs:ToolCallHandler.flushDeferredToolResponse`、`voice/announcement/announcement-window.mjs`；静默收口 `voice/realtime-provider.mjs:RealtimeFrontend.sendFunctionOutput`。
- xiaozhi-esp32-server：`direct_answer` 虚拟工具；并行 + 30 s 超时；结果四分类；最多 5 层。`core/connection.py:ConnectionHandler.chat`、`plugins_func/register.py`。
- ten-framework：工具即扩展，`tool_call` 串行等结果再调 LLM；无填充语、无异步。`LLMExec._handle_llm_response`。
- step-audio2：结果以 `role: "input"` 回注，不自动续答，天然可跳过再生成。`examples-vllm.py:tool_call_test`。

## 相关

- 架构：02-architectures/s2s.md（第 3、5 节；4.2 节中途注入）、02-architectures/cascade.md（4.2 节直念）、02-architectures/half-cascade.md、02-architectures/floor-control.md（提示语、播报、插话窗口）、02-architectures/state-and-context.md
- 基础：01-foundations/latency-budget.md（2.3 节）、01-foundations/tts.md
- 机制：03-mechanisms/first-audio.md、03-mechanisms/interruption.md、03-mechanisms/session-recovery.md、03-mechanisms/evaluation.md
