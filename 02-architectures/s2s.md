# 端到端 S2S：语音进语音出

> 状态：草稿
> 最后更新：2026-10-02

**要点**：S2S 把听、想、说交给一个实时模型，音频进、音频出，中间没有独立的 ASR 和 TTS。普通回合最快、最自然，但代价是**你接入的是别人的会话模型**：判停、上下文、缓存、会话长度、错误语义都由上游决定。工程上的大部分功夫花在两件事上：一是摸清并绕开上游的限制，二是把"智力"和"事实"从模型里搬到自己的代码里。

---

## 1. 这种形态是什么

```
 设备 / 客户端
     │ 音频帧、按键、事件
     ▼
 ┌──────────────────────────── 网关（自己的代码）────────────────────────────┐
 │  回合状态机 · 输出仲裁 · 工具层 · 状态块 · 本地对话记录 · 会话重建器         │
 │                                                                            │
 │   上游适配（只做 I/O）：reader 收帧 → dispatcher 分发 → 工具在后台执行       │
 └─────────────┬───────────────────────────────────────────────▲──────────────┘
               │ input_audio_buffer.append / session.update      │ 音频 delta / 转写 /
               │ conversation.item.create / response.create      │ function_call / error
               ▼                                                 │
 ┌──────────────────────────── 上游会话（别人的代码）─────────────────────────┐
 │  服务端 VAD 判停 → 内部"听" → 模型推理（含原生 function call）→ 直接出音频   │
 │  会话状态：系统提示、对话条目、工具表、缓存                                   │
 └────────────────────────────────────────────────────────────────────────────┘
```

这里说的 S2S 指**半双工 S2S**：服务端判停后双方轮流说，用户开口时服务端取消当前回复。OpenAI Realtime、Gemini Live、Qwen Audio / Omni Realtime、StepAudio Realtime、Doubao 一类云端实时 API 都是这种形态，协议大多是 OpenAI Realtime 的方言：`session.update` + `input_audio_buffer.append` + `conversation.item.create` + `response.create / cancel`。双方能同时说话的"真全双工"另见 [full-duplex](full-duplex.md)。

Qwen3-Omni、Step-Audio 2 这类模型有开源权重，但开源仓库里**没有流式输入和实时服务端**，本地 demo 都是"录完点提交"；它们的 Realtime 版本是云端服务。所以自托管时它们更适合做 [半级联](half-cascade.md) 的理解端，作为 S2S 使用时就是云 API。

---

## 2. 适合与不适合

**适合**

- 闲聊、陪伴、讲故事这类**以在场感和表现力为主**的回合：语气、停顿、副语言都是模型原生的。
- 普通回合首音要求高、工具调用不多的产品。
- 团队不想自己运维 ASR / TTS，接受"云端一个 API 搞定"。

**不适合**

- **工具密集**的产品：S2S 的工具回合是所有形态里最慢的（见第 3 节）。
- **事实必须准确、需要出声前审核**的高风险场景：出声前没有完整文本，护栏只能事后纠偏。
- 需要长会话、强记忆的产品：上游通常只保留有限轮数，中途注入的上下文不一定被使用。
- 按键说话、儿童语音、户外噪声这类**判停容易出错**的场景：服务端 VAD 往往关不掉。能关或能半关（只静音不触发回复）时可以用，关不掉时要按 [turn-detection](../03-mechanisms/turn-detection.md) 里"按键 + 服务端判停关不掉"的做法兜底，不作首选。

---

## 3. 关键取舍

| 维度 | S2S 的表现 | 说明 |
|---|---|---|
| 普通回合延迟 | **最快** | 一份实践笔记实测：S2S 普通回合首音 0.87–0.96 s，同项目级联 P50 1.4 s（该笔记称其上游为"全双工 S2S"，按本手册的划分，它由服务端判停、轮流说话，属于半双工 S2S） |
| 工具回合延迟 | **最慢** | 同一项目实测有内容首音 2.1–2.3 s：识别 ~0.37 s → 模型给出 function call ~0.8 s → 执行 → 回传 → 模型再生成一段音频约 1.2 s。"回传后再生成"是大头 |
| 智力 | 弱于同代文本模型 | 在"选哪个工具"这种窄任务上不差（63 句冻结盲测：S2S 原生 FC 81%，小文本 LLM 84%，样本小、差异不显著）；在多步任务上差距明显（qwen-audio-agent 的客服评测：纯 S2S 44%，S2S + 外围工程 62%，纯文本大模型 68%，仓库自述） |
| 可控性 | 低 | 文本只比音频早几十毫秒；判停、上下文、截断受上游语义约束 |
| 自然度 | 最高 | 原生语音、单一声音、副语言 |
| 成本 | 一个上游 | 少运维；但缓存失效会放大输入费用（见 4.4） |
| 对上游的依赖 | **高** | 每家的会话语义不同，换上游可能要重写适配 |

**反直觉的结论**：S2S 普通回合最快，工具回合最慢。外置智力（把决策交给规则或后台模型）恰恰会制造更多工具回合，所以**外置必须和"收口但不再生成、结果由我们直接念"一起做**，否则每搬走一项智力就多付一次约 1.2 s。

---

## 4. 受制于上游的典型问题

下面这些是一份实践笔记在一个闭源全双工 S2S 上踩过的坑，配合开源框架里的对应注释。具体到某一家上游是否存在，要用探针实测，不要按厂商名推断。

### 4.1 服务端 VAD 关不掉

按键说话时，服务端 VAD 会把句中停顿切成两段、在松键前就调工具，偶尔只识别出一个字。在 qwen-audio-agent 的各家 provider 代码里都没有看到把 `turn_detection` 设为 null 的路径，DashScope 文档写明"不暴露手动 VAD 调参"。

应对（一种实践）：

- 网关**只认按键为回合边界**，忽略上游的 `speech_started` 一类事件；被切出的多段归到同一回合，不产生新回合（见 [turn-model](turn-model.md)）。
- 上游支持"只切段不自动回复"（OpenAI 类 `create_response=false`）就用它，提交时统一 `response.create`；不支持时取消上游自行开始的回复，按代际丢弃其输出。
- 规则短路（如"≤2 字算噪音"）**只在模型尚未开始调工具或说话时生效**，否则会吞掉被切出来的半句话。
- 收到"提交完成"且已有识别结果时立即定稿；识别开始后一定时间无结果，主动出声兜底。
- 更根本的方向：在上游前加自己的 VAD、降噪和停顿压缩。

### 4.2 上下文注入不被使用

同一项目的实测（断线重连后问"我刚才说我叫什么"，各 6 次）：

| 方式 | 结果 |
|---|---|
| 建会话时在初始化参数里带入成对的历史问答 | 6/6 |
| 用会话 id 接续 | 1/6（历史看得到，模型不用） |
| 建会话后逐条插入历史 | 0/6 |
| 中途插入 system / user 条目说明状态 | 基本不看 |

开源印证：Pipecat 所有 S2S 服务在会话中途只同步工具结果，适配器注释里写着"OpenAI 逐条插入后模型会忘了自己能出音频""Gemini 2.5 无视灌入的历史"；LiveKit 的 OpenAI 插件重连时恰好用的是"建会话后逐条插入"。结论（一种实践）：**历史走会话初始化参数，状态走系统提示，中途插入不可靠**。详见 [state-and-context](state-and-context.md)。

### 4.3 轮数上限

上游通常只保留最近约 20 轮问答（该项目的观察值），早期信息（名字、带没带孩子）会丢。应对：硬信息收进状态块；在自己这层做摘要，按自己的节奏主动换会话，用"摘要 + 最近几轮 + 状态块"重新初始化。

### 4.4 配置更新导致缓存失效

状态随系统提示更新（`session.update`）时，上游会重新预填整段对话，缓存作废：该项目实测未命中缓存的输入 ×3.2；只在粗粒度变化时更新，首音多 80–200 ms，每轮都更新多 150–270 ms。效果是值得的（多轮上下文用例 66/80 → 76/78），但要控制频率：只在两轮之间、只在"不更新就会选错动作"的字段变化时更新。工具表同理：Gemini、Ultravox、AWS 改工具都要重连，所以**工具表建会话时一次给全，按阶段限制在工具层拒绝**。

### 4.5 其他常见差异

| 问题 | 说明 |
|---|---|
| 工具结果回注后是否自动续答 | 各家不同：Doubao、Google Live 自动续答，省一次往返，但失去"结果由我们直接念、模型不再生成"这条路 |
| 有无按回复的临时指令 | 没有时只能插入一条持久对话项，会污染历史（StepFun 的做法） |
| 悬着的函数调用 | 该项目试过"只选工具、直接念结果、不回传"：首音降到 1.0–1.1 s，但调用悬着会导致约 21% 重复调用。**每个调用都要收口** |
| 文本条目翻转模态 | 往音频会话灌文本历史后，模型可能改用纯文本回复；trace 要记回复模态 |
| 错误语义 | 不是每个 `error` 都是断线；要分"重建也没用 / 可重建 / 上下文超限 / 非致命" |

**用能力声明表达差异**：LiveKit 的 `RealtimeCapabilities`、qwen-audio-agent 的 provider 能力位（单响应槽位、自动续答、按回复指令、会话可变、能否恢复上下文）都在做同一件事：上游差异写成字段，通用代码读字段，不按厂商名分支。

---

## 5. 把智力搬出 S2S

S2S 的定位可以收窄为"负责在场和表现力"，智力放在别处。按"从模型里拿走多少"排序的六个手段（一份设计讨论的归纳）：

| # | 手段 | 从 S2S 拿走什么 |
|---|---|---|
| 1 | 缩小决策空间：单一委派工具 + 白名单、按阶段收窄、外置意图判定 | 大部分"选什么" |
| 2 | 事实不靠它：逐字念、RAG 分层、上屏 | "知道什么" |
| 3 | 规则短路 + 规则修正层 + 写操作门槛 | "决定做什么"的最终权 |
| 4 | 前台 / 后台分工：前台 S2S 承接，后台文本模型想清楚 | "想清楚、组织语言" |
| 5 | Prompt 结构：人设 → 规则 → 状态块，硬规则可检查 | 不拿走，让剩下的更稳 |
| 6 | 评测驱动迁移：按失败类别逐项搬走 | 它实测做得不好的那部分 |

开源里的四个代表实现都只做了第 4 种（委派）：openai-realtime-agents 的 chat-supervisor、LiveKit GPT-Live 的 delegation、Pipecat 的 `BackendLLMWorker`、qwen-audio-agent 的 `spawn_thinking`。补不了的三样是：**听错**（转写错了，外置层只能拒绝不能纠正）、**讲解时补细节**（只要让模型自己组织语言就会补）、**长对话管理**（模型怎么用长上下文是它内部的事）。

---

## 6. 代表项目

| 项目 | 角色 | 说明 |
|---|---|---|
| [openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md) | 官方示例 | Chat-Supervisor（实时模型对话 + gpt-4.1 做工具和难题）与 Sequential Handoff（`session.update` 换指令和工具）；结果回传后紧跟 `response.create`，把收口和再生成绑死 |
| [qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md) | 前台 S2S + 后台 agent | 默认上游 Qwen Audio 3.0 Realtime；provider 能力位覆盖 DashScope、StepFun、Doubao、MiniCPM-o 等 |
| [livekit-agents](../04-projects/frameworks/livekit-agents.md) | 框架 | `llm.RealtimeModel`（OpenAI Realtime、Gemini Live 等）与 `DuplexModel`；`RealtimeCapabilities` 声明上游能力 |
| [pipecat](../04-projects/frameworks/pipecat.md) | 框架 | `OpenAIRealtimeLLMService`、`GeminiLiveLLMService`、AWS Nova Sonic、Ultravox 等 S2S 服务；工具结果绕 context 聚合器后再 diff 发给上游 |
| [ten-framework](../04-projects/frameworks/ten-framework.md) | 框架 | 示例 `voice-assistant-realtime` 的图用 `openai_mllm_python` 扩展 |
| [qwen3-omni](../04-projects/e2e-models/qwen3-omni.md)、[step-audio2](../04-projects/e2e-models/step-audio2.md) | 模型 | 开源权重无实时服务端；Realtime 版本在云端 |

---

## 7. 涉及的机制

| 机制 | S2S 里的关注点 |
|---|---|
| [turn-detection](../03-mechanisms/turn-detection.md) | 服务端 VAD 关不掉时的处理 |
| [tool-calls](../03-mechanisms/tool-calls.md) | 收口与再生成分离、旁路提示语、委派 |
| [first-audio](../03-mechanisms/first-audio.md) | "有声音"和"有内容"分开计；工具回合的提示语 |
| [interruption](../03-mechanisms/interruption.md) | cancel + truncate；打断时已回传的调用补极短结果 |
| [session-recovery](../03-mechanisms/session-recovery.md) | 重灌、会话重建器、错误分级与重建预算 |
| [evaluation](../03-mechanisms/evaluation.md) | 冻结盲测集；按版本比较人设、状态块 |

---

## 待确认

- 各家上游的服务端 VAD 能否关闭（DashScope 试 `turn_detection: null`、Doubao 的 `input_audio_mute/unmute` 能否当"按键期间才听"），需要探针。
- "上游只保留约 20 轮"是一个闭源上游上的观察，其他上游的上限未测。
- 按回复覆盖指令是否同样导致缓存失效。
- 上游 cancel 之后是否真的会推来迟到的音频 delta。
- 4.2 的接续对比每组只有 6 次、只测了"名字"一类问题。

---

素材来源：一份实践笔记（未发布）§5、§6；原始长文 [S2S 架构下的智力外置](essays/s2s-intelligence-externalization.md)；调研笔记 `notes/e2e-models.md`（未发布）、`openai-realtime-agents.md`、`qwen-audio-agent.md`、`pipecat.md`、`pipecat-B.md`、`livekit-agents-B.md`。
