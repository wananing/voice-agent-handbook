# 会话恢复与上下文同步（session recovery）

手册：03-mechanisms/session-recovery.md

## 问题

两个问题：（a）S2S 上游在服务端维护自己的 conversation，本地还有一份，两者在工具结果回传、中途插入消息、被打断回复的截断、改 instructions 导致缓存失效这几处分叉。级联每轮自己拼，没有同步问题。（b）断线后能恢复什么：网关到上游断（会话没了或到时长上限）、设备到网关断（移动网络、休眠）；用什么重建、设备重连算不算同一会话、没终态的那一轮怎么收尾、长会话历史放不下怎么办。

不处理的后果：重连后模型忘了用户刚说的话；被打断的回复以全文回到上游；写操作被再执行一遍；长会话早期信息（名字、陪同人）丢失。原则"本地上下文是唯一真相、上游只是镜像"见 state-and-context。

## 解法分类

**（a）上下文同步**
- 无状态全量拼：级联、无状态端到端模型（Qwen3-Omni、Step-Audio 2、Ultravox）。只有长度问题。代价最低，超长会溢出（unmute vLLM `--max-model-len=1536`）。
- 本地镜像 + 全量 diff：LiveKit OpenAI 插件（`RemoteChatContext` + `compute_chat_ctx_diff` LCS diff，每次同步等确认，统一 5 s）。代价：复杂，要求上游能删改条目；镜像没处理 `conversation.item.truncated`，可能仍是全文（需实测）；中途插入不一定被模型使用。
- 只同步工具结果：Pipecat 所有 S2S 服务。建会话灌一次历史，之后只回传新工具结果。代价：本地摘要、截断、改写到不了上游，要生效必须重建。
- 上游为真相：openai-realtime-agents，本地 history 只用于 UI 和委派。断线即丢。
- 状态走 instructions + 建连注入一次历史：qwen-audio-agent。代价：每次 `session.update` 让缓存作废，实践笔记实测首音 +80–200 ms（粗粒度更新）或 +150–270 ms（每轮更新），未命中缓存输入 ×3.2。

关键事实：
- 工具结果回传是所有 S2S 实现里唯一在会话中途稳定同步的内容。
- 中途追加消息基本无效：实践笔记实测建会话后逐条插入历史 0/6，中途插入 system / user 状态说明"基本不看"。
- Gemini、Ultravox、AWS 改工具表要重连。

**（b）断线重建**

| 做法 | 恢复质量 | 失败模式 |
|---|---|---|
| 不恢复（xiaozhi、unmute、Moshi、FunASR WS） | 无 | 用户从头说 |
| 重放镜像（LiveKit OpenAI、TEN） | 实践笔记实测逐条插入 0/6；LiveKit 重放排除全部 function call | 被截断回复以全文回上游；工具调用丢失；灌文本可能让模型改用纯文本回复 |
| resumption handle（Gemini Live） | 快 | Pipecat handle 只存内存，进程重启就没了；同类"会话 id 接续"实践笔记实测 1/6 |
| 本地重灌（建会话参数带入） | 实践笔记实测成对问答 6/6（样本仅 6 次） | Gemini 带 tool call 历史报 1007、2.5 要求以 user 结尾；AWS 40 条上限 |
| 主动换会话（Pipecat Nova `SessionContinuationHelper`、LiveKit `max_session_duration`） | 空档交接无感 | 预建会话空闲超时（Nova 30 s）；交接期用户音频要缓冲（Pipecat 回放约 3 s） |
| 设备侧跨连接快照 | 状态和近期对话完整 | 写入成本；在途写操作需幂等键。开源无实现 |

设备侧两层窗口（实践笔记规范）：续接窗口（短，例如 30 s，上游会话还在，带 `session_id` 重连接着用）；会话恢复窗口（长，例如 30 分钟，从快照恢复状态和最近 12 轮，上游按本地重灌重建）。两层都过期才算新会话；用户说"重新开始"是唯一提前开新会话的方式，分"只清对话"和"连业务状态一起清"两档。

上游差异（决定能选哪种）：
- OpenAI Realtime：支持删改条目和 truncate，无 handle；往音频会话逐条插文本模型"会忘了自己能出音频"（Pipecat 注释）；LiveKit 默认 20 分钟主动重连。
- Gemini Live：不支持删除和 truncate，改工具表要重建；有 resumption handle；1007 = 上下文耗尽。
- AWS Nova Sonic：历史只能建会话时带入；Pipecat 360 s 主动换会话；单条 50 KB / 总量 200 KB（调研笔记）。
- GPT-Live（LiveKit 插件）：instructions、历史会话开始后不可变；128 条 / 8192 token（调研笔记）。
- DashScope 等单槽位：不截断；部分厂商注入历史被当实时输入，用 `restoreConversationContext=false` 跳过。

错误分级的代价：全当断线会无谓重建、缓存全冷；全当可恢复，鉴权 / 配额错误会无限重连；1007 原样重放必然再失败。

## 推荐

**级联、半级联、无状态端到端**：每轮从本地全量拼，不做镜像。长会话用"状态块 + 最近 K 轮原样 + 文本 LLM 摘要"，状态块放消息尾部保持前缀稳定。端到端模型不产出用户转写，要另跑 ASR 保证历史里有用户文本，否则 KV 一丢就恢复不了。

**S2S 会话中途**：选"只同步工具结果"，不选全量 diff。
- 本地上下文唯一真相，上游只写不读；镜像只用于判断哪些工具结果已回传，不用于重放。
- 中途只允许：工具结果回传、上游支持时的按回复临时指令、协议专门的追加通道。不中途插入历史或状态说明。
- 上游支持 truncate 时按设备播放回执截断；不支持时只改本地，下次重建带上"被打断"。
- instructions 只在两轮之间、只在"不更新就会选错动作"的字段变化时更新，其余懒更新到下次重建。工具表建会话时一次给全。

**S2S 重建**：全系统只有一个重建器，断线、上下文超限、主动换会话都走它。
1. 触发：断线 / 可重建错误 / 上下文超限 / 时长或轮数到点 / 状态变化。紧急的立即走，其余等空档（助手说完、无未收口调用、用户没在说）。
2. 从本地投影：人设 + 状态块 + 历史（摘要 + 最近 K 轮，assistant 只放用户实际听到的文本，被打断条目带标记）+ 工具表，走建会话参数。超限先缩历史，不原样重放。
3. 重建"已回传工具结果"登记表；处理无终态回合；回放交接期缓冲的用户音频；trace 写 `rebuild_reason`、`history_scope`、首轮缓存命中。
- 不重放镜像。resumption handle 只当优化，不替代重灌。
- 灌文本后模型可能改用纯文本回复：trace 记每轮回复模态，纯文本时交给本地 TTS，反复出现就在下个空档换会话。
- 错误四类，按回合计重建预算（例如 3 次、指数退避、稳定后清零）：

| 类别 | 例子 | 处理 |
|---|---|---|
| 重建也没用 | 鉴权、配额、计费（白名单，参考 LiveKit `_is_fatal_error`） | 停止重建，本地兜底话，告警 |
| 会话失效可重建 | 断线、服务端关会话、可重试限流 | 走重建器，补一句"信号不好"本地话术 |
| 上下文超限 | Gemini 1007 | 先缩历史再重建 |
| 非致命 | 取消竞态（`response_cancel_not_active`）、审核命中、条目级错误、空提交 | 只让这一回合失败；被拒回合不进后续历史 |

- 有时长上限（OpenAI 20 分钟、Nova 约 6 分钟）或轮数上限（实践笔记在某闭源上游观察到约 20 轮）就在空档主动换会话，把"会话满了"变成常规路径。

**设备侧断线**：自有设备或 App 做跨连接快照，每个回合终态后写一次，不含上游镜像和原始音频。30 分钟、12 轮是实践笔记的值，按产品定。浏览器演示、一次性会话可以不做。

**被打断那一轮**：
- 没有发起写操作：拿最后一句 user 作为新会话首轮输入，重新回答。
- 已执行写操作：不重放，本地通道告诉用户并写进状态块；状态未知先用幂等键查后端。
- 已播一部分的回复：带打断标记进历史，不自动重说。
- 断线时用户正在说：缓冲音频在新会话就绪后回放（可快于实时），不转文本；交接期只允许本地提示音和话术。
- 旧会话在跑的工具调用：本地收口，结果走本地通道，不回传新会话。

**长会话**：硬信息（名字、陪同人、已选路线）放状态块，不依赖历史或摘要。摘要用自己的文本 LLM 提前异步生成，每次从原始条目重新生成，不对摘要再摘要，不切断未收口的工具调用。

## 各项目怎么做

- livekit-agents：本地条目 id 直接作上游 item id；OpenAI 镜像 + LCS diff，重连重放镜像（排除工具调用）；Gemini 有 handle 用 handle；跨模型兜底用 agent chat_ctx 建新会话并 `regenerate_on_swap`。`llm/remote_chat_context.py:RemoteChatContext`、`llm/utils.py:compute_chat_ctx_diff`、`RealtimeSession._main_task`（`_reconnect`）、`llm/realtime_fallback_adapter.py:RealtimeModelFallbackAdapter._swap`。致命错误 `_is_fatal_error`。
- pipecat：首次灌历史，之后只 diff 工具结果；OpenAI 不自动重连，`reset_conversation` 手动；Gemini 3 次内自动重连带 handle；Nova 主动换会话。`services/openai/realtime/llm.py:OpenAIRealtimeLLMService._handle_context`、`services/google/gemini_live/llm.py:GeminiLiveLLMService._reconnect`、`services/aws/nova_sonic/session_continuation.py:SessionContinuationHelper`。`LLMContextSummarizer` 只改本地。
- qwen-audio-agent：状态走 instructions（默认不重发 `session.update`）；建连后把近期对话拼成 `<restored_context>` user 条目；指数退避。`RealtimeFrontend.updateAgentContext`、`restoreRecentConversation`、`voice/reconnect-backoff.mjs:ReconnectBackoff`。
- ten-framework：S2S 断线 1 s 后重连，`message_context` 逐条 `conversation.item.create` 回放，无退避、无错误分级。`openai_mllm_python/extension.py:OpenAIRealtime2Extension._handle_reconnect`。
- openai-realtime-agents：上游为真相，无断线重连，手动换 agent 历史全丢。`App.tsx:handleSelectedAgentChange`。
- pipecat-flows：切节点 `APPEND` / `RESET`，摘要改在 pre_action 推 `LLMSummarizeContextFrame`。`manager.py:FlowManager._update_llm_context`。
- xiaozhi-esp32-server：一个 WS 连接一个会话，`Dialogue` 无上限；跨会话靠记忆模块关闭时压缩。`utils/dialogue.py`、`mem_local_short`。
- moshi：上下文是模型 token 缓存，断线 `reset_streaming()`；moshi-server 4096 步（约 5.5 分钟）上限。`handle_chat`。

## 相关

- 架构：02-architectures/state-and-context.md（真相归属、状态块、快照字段 2.6 节）、02-architectures/s2s.md（第 4 节上游限制、4.2 节中途注入）、02-architectures/turn-model.md（回合终态是写快照时机）、02-architectures/floor-control.md、02-architectures/half-cascade.md
- 基础：01-foundations/transport.md
- 机制：03-mechanisms/interruption.md、03-mechanisms/tool-calls.md、03-mechanisms/evaluation.md、03-mechanisms/audio-preprocessing.md
