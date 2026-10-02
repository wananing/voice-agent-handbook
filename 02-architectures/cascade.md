# 级联：ASR → LLM → TTS

> 状态：草稿
> 最后更新：2026-10-02

**要点**：级联把语音对话拆成"听写 → 文本推理 → 朗读"三段，每段都能单独换、单独测，文本在中间，出声之前可以检查、改写或拦截。代价是延迟一段段叠加：段与段之间只要有一处是串行等待，首音就比三个模型首包之和多出一到两秒。所以级联的工程重点是**流式衔接**，模型选型反而是次要的。

---

## 1. 这种形态是什么

```
 麦克风 ─► 判停（VAD / 语义判停 / 按键）
              │ 用户音频
              ▼
         ┌─────────┐ partial / final ┌──────────┐  token 流  ┌────────┐  文本片段  ┌─────────┐  音频块  ┌──────────┐
         │   ASR   │───────────────► │ 文本 LLM │──────────► │ 分句器 │──────────► │   TTS   │────────► │ 下行限速 │─► 播放
         │（流式） │                 │ 工具调用 │            │首句优先│            │（流式） │          │ 浅缓冲   │
         └─────────┘                 └────┬─────┘            └────────┘            └────┬────┘          └──────────┘
                                          │ 工具结果                                     │ 已播放的词（时间戳）
                                          ▼                                              ▼
                                     工具层 / 后端                       上下文：只记用户实际听到的部分
```

三段之间流动的都是**文本**：ASR 把音频变成文字，LLM 读文字、写文字，TTS 把文字念出来。上下文就是一份 chat messages，由应用自己拼，每轮可以随意改。级联是开源框架的默认形态，也是最容易理解、最容易调试的一种。

"级联"不等于"慢"。慢的是**全串行**的级联：等判停确认才送 ASR，等 ASR 定稿才发 LLM，等 LLM 整段写完才合成，等合成完整句才下发。把每一段改成流式、让后一段提前起跑，才是这种形态真正的样子。

---

## 2. 适合与不适合

**适合**

- 需要**出声前审核**的场景：高风险内容、事实必须准确、要有护栏（文本先于语音，护栏可以在 TTS 之前生效）。
- **工具密集**的场景：工具调用以文本形式出现，结果可以直接念，不必让模型再生成一遍；需要再组织语言时只付文本 LLM 的首 token 时间。
- 要用**最强文本模型**的场景：智力可以换成任意文本 LLM，不受语音模型能力限制。
- 中文、方言、儿童语音等**需要专门 ASR** 的场景：ASR 可以单独挑、单独调、单独评测。
- 需要**完全掌握上下文、判停、历史长度、错误恢复**的场景：这些在级联里都是你自己的代码。

**不适合**

- 对**普通回合首音**和**自然度**要求极高的闲聊陪伴类产品。级联至少多一段 TTS 首包，ASR 还会把语气、情绪压平。
- 需要**副语言理解**（情绪、语气、年龄）且不愿另接分类器的场景。
- 部署资源紧张、不想运维三套模型或三家云服务的小团队。

---

## 3. 关键取舍

| 维度 | 级联的表现 | 说明 |
|---|---|---|
| 普通回合延迟 | 中 | 首音 ≈ 判停 + ASR 定稿残余 + LLM 首 token 与首个可合成片段 + TTS 首包 + 下行与播放启动。一份实践笔记的实测：按键模式、带决策层和正文投机，有内容首音 P50 1.4 s；同项目的 S2S 普通回合是 0.87–0.96 s |
| 工具回合延迟 | 中偏快 | 结果可以直接念；需要模型组织语言时只付文本 TTFT，历史是文本，前缀缓存稳定。同一项目里级联工具回合 1.4 s，而 S2S 工具回合 2.1 s |
| 可控性 | 高 | 文本在中间：可审核、可改写、可逐字念；判停、上下文、截断全在自己手里 |
| 自然度 | 中 | 取决于 TTS；ASR 丢掉了副语言，TTS 长文本容易平 |
| 成本 | 三段分别计费或部署 | 每段都可以挑便宜的；但要运维的组件最多 |
| 对上游的依赖 | 低 | 每段都有多家可替换，不会被单一厂商的会话语义卡住 |
| 错误传播 | 有 | ASR 听错，后面全错；LLM 只能拒绝，不能纠正（儿童语音、噪声下尤其明显） |

### 3.1 延迟为什么累加，以及怎么不累加

把首音拆成五段（细节见 [first-audio](../03-mechanisms/first-audio.md) 和 [latency-budget](../01-foundations/latency-budget.md)）：

| 段 | 串行做法的浪费 | 流式衔接的做法 |
|---|---|---|
| ① 判停 | 固定静音阈值等太久 | VAD 静音约 200 ms 后跑语义判停，未说完时自适应多等（LiveKit、Pipecat 都是这个组合） |
| ② ASR 定稿 | 判停后才开始识别整段 | 流式 ASR 边说边识别，判停时只剩尾巴；按键场景按下即建连 |
| ③ LLM 首 token + 首个可合成片段 | 等 ASR 定稿才发请求；等整句才送 TTS | **抢先生成**：判停未确认时用 partial 先跑 LLM，输出扣在闸门里，确认后放行（LiveKit preflight、Pipecat eager end-of-turn）。按键设备松键即定稿，抢先生成收益小，改为"识别定稿即起跑"（见 [first-audio](../03-mechanisms/first-audio.md)）；LLM 流式输出按句切，**首句尽量短** |
| ④ TTS 首包 | LLM 首 token 之后才建 TTS 连接 | TTS 建连 / 预热与 LLM 请求并行（unmute 的做法）；说话人特征启动时缓存 |
| ⑤ 下行与播放启动 | 一次性推送大块音频，设备深缓冲 | 服务端按实时节奏限速下发、客户端浅缓冲（小智前 5 包直发、之后按 60 ms 一包；unmute 只超前约 0.32 s） |

一份针对级联首音的推算是：不换模型、只改架构，首音可以从约 3.6 s 降到约 1.1–1.4 s（推算，未实测）。这个数字的意义在于说明**架构造成的开销和模型首包是同一个量级**。

两个容易踩的坑：

- **抢先生成不能抢跑工具**。工具有副作用，投机被丢弃就撤不回来。LiveKit 把工具执行放在授权之后，Pipecat 在投机中一旦出现工具调用就撤销整次投机。结果是抢先生成只加速普通回合。
- **中文分句不能按空格切**。按空白切词的实现（unmute 就是这样）对中文会把整段回复憋到流结束才送 TTS。首句要按中文标点、按字数强制切。

### 3.2 "每段可换"的真实含义

可换不只是"换个厂商"。因为接口是文本，每一段可以独立地：

- **单独评测**：ASR 用字错率，LLM 用冻结盲测集的动作命中率，TTS 用 MOS 和首包，各有各的尺子（见 [evaluation](../03-mechanisms/evaluation.md)）。
- **单独加规则**：ASR 之后可以接规则短路（噪音、"重新开始"之类确定性高的指令），LLM 之后可以接规则修正层和写操作门槛，TTS 之前可以接护栏。
- **按回合换模型**：小模型做意图判定，大模型写正文；一份实践笔记的级联策略层就是"最小档位模型只输出动作枚举，首 token 约 0.5 s，正文交给另一个模型并行生成"。
- **把"自己的话"和模型的话统一**：所有出声都走同一个 TTS，声音天然一致。

---

## 4. 级联的几个结构要点

### 4.1 上下文只记用户听到的

级联里助手的回复是模型生成的全文，但用户被打断时只听到了一部分。开源里的做法是**让 TTS 按播放时刻回吐词，只有播出去的词进上下文**：

- Pipecat：TTS 给每个词打 pts，输出 transport 按时钟释放，没到播放时刻的词进不了 assistant 聚合器。
- unmute：写进历史的是 TTS 按播放时刻回吐的词，LLM 原文只发给客户端显示。

反例是小智服务端：被打断时把已生成的全部文本写进历史，也没有打断标记。详见 [state-and-context](state-and-context.md) 和 [interruption](../03-mechanisms/interruption.md)。

### 4.2 工具回合

级联的工具回合在结构上比 S2S 快：结果出来后，要么由我们直接念（不经过模型），要么让文本 LLM 续写（只付 TTFT），不需要"再生成一段音频"。小智服务端把工具结果分成 `RESPONSE`（直接念，不再过 LLM）、`REQLLM`（结果写回历史，递归再生成）、`RECORD`、`NONE` 四种，就是这个思路的一种实现。工具执行期间如何出声见 [tool-calls](../03-mechanisms/tool-calls.md)。

### 4.3 打断

级联的打断完全由自己控制：取消 LLM 流、冲刷 TTS、清下行队列、截断上下文。难点不在"能不能打断"，而在**迟到的帧**：已取消的 LLM 或 TTS 仍可能推来输出。这需要代际编号或请求 id 过滤，见 [floor-control](floor-control.md)。

---

## 5. 代表项目

| 项目 | 级联的形态 | 依据 |
|---|---|---|
| [pipecat](../04-projects/frameworks/pipecat.md) | 帧驱动 pipeline：`transport.input → STT → user_aggregator → LLM → TTS → transport.output → assistant_aggregator`；README 列出数十家 STT / LLM / TTS 服务，同时也支持 S2S 服务 | `examples/function-calling/function-calling-openai-async.py`；README 服务表 |
| [livekit-agents](../04-projects/frameworks/livekit-agents.md) | `AgentSession(stt=..., llm=..., tts=...)` 的 STT-LLM-TTS 管线；同一框架也能把 `llm` 换成 `RealtimeModel` 走 S2S | README 示例；`voice/agent_session.py` |
| [xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md) | Silero VAD → ASR（默认本地 FunASR / SenseVoiceSmall）→ LLM（默认 glm-4-flash）→ TTS（默认 EdgeTTS）；首句遇逗号就切，后续句号切；按实时节奏下发 | `config.yaml` 的 `selected_module`；`core/providers/tts/base.py` |
| [unmute](../04-projects/full-duplex/unmute.md) | Kyutai 流式 STT + 任意 OpenAI 兼容文本 LLM + Kyutai 流式 TTS；逐词送 TTS，TTS 建连与 LLM 并行；STT 持续收音，能在说话时被打断 | README；`unmute/unmute_handler.py` |
| [ten-framework](../04-projects/frameworks/ten-framework.md) | 示例 `voice-assistant` 的图由 ASR、LLM、TTS 扩展组成（如 `deepgram_asr_python`、`openai_llm2_python`、`deepgram_tts`）；另有 `voice-assistant-realtime` 走 S2S | `ai_agents/agents/examples/voice-assistant/tenapp/property.json` |

unmute 在本手册里归在全双工目录下，因为它是"组装式全双工"的代表；从链路形态看它就是级联，见 [full-duplex](full-duplex.md)。

---

## 6. 涉及的机制

| 机制 | 级联里的关注点 |
|---|---|
| [turn-detection](../03-mechanisms/turn-detection.md) | 判停在自己手里：VAD + 语义判停 + 自适应等待 |
| [first-audio](../03-mechanisms/first-audio.md) | 抢先生成、首句切分、TTS 预热、填充语 |
| [interruption](../03-mechanisms/interruption.md) | 取消 LLM / TTS、按播放位置截断上下文 |
| [tool-calls](../03-mechanisms/tool-calls.md) | 结果直接念还是让模型续写 |
| [audio-preprocessing](../03-mechanisms/audio-preprocessing.md) | ASR 前的降噪、AEC（开放麦时） |
| [evaluation](../03-mechanisms/evaluation.md) | 分段计时，"有声音"和"有内容"两个首音分开记 |

基础概念见 [asr](../01-foundations/asr.md)、[tts](../01-foundations/tts.md)、[vad](../01-foundations/vad.md)。

---

## 7. 和其他形态的关系

- 级联和 [S2S](s2s.md) 的对比是"可控性 vs 自然度和普通回合速度"。一个反直觉的事实是：**S2S 普通回合更快，工具回合反而更慢**，所以工具密集的产品不一定该选 S2S。
- [半级联](half-cascade.md) 去掉了 ASR 这一跳，用音频理解模型直接出文本，其余（文本层、TTS、上下文）与级联相同。
- 混合架构可以按回合分流：寒暄走 S2S，事实和工具走级联或半级联，前提见 [half-cascade](half-cascade.md) 第 5 节。

---

## 待确认

- "全串行约 3.6 s → 优化后约 1.1–1.4 s"是推算，没有在同一条链路上实测过。
- 小智服务端双流式 TTS（火山 `huoshan_double_stream` 等）的切句与首包行为未细读。
- 抢先生成的命中率和浪费率在中文、儿童语音下的数据没有。

---

素材来源：一份实践笔记（未发布）§3.3、§6；原始长文 [级联架构的首音优化](essays/cascade-first-audio-optimization.md)；调研笔记 `notes/pipecat.md`（未发布）、`livekit-agents.md`、`xiaozhi.md`、`unmute.md`、`ten-and-deployment.md`。
