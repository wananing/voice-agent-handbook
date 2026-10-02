# openai-realtime-agents

> 仓库：https://github.com/openai/openai-realtime-agents
> 许可：MIT（见仓库 LICENSE 文件）
> 分析基于：commit 94c9e91（2026-01-07）
> 状态：草稿
> 最后更新：2026-10-01

仓库内路径默认相对 `src/app/`。大部分运行时行为在 OpenAI Agents SDK 里，仓库没有附带 `node_modules`。`package-lock.json` 锁定 `@openai/agents-realtime@0.0.5`，下文标"SDK"的引用来自该版本 npm 包的 `dist/*.mjs`（`@openai/agents-core` 同为 0.0.5）。更新版本 SDK 的行为没有核实。标"推断"的是读代码推出来、没有跑过的结论。

## 定位

OpenAI 官方的 Realtime API 多 agent 示例，Next.js + TypeScript，浏览器里通过 WebRTC 直连 Realtime API。它不是框架，是一组用 Agents SDK 写的参考配置，演示两种模式（`README.md`）：

1. **Chat-Supervisor**（默认场景，`agentConfigs/index.ts:defaultAgentSetKey`）：前台实时模型负责寒暄、收集参数和"先说一句"，其余一律委派给后台文本模型（`gpt-4.1`），后台负责工具调用和组织答案。
2. **Sequential Handoff**：多个实时 agent 通过工具调用互相转交，每次转交用 `session.update` 换掉 instructions 和 tools。

和同类项目的区别：判停、打断、上下文都在 OpenAI 服务端，客户端几乎不做音频处理；委派和交接都靠 prompt 加标准 function call 实现，没有专门的调度层。它适合拿来理解"前台 S2S + 后台文本模型"的接法和代价，不适合当运行时用。

## 整体架构

```
浏览器（Next.js 页面）
  │ getUserMedia → RTCPeerConnection（Opus / PCMU / PCMA 可选）
  ▼
SDK RealtimeSession（OpenAIRealtimeWebRTC transport）
  ├─ 本地 history 镜像（由服务端事件重建）
  ├─ 工具执行：在 response.output_item.done 收到完整 function_call 就执行
  ├─ handoff：transfer_to_<agent> → session.update(instructions, tools, voice) → output + response.create
  └─ outputGuardrails：对输出转写分段分类，命中则 interrupt + 注入纠正消息
  │ data channel（JSON 事件）
  ▼
OpenAI Realtime API（gpt-4o-realtime-preview-2025-06-03；server_vad；转写 gpt-4o-mini-transcribe）

Chat-Supervisor 的后台：
  getNextResponseFromSupervisor.execute
    → fetch /api/responses（Next.js 路由，非流式代理 Responses API）
    → gpt-4.1 ⇄ 本地 mock 工具循环（parallel_tool_calls: false）
    → { nextResponse } 作为 function_call_output 回传前台
```

**核心抽象**（来自 SDK）

| 抽象 | 位置 | 作用 |
|---|---|---|
| `RealtimeAgent` | SDK `realtimeAgent.mjs:RealtimeAgent` | 一组 instructions + tools + handoffs + voice。不是独立模型实例，同一会话里的所有 agent 共用一个 Realtime 连接；voice 在首次出声后不能再改 |
| `RealtimeSession` | SDK `realtimeSession.mjs:RealtimeSession` | 维护本地 history、执行工具、处理 handoff、跑输出护栏 |
| `OpenAIRealtimeWebRTC` / `OpenAIRealtimeWebSocket` | SDK `openaiRealtimeWebRtc.mjs`、`openaiRealtimeWebsocket.mjs` | 两种传输；本仓库用 WebRTC（`hooks/useRealtimeSession.ts`） |
| `tool({ execute })` | SDK `@openai/agents-core` | 函数工具，在浏览器里执行 |
| handoff | SDK core `handoff.mjs` | 自动生成的 `transfer_to_<name>` 工具，没有参数 |

**Chat-Supervisor 的一次委派**

1. server VAD 判停（`App.tsx:updateSession`：`threshold 0.9`、`silence_duration_ms 500`、`create_response true`），服务端自动发起 response。
2. 前台 `chatAgent` 按 prompt 先说一句中性的填充语（"One moment."），然后在同一个 response 里调用 `getNextResponseFromSupervisor(relevantContextFromLastUserMessage)`（`agentConfigs/chatSupervisor/index.ts`）。prompt 用白名单划边界：只有问候、"请重复"、收集参数可以自己处理，后台工具以 YAML 形式写在 prompt 里"仅供参考，不许直接调"。
3. SDK 在 `response.output_item.done` 收到完整的 function_call 就执行工具（SDK `openaiRealtimeBase.mjs:OpenAIRealtimeBase` 的消息处理、`realtimeSession.mjs:RealtimeSession.#handleFunctionToolCall`），不等 response 结束，也不等填充语播完。执行前把本地 history 深拷贝进 `context.history`。
4. `agentConfigs/chatSupervisor/supervisorAgent.ts:getNextResponseFromSupervisor.execute` 只保留 `type === 'message'` 的历史，`JSON.stringify` 后和"最后一句的要点"一起塞进一条 user 消息，请求 `gpt-4.1`。`handleToolCalls` 循环执行后台工具（mock 数据），直到没有 function_call，拼出最终文本。`api/responses/route.ts` 以 `stream: false` 调 Responses API。
5. 返回 `{ nextResponse }`，SDK 转成字符串后 `sendFunctionCallOutput(toolCall, result, true)`：发 `conversation.item.create(function_call_output)` 和 `response.create`（SDK `openaiRealtimeBase.mjs:OpenAIRealtimeBase.sendFunctionCallOutput`）。
6. 前台实时模型再生成一轮，把后台答案念出来。prompt 要求"read verbatim"，但这是重新生成，仓库自带的示例对话里前台就改写了措辞（`chatSupervisor/index.ts` 的 Example 段）。

README 给的唯一时延数据：填充语播完到"有内容"的回答开口约 2 s。时间线（推断，未实测）：

```
用户说完 ──server_vad 静音 500 ms──▶ response #1
  ├─ 前台生成填充语音频 "One moment."          ◀ 第一次出声（与普通回合首音相当）
  ├─ 同一 response 里生成 function_call
  │    └─ SDK 立即执行：fetch /api/responses → gpt-4.1（非流式）
  │                    [→ 本地工具 → gpt-4.1 再请求一次]
  │       （与填充语的播放在时间上重叠）
  ├─ function_call_output + response.create
  └─ response #2：前台把 nextResponse 再生成成语音  ◀ 第二次出声（有内容）
```

**为什么最后一句要复述进参数**：用户转写是异步的，SDK 在 `input_audio_transcription.completed` 之后才 `conversation.item.retrieve` 回填（SDK `openaiRealtimeBase.mjs`），前台模型可能在转写回来之前就发出调用。prompt 明写"the supervisor may not have access to that message"，所以让前台把要点放进 `relevantContextFromLastUserMessage`。这是一个真实的竞态，用 prompt 打补丁。

**Sequential Handoff 的一次交接**（SDK `realtimeSession.mjs:RealtimeSession.#handleHandoff`）：

1. 模型调用 `transfer_to_<name>`；
2. `#setCurrentAgent(newAgent)`，`updateSessionConfig` 发 `session.update`，整体替换 instructions、tools；
3. `sendFunctionCallOutput(toolCall, '{"assistant":"<name>"}', true)`，新 agent 在自己的指令下开口。

**两种模式对比**

| | Chat-Supervisor | Sequential Handoff |
|---|---|---|
| 换的是什么 | 换模型：难的部分交给更强的文本模型 | 换 prompt 和工具：模型、声音不变 |
| 实现方式 | 一个普通 function tool，`execute` 里同步请求文本模型 | SDK 自动生成的 `transfer_to_<name>` 工具 |
| 谁决定 | 前台实时模型，按 prompt 白名单 | 当前 agent，按 `handoffDescription` |
| 上下文 | 后台每次收到过滤后的全量消息历史 | 共享服务端 conversation |
| 出声 | 前台填充语 + 前台再生成后台答案 | 新 agent 在新指令下开口 |
| 额外延迟 | 文本模型请求（非流式）+ 再生成 | 一次工具回合 + `session.update` |

服务端 conversation 不动，新 agent 看得到全部历史；core 的 handoff 支持 `inputFilter`，但 realtime 版没有用它。零售场景里四个 agent 两两互通（`agentConfigs/customerServiceRetail/index.ts`），所有 agent 都用 `sage` 声音。`returns` agent 的"能不能退货"另外请求 `o4-mini` 给出判断（`customerServiceRetail/returns.ts` 中 `checkEligibilityAndPossiblyInitiateReturn` 工具），是 Chat-Supervisor 思路在 handoff 场景里的变体。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 场景注册 | `agentConfigs/index.ts:allAgentSets` | `simpleHandoff`、`customerServiceRetail`、`chatSupervisor` |
| 建会话 | `hooks/useRealtimeSession.ts:connect` | WebRTC transport、模型、转写模型、outputGuardrails |
| 临时密钥 | `api/session/route.ts:GET` | 服务端代为请求 `/v1/realtime/sessions` |
| 判停配置、按键说话 | `App.tsx:updateSession`、`handleTalkButtonDown`、`handleTalkButtonUp` | PTT 时 `turn_detection: null`，松开后 commit + `response.create` |
| 前台 agent | `agentConfigs/chatSupervisor/index.ts:chatAgent` | 白名单、填充语、唯一可调工具 |
| 委派工具 | `agentConfigs/chatSupervisor/supervisorAgent.ts:getNextResponseFromSupervisor` | 组装历史，调 `gpt-4.1` |
| 后台工具循环 | `agentConfigs/chatSupervisor/supervisorAgent.ts:handleToolCalls`、`fetchResponsesMessage` | 串行，非流式 |
| Responses 代理 | `api/responses/route.ts:POST` | `stream: false` |
| 工具执行与回传 | SDK `realtimeSession.mjs:RealtimeSession.#handleFunctionToolCall`；`openaiRealtimeBase.mjs:OpenAIRealtimeBase.sendFunctionCallOutput` | 执行完 output + `response.create` |
| 工具审批钩子 | SDK `realtimeSession.mjs:RealtimeSession.#handleFunctionToolCall`（`needsApproval`） | 仓库没有用 |
| 交接 | SDK `realtimeSession.mjs:RealtimeSession.#handleHandoff` | session.update → output → response.create |
| 打断（WebRTC） | SDK `openaiRealtimeWebRtc.mjs:OpenAIRealtimeWebRTC.interrupt` | `response.cancel` + `output_audio_buffer.clear`，不发 truncate |
| 打断（WebSocket） | SDK `openaiRealtimeWebsocket.mjs:OpenAIRealtimeWebSocket.interrupt`、`_interrupt` | 自算 `audio_end_ms` 发 truncate；本仓库未用 |
| 输出护栏 | `agentConfigs/guardrails.ts:createModerationGuardrail`；SDK `realtimeSession.mjs:RealtimeSession.#runOutputGuardrails` | 每 100 字符分类一次 |
| 护栏消息识别 | `hooks/useHandleSessionHistory.ts:sketchilyDetectGuardrailMessage` | 正则匹配 `Failure Details:` |
| 编解码选择 | `lib/codecUtils.ts:applyCodecPreferences` | 模拟电话窄带 |

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：完全用服务端 `server_vad`（`threshold 0.9`、`prefix_padding_ms 300`、`silence_duration_ms 500`、`create_response true`，见 `App.tsx:updateSession`），没有语义判停；按键说话模式把 `turn_detection` 置 null，按下时 clear 输入缓冲，松开时 `input_audio_buffer.commit` + `response.create`（`App.tsx:handleTalkButtonUp`）。
- [打断与截断](../../03-mechanisms/interruption.md)：语音打断由服务端 VAD 处理；应用层在按键按下、发送文本时调用 `interrupt()`，WebRTC 传输下只发 `response.cancel` 和 `output_audio_buffer.clear`，不发 truncate（SDK `openaiRealtimeWebRtc.mjs:OpenAIRealtimeWebRTC.interrupt`），服务端是否按播放位置自动截断代码里看不到，待确认。收到 `conversation.item.truncated` 时 SDK 会 `conversation.item.retrieve` 刷新本地镜像；委派进行中被打断时 `fetch` 不会取消，结果照样回传并 `response.create`，过时答案可能被念出来（推断）。
- [首音优化](../../03-mechanisms/first-audio.md)：靠 prompt 要求前台在委派前先说一句中性填充语（`chatSupervisor/index.ts` 的 "Sample Filler Phrases"），填充语是模型生成的普通 assistant 消息，进上下文；代码没有兜底，模型跳过就是干等。后台请求非流式、用工具时至少两次串行请求，再加前台再生成一轮，README 自述填充语播完后约 2 s 才有内容。
- [工具回合](../../03-mechanisms/tool-calls.md)：工具在浏览器执行，SDK 收到完整 function_call 即执行，完成后 `function_call_output` + `response.create` 收口（SDK `realtimeSession.mjs:RealtimeSession.#handleFunctionToolCall`）；Chat-Supervisor 的委派就是一次普通 function call，没有异步工具、中间进度或 `run_llm` 一类"不再生成"的开关。SDK 提供 `needsApproval` 审批钩子，仓库没有用；退货、下单等写操作的"能不能做"交给 LLM 判断，代码不校验。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：上下文以服务端 conversation 为准，SDK 用服务端事件在本地重建 history 镜像，只用于 UI 和传给后台模型；后台每次委派全量重发过滤后的消息历史，丢掉了所有 function_call / output。没有断线重连逻辑；交接保留服务端历史，但在 UI 下拉框手动换 agent 会断开重连，历史全丢（`App.tsx:handleSelectedAgentChange`）。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：SDK 以 `getUserMedia({ audio: true })` 取麦克风，回声消除、降噪用浏览器默认（SDK `openaiRealtimeWebRtc.mjs`）；仓库唯一相关的是编解码选择：Opus 48 kHz 或 PCMU / PCMA 8 kHz，用来模拟电话线路下的 ASR / VAD（`App.tsx` 注释、`lib/codecUtils.ts:applyCodecPreferences`）。没有配置服务端 `noise_reduction`。
- [评测](../../03-mechanisms/evaluation.md)：不涉及。仓库没有测试、评测集或延迟统计，`package.json` 只有 dev / build / start / lint；调试靠 UI 里的事件日志、转写面包屑和录音下载（`hooks/useAudioDownload.ts`）。

## 取舍与局限

**Chat-Supervisor 解决的是"智能"，不是"延迟"**

- 注入路径是 `function_call_output → response.create → 前台再生成`，保留了"回传后再生成"这一跳，还在前面串了一次（用工具时两次）非流式文本模型请求。
- 前台再生成会改写后台答案，数字和引用可能走样；后台输出要求的 `# Message` 前缀和 `[NAME](ID)` 引用靠前台模型自己在口语里去掉，代码不过滤。
- 两边记忆不一致：后台看不到历史工具结果（只保留 message），前台只看到一个 output 字符串。
- 可借鉴的部分：单一委派工具 + prompt 白名单收窄前台的工具选择；委派参数里显式带"最后一句的要点"对冲转写竞态；后台 prompt 可以复用文本 agent 的写法，语音约束（简短、不列清单）放在输出层。

**Handoff 的代价**

- 每次交接都是一次工具回合，加一次全量 `session.update`。全量更新 instructions 对服务端缓存的影响，仓库没有说明，待确认。
- 所谓"专家 agent"只是换 prompt 和工具，模型和声音不变。
- 迁移到 SDK 后示例有漂移：`returns.ts` 的 prompt 还在引用旧版的 `conversation_context`、`rationale_for_transfer` 参数，`authentication.ts` 的状态机还在让模型调 `transferAgents`，而 SDK 生成的 handoff 工具叫 `transfer_to_<name>` 且没有参数。README 里的 `injectTransferTools` 等说明也过时。说明示例缺回归测试。

**护栏是事后纠偏**

- 输出护栏判的是输出音频的转写，每累积 100 字符跑一次 `gpt-4o-mini` 分类，响应结束再跑一次（SDK `guardrail.mjs` 的 `debounceTextLength` 默认 100）。攒够字符加一次模型往返，这段话大部分已经播出去了。
- 命中后 `interrupt()`，再用 `sendMessage` 注入一条 role=user 的纠正消息并 `response.create`，这条消息永久留在会话里；UI 用正则把它识别成面包屑，代码注释自称 "sketchily"。
- 分类器报错时放行（`agentConfigs/guardrails.ts`）。护栏只管"说出去的话"，不管"做出去的事"。

**其他**

- 代码里的模型是 `gpt-4o-realtime-preview-2025-06-03`，README 的时序图写的是 `gpt-4o-realtime-mini`，不一致。
- 长等待的指令做不到：`returns` agent 的 prompt 要求"不要沉默超过 10 秒"，但同步 function call 下实时模型在工具返回前不会再生成，代码也没有定时补话（推断）。
- 仓库 README 指向一个不使用 Agents SDK 的分支 `without-agents-sdk`，本页没有分析。

## 相关

- 机制页：[判停](../../03-mechanisms/turn-detection.md)、[打断与截断](../../03-mechanisms/interruption.md)、[首音优化](../../03-mechanisms/first-audio.md)、[工具回合](../../03-mechanisms/tool-calls.md)、[会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)、[音频前处理](../../03-mechanisms/audio-preprocessing.md)、[评测](../../03-mechanisms/evaluation.md)
- 项目页：[pipecat](pipecat.md)（`OpenAILiveLLMService` 的前台委派是另一种接法）、[pipecat-flows](pipecat-flows.md)、[qwen-audio-agent](qwen-audio-agent.md)、[livekit-agents](livekit-agents.md)
- 对比页：[framework-matrix](../../05-comparison/framework-matrix.md)、[decision-guide](../../05-comparison/decision-guide.md)
