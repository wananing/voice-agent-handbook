# 会话恢复与上下文同步：断线、重连、上游会话映射

> 状态：草稿
> 最后更新：2026-10-02

## 问题是什么

这一页回答两个相关的问题。

**（a）本地上下文和上游会话怎么保持一致。** 级联链路每轮自己拼消息，"上下文"只有一份，不存在同步问题。S2S 链路不一样：上游（OpenAI Realtime、Gemini Live、Nova Sonic、DashScope 一类）在服务端维护自己的 conversation，agent 本地还有一份记录，两份会在这几个地方分叉：工具结果有没有回传，中途插入的消息模型用不用，被打断的回复在上游有没有截断，改 instructions 或工具表会不会让缓存失效。

**（b）断线以后能恢复什么。** 断线分两层：网关到上游的连接断了（上游会话没了，或者到了时长上限），设备到网关的连接断了（移动网络、休眠、切 App）。恢复要回答：用什么重建上游会话（重放镜像、用续接 handle，还是从本地上下文重灌），设备重连后算不算同一个会话，断线时没有终态的那一轮怎么收尾，长会话的历史放不下了怎么办。

不处理的后果：重连后模型忘了用户刚说过的话；被打断的回复以全文形式回到上游，模型以为自己说完了；重连时把已经执行过的写操作再执行一遍；长会话里早期信息（名字、陪同人）丢失。

"本地上下文是对话的唯一真相、上游只是镜像"这条原则，以及状态块、快照内容的定义，见 [state-and-context](../02-architectures/state-and-context.md)，本页不重复，只讲机制和各项目的做法。

## 解法分类

**（a）上下文同步：本地和上游谁是真相、同步什么**

1. **无状态请求，每轮全量拼。** 上下文只在本地，每轮把完整消息发给模型。级联和无状态的端到端模型（Qwen3-Omni、Step-Audio 2、Ultravox 的 OpenAI 接口）都是这样。不存在同步问题，只有长度问题。
2. **本地镜像 + 全量 diff。** 本地维护一份"上游现在有什么"的镜像，本地上下文变化时算 diff，增删条目推给上游。LiveKit 的 OpenAI 插件是代表：`RemoteChatContext` 只在收到服务端确认后更新，`compute_chat_ctx_diff` 做 LCS diff。
3. **只同步工具结果。** 建会话时把历史灌一次，之后本地上下文的其他变化一概不推，只把新完成的工具结果 diff 出来回传。Pipecat 所有 S2S 服务都是这个套路。
4. **上游为真相，本地只做显示用的镜像。** 本地用服务端事件重建 history，只用于 UI 或转发给后台模型。openai-realtime-agents 是这样。
5. **状态走 instructions，历史在建连时注入一次。** 会话中尽量不碰上游；需要的状态写进 instructions，近期历史在建连时打包成一条消息写入。qwen-audio-agent 是这样。

和同步相关的几个子问题，各家的回答：

| 子问题 | 已知的做法和证据 |
|---|---|
| 只写不读 | 镜像只用来算 diff 和重放，不反推本地记录。LiveKit 的 fallback adapter 重建时用 agent 的 chat_ctx（用户听到的），不用服务端镜像 |
| 工具结果回传 | 是所有 S2S 实现里唯一在会话中途稳定同步的内容（Pipecat、LiveKit、qwen-audio-agent、TEN S2S） |
| 中途追加消息是否生效 | 一份实践笔记实测：建会话后逐条插入历史 0/6，中途插入 system / user 条目说明状态"基本不看"；Pipecat 的 OpenAI Realtime `_handle_messages_append` 未实现。详见 [s2s](../02-architectures/s2s.md) 第 4.2 节 |
| truncate 是否同步 | LiveKit（OpenAI）发 `conversation.item.truncate`；Pipecat（OpenAI）按"收到首个 delta 起的墙钟时间"发 truncate；Gemini、AWS 不支持；TEN 的 truncate 被注释掉；qwen-audio-agent 不截断上游。细节见 [interruption](interruption.md) |
| 配置更新导致缓存失效 | 改 instructions 会让上游重新预填整段对话。qwen-audio-agent 用 `refreshSession:false` 只更新本地缓存、不重发 `session.update`；一份实践笔记实测未命中缓存的输入 ×3.2。Gemini、Ultravox、AWS 改工具表要重连 |

**（b）断线重连：用什么重建上游会话**

1. **不恢复。** 一个连接就是一个会话，断线即丢，新连接从空历史开始。xiaozhi、unmute、Moshi、FunASR / SenseVoice 的 WebSocket 服务都是这样。
2. **重放镜像。** 新连接建好后，把本地保存的"上游应有内容"逐条 `conversation.item.create` 发回去。LiveKit 的 OpenAI 插件、TEN 的 OpenAI 扩展。
3. **上游续接 handle。** 上游给一个 resumption handle，重连时带上，由服务端恢复会话。Gemini Live（LiveKit、Pipecat 都用）。
4. **从本地上下文重灌。** 用本地记录（最好是"用户实际听到的"版本）在建会话参数里带入历史。LiveKit `RealtimeModelFallbackAdapter`、Pipecat `reset_conversation`、Gemini 没有 handle 时的退路。
5. **主动换会话。** 不等断线，在时长上限前、空档时预建或串行换一个新会话。Pipecat Nova Sonic 的 `SessionContinuationHelper`、LiveKit 的 `max_session_duration`。
6. **设备侧跨连接的会话快照。** 设备到网关断线后，在恢复窗口内重连算同一个用户会话，状态和近期对话从快照恢复，上游会话按 4 重建。一份实践笔记用 Redis 快照、30 分钟窗口、最近 12 轮对话。开源项目里没有对应实现。

做 6 时要分清两层窗口（一份实践笔记的规范）：

- **续接窗口**（短，例如 30 s）：网关到上游的会话还在，设备带 `session_id` 重连就能接着用，上游不用重建。
- **会话恢复窗口**（长，例如 30 分钟）：上游会话早就没了，但用户会话还在。重连后从快照恢复状态和近期对话，上游按 4 重建。

两层都过期后才算新会话。用户主动说"重新开始"是唯一提前开新会话的方式，可以分"只清对话"和"连业务状态一起清"两档。

**被打断那一轮怎么办**：三种做法。不管（LiveKit OpenAI 插件：等待中的 `generate_reply` 以错误结束，`voice/` 下没有监听 `session_reconnected`，不重答）；自动重新生成（LiveKit fallback adapter 的 `regenerate_on_swap`）；以最后一句 user 作为新会话的首轮输入重放（一份实践笔记的规范，前提是写操作不会重复执行）。

**历史上限与摘要**：硬截断（AWS Nova Sonic 插件本地超过 40 条截断；Qwen3-Omni demo 超过 5 个音频回合整轮删除；Moshi 有固定步数上限）；文本 LLM 做摘要（LiveKit `ChatContext._summarize`、Pipecat `LLMContextSummarizer`、pipecat-flows 的 `RESET` 加 pre_action 摘要）；跨会话的长期记忆（xiaozhi-esp32-server 在会话关闭时用 LLM 压缩整段对话，下次注入 system prompt）。

## 各解法的代价

**同步策略**

| 做法 | 延迟 | 复杂度 | 对上游的依赖 | 失败模式 |
|---|---|---|---|---|
| 每轮全量拼 | 长会话预填变长；前缀稳定时可命中前缀缓存 | 最低 | 无 | 超出模型上下文（unmute 的 vLLM `--max-model-len=1536`，超长行为待确认） |
| 镜像 + 全量 diff | 每次同步逐条发、等确认（LiveKit 统一等 5 s） | 高：要维护镜像、LCS diff、`previous_item_id` | 上游必须支持删除、插入条目 | 镜像和服务端不一致（LiveKit 没处理 `conversation.item.truncated`，镜像里可能仍是全文，待确认）；中途插入不一定被模型使用 |
| 只同步工具结果 | 无额外开销 | 低 | 只要求能回传工具结果 | 本地的摘要、截断、改写都不会到达上游，要生效必须重建会话 |
| 上游为真相 | 无 | 低 | 完全依赖上游 conversation | 断线即丢（openai-realtime-agents 手动换 agent 时历史全丢） |
| 状态走 instructions | 每次 `session.update` 让缓存作废：一份实践笔记实测首音 +80–200 ms（粗粒度更新）或 +150–270 ms（每轮更新） | 中 | 要求能中途更新 instructions | 更新太频繁时成本和首音都变差；更新太少时模型选错动作 |

**重连策略**

| 做法 | 恢复速度 | 恢复质量 | 失败模式 |
|---|---|---|---|
| 不恢复 | 最快 | 无 | 用户要从头说 |
| 重放镜像 | 建连 + 逐条发送 | 一份实践笔记实测"建会话后逐条插入"0/6；LiveKit 重放时还排除了全部 function call 和 output | 被截断的回复可能以全文回到上游；工具调用丢失；往音频会话灌文本条目可能让模型改用纯文本回复 |
| resumption handle | 快：服务端直接恢复 | 依赖上游；Pipecat 的 handle 只存在内存，不跨进程 | 进程重启就没了；同类"会话 id 接续"在一份实践笔记里实测 1/6 |
| 从本地重灌（建会话参数） | 建连 + 一次带入 | 一份实践笔记实测建会话时带入成对问答 6/6（样本只有 6 次） | 上游对历史格式有约束：Gemini 灌带 tool call 的历史报 1007、2.5 要求以 user 结尾；AWS 40 条上限 |
| 主动换会话 | 空档交接，用户无感 | 等同重灌 | 预建的会话有空闲超时（Nova 30 s）；交接期用户音频要缓冲（Pipecat Nova 回放约 3 s） |
| 设备侧快照 | 取决于快照存储 | 状态和近期对话完整 | 快照写入频率和存储成本；在途写操作需要幂等键才能安全恢复 |

**上游差异决定了能选哪种做法**（各项目页和调研笔记里能看到的部分，按上游列）：

| 上游 | 删改条目 | truncate | 续接 | 历史带入的约束 | 时长 / 其他 |
|---|---|---|---|---|---|
| OpenAI Realtime | 支持（`conversation.item.delete` / `create`） | 支持（`conversation.item.truncate`） | 无 handle | 无特别约束；往音频会话逐条插入文本，Pipecat 注释说模型"会忘了自己能出音频" | LiveKit 默认 20 分钟主动重连 |
| Gemini Live | 不支持删除；改工具表要整条连接重建 | 不支持，依赖服务端 `interrupted` | resumption handle（Pipecat 只存内存） | 带 tool call 的历史报 1007，要转成文字；2.5 要求以 user 结尾，用户第一句是语音时会无视灌入的历史 | 1007 = 上下文耗尽 |
| AWS Nova Sonic | 历史只在建会话时一次带入 | 不支持 | 无 | LiveKit 插件本地超过 40 条截断；单条 50 KB / 总量 200 KB（调研笔记） | Pipecat 360 s 主动换会话；预建会话 30 s 空闲超时 |
| GPT-Live（LiveKit 插件） | instructions、历史在会话开始后不可变 | 待确认 | 无 | 128 条 / 8192 token（调研笔记） | 待确认 |
| DashScope 等单槽位上游（qwen-audio-agent） | 待确认 | 不截断 | 无 | 部分厂商注入历史会被当成实时输入，用 `restoreConversationContext=false` 跳过 | 取消超时未释放槽位就断开重连 |

LiveKit 的 `RealtimeCapabilities` 和 qwen-audio-agent 的 provider 能力位都在做同一件事：把这些差异写成字段，通用代码读字段，不按厂商名分支。

**错误分级的代价**：把所有 `error` 都当断线，会无谓地重建、缓存全冷；把所有错误都当可恢复，遇到鉴权、配额错误会无限重连（LiveKit 的 OpenAI 插件注释说明：重连能成功但每次生成都失败）。上下文超限（Gemini 1007）原样重放必然再失败，LiveKit 的 Gemini 插件把它判为致命、不重试。

**被打断那一轮的代价**：自动重答或重放最后一句 user，可能重复执行写操作；不重答，用户会觉得"它没听见"。

## 各项目怎么做

| 项目 | 做法 | 代码路径 | 备注 |
|---|---|---|---|
| [livekit-agents](../04-projects/frameworks/livekit-agents.md) | 本地 `ChatContext` 条目 id 直接作上游 item id；OpenAI 用服务端镜像 + LCS diff 全量同步；重连重放镜像（排除工具调用）；Gemini 有 handle 用 handle，否则本地上下文打包重发；跨模型兜底用 agent 的 chat_ctx 建新会话并重新生成 | `llm/remote_chat_context.py:RemoteChatContext`、`llm/utils.py:compute_chat_ctx_diff`、`RealtimeSession._main_task`（`_reconnect`）、`llm/realtime_fallback_adapter.py:RealtimeModelFallbackAdapter._swap` | `max_session_duration` 默认 20 分钟走同一套重连；致命错误白名单 `_is_fatal_error`；Gemini 1007 判致命；AWS 本地 > 40 条截断；框架层无自动摘要 |
| [pipecat](../04-projects/frameworks/pipecat.md) | 首次收到 context 时灌历史，之后只 diff 新完成的工具结果回传；OpenAI 不自动重连，`reset_conversation` 手动重建；Gemini 连续 3 次失败内自动重连并带 resumption handle；Nova 主动换会话 | `services/openai/realtime/llm.py:OpenAIRealtimeLLMService._handle_context` / `reset_conversation`、`services/google/gemini_live/llm.py:GeminiLiveLLMService._reconnect`、`services/aws/nova_sonic/session_continuation.py:SessionContinuationHelper` | `LLMContextSummarizer` 只改本地 context，要生效需重建会话，仓库里没有自动接线；历史灌入格式各服务不同 |
| [pipecat-flows](../04-projects/frameworks/pipecat-flows.md) | 切节点时 `APPEND` 追加任务说明，`RESET` 用任务说明替换全部历史；`RESET_WITH_SUMMARY` 已废弃，改在 pre_action 推 `LLMSummarizeContextFrame` | `manager.py:FlowManager._update_llm_context` | 断线不涉及；flow `state` 只在内存 |
| [openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md) | 上下文以服务端 conversation 为准，SDK 用服务端事件重建本地 history，只用于 UI 和委派；委派时全量重发过滤后的历史，丢掉 function_call / output | `App.tsx:handleSelectedAgentChange` | 没有断线重连；手动换 agent 会断开重连、历史全丢 |
| [qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md) | 状态走 instructions，更新时默认不重发 `session.update`；建连收到 `session.updated` 后，把近期对话拼成一条 `<restored_context>` user 条目写入；指数退避重连；内容安全拒绝时隔离出错回合再重建 | `RealtimeFrontend.updateAgentContext`、`restoreRecentConversation`、`voice/reconnect-backoff.mjs:ReconnectBackoff`、`voice/realtime-recovery-context.mjs:RealtimeRecoveryContext` | 上游声明 `restoreConversationContext=false` 时跳过注入；注入效果无评测，待确认；后台 ACP 上下文按 owner 跨语音会话复用 |
| [ten-framework](../04-projects/frameworks/ten-framework.md) | 级联：`LLMExec` 每轮整段发；S2S：断线 1 s 后重连，`_resume_context` 把 `message_context` 逐条 `conversation.item.create` 发回 | `openai_mllm_python/extension.py:OpenAIRealtime2Extension._handle_reconnect` | 没有退避和次数上限（注释说的 exponential backoff 未实现）；没有镜像、diff、错误分级；`message_context` 怎么积累在 `ten_ai_base` 包里，待确认 |
| [unmute](../04-projects/full-duplex/unmute.md) | 每轮把完整 `chat_history` 发给 LLM，历史只在内存 | `Chatbot.preprocessed_messages` | 断线即丢；无截断、无摘要 |
| [moshi](../04-projects/full-duplex/moshi.md) | 上下文是模型内部 token 缓存，断线即 `reset_streaming()` | `handle_chat` | 硬上限：moshi-server 4096 步（约 5.5 分钟）、360 s 超时；不能注入上下文 |
| [xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md) | 一个 WebSocket 连接一个会话，`Dialogue` 无上限、无摘要；跨会话靠记忆模块在关闭时压缩整段对话 | `utils/dialogue.py`、`mem_local_short`、`receiveAudioHandle.py:no_voice_close_connect` | 断线不恢复；120 s 无人声念结束语后断开 |
| [xiaozhi-esp32](../04-projects/devices/xiaozhi-esp32.md) | 会话 = 一次音频通道连接，`session_id` 由服务端 hello 下发；断线后重新 hello 得到新会话 | `Protocol::IsTimeout` | 没有断点续传或序号补齐；120 s 无下行判超时 |
| [qwen3-omni](../04-projects/e2e-models/qwen3-omni.md) | 模型无状态，调用方持有 messages；demo 保留原始音频历史，超过 5 个音频回合整轮删除 | `web_demo.py:format_history` | 删除改变前缀，前缀缓存失效；模型不产出用户转写 |
| [step-audio2](../04-projects/e2e-models/step-audio2.md) | 模型无状态；用户音频原样保留，助手出语音时存 `tts_content` | `examples-vllm.py` | 要文本历史需额外转写；多模态前缀缓存是否生效待确认 |
| [ultravox](../04-projects/e2e-models/ultravox.md) | OpenAI 接口无状态，调用方持有历史；本地会话模式靠 KV cache 记住过去的音频 | `_build_past_messages` | KV 丢失后过去的用户话语只剩 `eos_token` 占位，要可恢复就得另跑 ASR |
| [funasr](../04-projects/asr/funasr.md) | 在线识别状态挂在 WebSocket 连接上 | `websocket-server-2pass.cpp:on_open` / `on_close` | 断线不续接 |
| [sensevoice](../04-projects/asr/sensevoice.md) | 每个 WS 会话自带音频缓冲，无声超过 `--vad-slot-ms` 后重置 | `sensevoice-server.cpp` | 默认 2000 ms |
| [aiortc](../04-projects/audio-processing/aiortc.md) | 断开后重新 offer / answer，aiortc 里搜不到 ICE restart；Pipecat 自己处理重协商 | `smallwebrtc/connection.py:RenegotiateMessage`（Pipecat） | 只是传输层恢复，不涉及应用会话 |
| [webrtc-audio-processing](../04-projects/audio-processing/webrtc-audio-processing.md)、[rnnoise](../04-projects/audio-processing/rnnoise.md) | 前处理有内部状态（AEC 收敛状态、`DenoiseState`），新通话时重置 | `audio_processing.h:124-125`（`Initialize()`） | 和对话上下文无关，但"跨轮保持还是每轮重置"会影响 AEC 收敛，见 [audio-preprocessing](audio-preprocessing.md) |
| [fireredtts2](../04-projects/tts/fireredtts2.md)、[fish-speech](../04-projects/tts/fish-speech.md) | TTS 侧的跨轮 / 跨 batch 韵律上下文，只存在一次调用内部 | `generate_dialogue`、`generate_long` | 没有持久化或恢复接口 |

不涉及（无状态或只处理单段音频）：[fireredasr](../04-projects/asr/fireredasr.md)、[sherpa-onnx](../04-projects/asr/sherpa-onnx.md)、[smart-turn](../04-projects/turn-vad/smart-turn.md)、[ten-vad](../04-projects/turn-vad/ten-vad.md)、[cosyvoice](../04-projects/tts/cosyvoice.md)、[index-tts](../04-projects/tts/index-tts.md)、[spark-tts](../04-projects/tts/spark-tts.md)、[voxcpm](../04-projects/tts/voxcpm.md)。

## 我们的判断

**级联、半级联、无状态端到端模型**：每轮从本地上下文全量拼消息，不做镜像。长会话用"状态块 + 最近 K 轮原样 + 文本 LLM 摘要"控制长度，状态块放在消息尾部保持前缀稳定。断线后只要本地上下文还在，重拼即恢复。用端到端模型时要另跑 ASR 或额外转写，保证历史里有用户文本，否则 KV 一丢就恢复不了（Ultravox、Qwen3-Omni、Step-Audio 2 都不产出用户转写）。

**S2S，会话中途的同步**：选"只同步工具结果"，不选全量 diff。

- 本地上下文是唯一真相，上游只写不读。镜像如果要维护，只用于判断"哪些工具结果已回传"，不用于重放。
- 会话中途只允许：工具结果回传；上游支持时用按回复的临时指令；协议有专门追加通道时用追加通道。不中途插入历史或状态说明。
- 上游支持 truncate 时，按设备播放回执的位置截断；不支持时只改本地记录，并在下一次重建时把"被打断"带进去。
- 状态变化只在两轮之间、只在"不更新就会选错动作"的字段变化时更新 instructions。其余的懒更新，等下一次重建时带上。工具表建会话时一次给全。

**S2S，重建上游会话**：全系统只有一个重建器，断线、上下文超限、主动换会话都走它。

```
触发：断线 / 可重建错误 / 上下文超限 / 时长或轮数到点 / 状态变化
  │  紧急触发立即走；其余等空档（助手说完、无未收口调用、用户没在说话）
  ▼
从本地上下文投影：人设 + 状态块 + 历史（摘要 + 最近 K 轮，assistant 取已播放文本）+ 工具表
  │  上下文超限时先缩历史，不原样重放
  ▼
建新会话，历史走建会话参数 ──► 重建"已回传工具结果"登记表（历史里的结果不再回传）
  ▼
断线时没有终态的回合：按下面的规则重答或本地收口
  ▼
回放交接期缓冲的用户音频 ──► 写 trace：rebuild_reason、history_scope、首轮缓存命中
```

- 输入固定为"人设 + 状态块 + 从本地投影的历史 + 工具表"，历史在建会话参数里带入，assistant 一侧只放用户实际听到的文本，被打断的条目带显式标记。
- 不重放镜像。resumption handle 能用就用，但只当优化，不能替代重灌（进程重启就没了）。
- 往音频会话灌文本历史后，模型可能改用纯文本回复。trace 要记每轮的回复模态；出现纯文本回复时交给本地 TTS 出声，同一会话里反复出现就在下一个空档换会话。
- 错误分四类，按回合计重建预算（例如 3 次、指数退避、连接稳定一段时间后清零，参考 Pipecat Gemini 和 AWS 的取值）：

| 类别 | 例子 | 处理 |
|---|---|---|
| 重建也没用 | 鉴权、配额、计费（白名单，参考 LiveKit `_is_fatal_error`） | 停止重建，说一句本地兜底话，告警 |
| 会话失效、可重建 | 断线、服务端关闭会话、可重试的限流 | 走重建器，补一句"信号不好"类本地话术 |
| 上下文超限 | Gemini 1007 | 先缩历史（摘要或截断到最近 K 轮 + 状态块）再重建 |
| 非致命 | 取消竞态（`response_cancel_not_active` 等）、审核命中、条目级错误、空提交 | 只让这一回合失败，会话继续；被拒的回合内容不进后续历史 |

- 上游有时长上限（LiveKit 的 OpenAI 默认 20 分钟、Nova 约 6 分钟）或轮数上限（一份实践笔记在一个闭源上游观察到约 20 轮），就按轮数、时长在空档主动换会话，把"会话满了"变成常规路径。

**设备侧断线**：自有设备或 App 要做跨连接快照，在恢复窗口内重连算同一会话，快照在每个回合终态后写一次，不含上游镜像和原始音频。窗口长度按产品定，30 分钟、12 轮是一份实践笔记的值，不是通用答案。快照字段见 [state-and-context](../02-architectures/state-and-context.md) 第 2.6 节。浏览器演示、一次性会话可以不做，断线即新会话。

**被打断那一轮**：

- 该回合没有发起写操作：拿最后一句 user 作为新会话的首轮输入，重新回答。
- 已执行写操作：不重放，用本地通道把结果告诉用户，同时写进状态块。写操作状态未知时，先用幂等键查后端。
- 已经播了一部分的 assistant 回复：带打断标记进历史，不自动重说。
- 断线时用户正在说话：已缓冲的用户音频在新会话就绪后回放（可以快于实时），不转成文本重放。交接期间只允许本地提示音和本地话术出声。
- 旧会话上还在跑的工具调用：新会话不认识它的 call id，由本地收口，结果走本地通道念或上屏，不回传给新会话。

**长会话**：用户的名字、陪同人、已选路线这类硬信息放进状态块，不依赖历史或摘要。摘要用自己的文本 LLM 提前异步生成，每次都从原始条目重新生成，不对摘要再摘要。摘要不切断没有收口的工具调用（Pipecat 的 `LLMContextSummarizer` 就是这么做的）。

## 相关

- 架构层：[state-and-context](../02-architectures/state-and-context.md)（真相归属、状态块、快照）、[s2s](../02-architectures/s2s.md)（第 4 节，上游限制）、[turn-model](../02-architectures/turn-model.md)（回合终态是写快照的时机）、[floor-control](../02-architectures/floor-control.md)（播放回执、跨连接的播报领取）、[half-cascade](../02-architectures/half-cascade.md)
- 其他机制：[interruption](interruption.md)（按播放位置截断）、[tool-calls](tool-calls.md)（工具调用收口、重建后的在途调用）、[evaluation](evaluation.md)（重连脚本、`rebuild_reason` 进 trace）、[audio-preprocessing](audio-preprocessing.md)（前处理状态跨轮保持）
- 基础：[transport](../01-foundations/transport.md)（序号、断线检测）
- 项目页：[livekit-agents](../04-projects/frameworks/livekit-agents.md)、[pipecat](../04-projects/frameworks/pipecat.md)、[qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md)、[ten-framework](../04-projects/frameworks/ten-framework.md)、[openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md)、[xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md)、[unmute](../04-projects/full-duplex/unmute.md)、[moshi](../04-projects/full-duplex/moshi.md)
