# 首音优化：填充语、投机生成、分句播放

> 状态：草稿
> 最后更新：2026-10-02

## 问题是什么

首音是"用户说完 → 听到第一个字"。它有两种，必须分开算（定义见 [latency-budget](../01-foundations/latency-budget.md) 1.1 节）：

- **有声音**：任何声音都算，包括"我查一下"这类填充语、提示音。
- **有内容**：回答本身的第一句。

两者混在一起会自欺：填充语能把"有声音"提前到 1 s 以内，"有内容"却一点没变。本页的手段都要写明自己改善的是哪一个。

不处理会怎样：用户 2 s 左右听不到声音，就会以为设备坏了、重复提问或者开始抢话。一份实践笔记把产品约束定为：有内容首句 ≤ 2.5 s、目标约 1 s；超过 2 s 无声即视为故障；首句到正文的空档 ≤ 1.5 s。

三条链路的首音预算不同，本页手段能动的段也不同。下表只列本页相关的部分，完整分解见 [latency-budget](../01-foundations/latency-budget.md) 第 2 节：

| 链路 | 关键路径上本页能动的段 | 一份实践笔记实测（从松键起算） | 本页最有用的手段 |
|---|---|---|---|
| [级联](../02-architectures/cascade.md) | LLM 首 token、攒首段、TTS 首包、TTS 建连 | 普通回合有内容 P50 1.4 s | 分句、流式首包、抢先生成、并行建连 |
| [半级联](../02-architectures/half-cascade.md) | 攒首段、TTS 首包、TTS 建连（理解端首 token 不受本页手段影响） | 无实测；建议验收线 P50 ≤ 1.1 s | 分句、流式首包、并行建连 |
| [S2S](../02-architectures/s2s.md) | 普通回合几乎没有（音频由上游直出）；工具回合的"有声音" | 普通回合 0.87–0.96 s；工具回合有内容 2.1–2.3 s | 本地预合成提示语；"有内容"要靠 [tool-calls](tool-calls.md) |

判停等待（开放麦下可能是最大的一段）不在本页，见 [turn-detection](turn-detection.md)；前缀缓存、关思考等模型侧手段见 [latency-budget](../01-foundations/latency-budget.md) 和 [s2s](../02-architectures/s2s.md) 4.4 节。

## 解法分类

按思路分三类：**压缩**某一段的等待、让后一段**重叠**提前开跑、用别的声音**占位**。

### 1. TTS 分句聚合（压缩"攒首段"）

LLM 流式出 token，大多数 TTS 按句合成，中间要有一个切分器。切分规则决定首包前要等多少文本。

- **句末标点切**：Pipecat 默认 `TextAggregationMode.SENTENCE`，`SimpleTextAggregator` 攒到句末才送；标点集 `utils/string.py:SENTENCE_ENDING_PUNCTUATION` 含 `。？！`。遇到句末标点后要等到下一个非空白字符才确认断句（英文 `.` 有歧义，中文全角标点属于 `UNAMBIGUOUS_SENTENCE_ENDING_PUNCTUATION`）。docstring 估计每句多 200–300 ms。
- **首段特殊处理**：小智服务端首句碰到 `，`、`、`、`~`、`：` 等就切，之后只在句号类标点切（`core/providers/tts/base.py` 的 `first_sentence_punctuations`）。TEN 的 `main_control` 按标点切句（`helper.py:parse_sentences`），没有首段特例。
- **最短首段**：太短会伤韵律、甚至合成失败（CosyVoice 在分段短于参考文本一半时告警）。一份实践笔记的起点是：首段遇到 `。！？；，、` 且累计 ≥ 6 字就切，12–15 字还没遇到标点就强制切；后续段 20–40 字、按句号类标点切。见 [tts](../01-foundations/tts.md) 1.3 节。
- **逐 token / 逐词送**：Pipecat `TextAggregationMode.TOKEN`；unmute 按空白切成整词逐词送（`rechunk_to_words`）。**只对支持文本流入的 TTS 有意义**；对整句接口逐字送，每次都要重新 prefill。unmute 按空格切词对中文无效，整段回复会憋到流结束。
- **TTS 自带切句不能直接用**：CosyVoice 内置切段面向离线质量，首段可能长达 60–80 token，agent 里要自己按短语切。

### 2. TTS 流式首包（压缩"TTS 首包"）

"支持流式"要拆成文本侧和音频侧来看（三种形态见 [tts](../01-foundations/tts.md) 1.1 节）。各开源 TTS 的实际能力差异很大：

| TTS | 流式能力 | 首块数字（仓库自述，未复现） | 要注意的 |
|---|---|---|---|
| [CosyVoice](../04-projects/tts/cosyvoice.md) | 音频流式输出 + 文本流式输入（bistream）两条路径 | Triton + TRT-LLM 首块 P50 218 ms，开说话人缓存后 185 ms | bistream 不能配 vLLM、跳过 TN；官方 FastAPI / gRPC 示例没开流式 |
| [FireRedTTS2](../04-projects/tts/fireredtts2.md) | 逐帧（80 ms）流式，首块只需 prefill 加两三帧 | README "as low as 140ms"（L20） | 每次调用都重编码参考和历史音频，要自己缓存参考 token |
| [VoxCPM](../04-projects/tts/voxcpm.md) | 每步一个 patch 的细粒度流式 | 无 | 要启动时建 prompt cache、开 `optimize()` 并预热 |
| [Spark-TTS](../04-projects/tts/spark-tts.md) | 本地整段出；Triton 流式，首块 1.0 s 音频、之后块长指数放大 | Triton 并发 1 首块 P50 210 ms | 本地路径不能用于实时 |
| [IndexTTS](../04-projects/tts/index-tts.md) | PyTorch 路径无句内流式；TRT 后端 100 code 分块流式，只支持 2.0 | 无 | PyTorch 路径只能靠上游把首句切短 |
| [fish-speech](../04-projects/tts/fish-speech.md) | 本仓库按 batch 出，无 speaker 标签时首包等于整段合成时间 | 外部 SGLang-Omni 自述 TTFA ~100 ms（H200），待确认 | 名义流式、实际整段 |
| Kyutai TTS（经 [unmute](../04-projects/full-duplex/unmute.md)） | 文本流入、音频流出 | 整条链路 TTS 延迟单卡同机约 750 ms、分卡约 450 ms，口径待确认 | 依赖逐词送 |

结构性因素：首块需要多少语音 token、参考音频是否每次重新 prefill、并发数。首包随并发恶化很快（CosyVoice2 并发 4 时首块 P50 约 0.9–1.2 s），见 [tts](../01-foundations/tts.md) 1.2 节。

S2S 上游的音频首块由上游决定。开源 S2S 模型里：[Moshi](../04-projects/full-duplex/moshi.md) 结构性解决（80 ms 帧、理论 160 ms、L4 实测约 200 ms）；[Step-Audio2](../04-projects/e2e-models/step-audio2.md) 每 25 个音频 token（约 1 s 音频）合成一块；[Qwen3-Omni](../04-projects/e2e-models/qwen3-omni.md) 开源推理路径都是非流式 `generate`，Talker 的流式出音没有用法，待确认。

### 3. 抢先生成（重叠"判停"与"LLM 首 token"）

判停还没确认时就用当前转写启动 LLM，输出扣在闸门里；确认后输入没变就放行，变了就丢弃重跑。

- **LiveKit preemptive generation**（默认开启，仅级联）：STT 给出 preflight 转写，或 final 转写到达时判停还没完成，进 `AgentActivity.on_preemptive_generation`，调 `_generate_reply(..., schedule_speech=False)`。判停后四项都等价才采用：转写（忽略大小写和标点）、`on_user_turn_completed` 之后的 chat_ctx、tools、tool_choice。默认只抢跑 LLM，`preemptive_tts=True` 连 TTS 也抢跑。上限 `max_speech_duration=10 s`、`max_retries=3`。实时模型路径直接 return。
- **Pipecat**：`EagerUserTurnStopStrategy` + `SpeculationGate`，依赖 STT 自带回合预测给 eager 转写（Deepgram Flux、Cartesia）。聚合器用 eager 转写构造临时 context（`LLMUserAggregator._run_speculative_inference`），不改真 context；确认且转写匹配（`NormalizedMatch` / `ExactMatch`）放行。
- **遇到工具时撤销**：工具有副作用，投机被丢弃就撤不回来。LiveKit 把工具执行放在发言被授权之后（`_pipeline_reply_task_impl` 注释 "start to execute tools (only after play())"）；Pipecat 在投机中一旦出现工具调用就撤销整次投机（`LLMService.run_function_calls`）。结果是**抢先生成只加速普通回合**。
- 丢弃条件（LiveKit）：四项等价任一不满足；出现更新的转写（新抢跑替换旧的）；用户说话超过 10 s；单轮重试超过 3 次；打断。新版 turn detector 在静音满 200 ms 时就预取一次预测，所以抢跑的起点可以早于判停确认几百 ms。
- 收益上限是 min(触发点到判停确认的间隔, LLM TTFT)。判停等待越长、TTFT 越长，省得越多；有了抢先生成，`min_delay` 可以取得保守一些，用来换更低的截断率（推算）。
- 按键说话没有判停可抢，等价做法是"识别定稿即起跑"，不等后面的决策层。
- 其他项目：TEN 没有；FunASR 的 partial 能让上层提前预取 LLM，但仓库不提供这一层。

### 4. TTS 连接预热与并行建连（把建连移出关键路径）

- **并行建连**：unmute 先挂 TTS quest，紧接着发 LLM 请求，拿到第一个词时才 `await quest.get()`（`_generate_response_task`）。
- **连接复用**：Pipecat 的 WebSocket TTS 在同一 LLM 回合内复用 audio context id（`reuse_context_id_within_turn=True`），句子作为同一 context 的增量发送。不支持取消的 TTS 打断后要重连（`InterruptibleTTSService`），下一句首音会多一次建连（推断）。
- **进程与模型预热**：LiveKit 预热进程池加 `prewarm_fnc`；Moshi 启动时 `warmup()` 并用 CUDA graph；VoxCPM 要预热并复用 prompt cache；CosyVoice `add_zero_shot_spk` 缓存说话人特征。TEN 每会话冷启动，没有预热。
- **下行侧**：小智服务端前 5 包直发作预缓冲（`core/handle/sendAudioHandle.py:18`），限速器不推迟首包；小智设备侧浅缓冲（解码队列 ≤ 1.2 s、播放 2 帧）。60 ms Opus 帧比 20 ms 多约 40 ms 分帧延迟。传输细节见 [transport](../01-foundations/transport.md)。

### 5. 填充语 / 提示音（占位，只改善"有声音"）

| 做法 | 谁生成 | 进不进上下文 | 例子 |
|---|---|---|---|
| 本地预合成 | 会话开始时用同一音色批量合成、按文本缓存 | 不进 | 一份实践笔记的"旁路提示语"；小智唤醒词回复走缓存音频 |
| 框架 API 播报 | 本地 TTS 实时合成或传入预合成帧 | 可选 | LiveKit `session.say(text, audio=..., add_to_chat_ctx=...)`；Pipecat `TTSSpeakFrame`（`append_to_context` 默认 True）；pipecat-flows `pre_actions: tts_say` |
| 空闲触发 | 框架调度 | 按 `say()` 的设置 | LiveKit `RunContext.with_filler(source, delay, interval, max_steps)`：会话连续空闲满 `delay` 秒才播（`voice/filler_scheduler.py:_FillerScheduler`） |
| prompt 让模型先说一句 | 模型生成 | 进（是普通 assistant 消息） | openai-realtime-agents 的 Chat-Supervisor："Sample Filler Phrases"，代码没有兜底，模型跳过就是干等 |
| 只驱动 UI | 不出声 | 不进 | qwen-audio-agent 发 `voice.state=processing` 驱动动画；前台 prompt 还明确要求"不要用话语填补等待" |

提示语进不进上下文是个取舍。进上下文，模型知道自己"说过"，不会再说一遍；但它是给人听的状态提示，不是模型输出，进了上下文会被模型当成自己的话模仿。Pipecat `TTSSpeakFrame` 和 pipecat-flows `tts_say` 默认进；LiveKit `say()` 可用 `add_to_chat_ctx=False` 关掉（但上游声明 `supports_say` 时这个参数被忽略）；一份实践笔记的做法是不进模型、不进上下文，类比有界面 agent 调工具时显示的"正在搜索……"。

提示语库的实现要点（同一份笔记）：会话开始时用同一音色批量预合成，裁掉首尾静音，按文本缓存；计时只取有声段。

一份实践笔记实测本地预合成的效果：在模型发出 function call 那一刻播，"有声音"从 2.1 s 提前到 0.82–0.87 s，"有内容"仍是 2.1 s。措辞上用"我 + 动作 + 对象"（"我查一下路线。"），不用空泛的"我想想"；每个工具一组措辞随机轮换。

**填充语只用在"已判停、在等工具或慢决策"的阶段**。判停等待期间出声就是抢话，还可能被自己的 VAD 或上游当成人声。

### 6. 首句到正文的空档

占位解决了开头，但"填充语播完 → 正文开口"之间又可能出现一段静默。openai-realtime-agents 的 README 自述这段约 2 s（后台非流式请求 + 前台再生成一轮）。造成空档的原因和对策：

以 S2S 工具回合为例（数字来自一份实践笔记实测，从松键起算；结构与 openai-realtime-agents 的 Chat-Supervisor 相同）：

```
松键 ─0.37 s─ 识别完成 ─0.8 s─ function call ─┬─ 本地提示语开播（有声音 ≈ 0.87 s）
                                              ├─ 执行工具（+1–50 ms）→ 回传
                                              └─ 模型再生成 ……约 1.2 s…… 正文开播（有内容 ≈ 2.1 s）
                        提示语 "我查一下路线。" 约 1 s ┘          ▲ 空档 = 正文开播 − 提示语结束
```

提示语越短、正文越晚，空档越大；提示语越长，正文又可能被推迟。两头都要压。

- **结果等提示语播完才去拿**：提示语时长整段串进有内容延迟。对策是结果立即产出、立即就绪，只在播放时排在提示语后面。一份实践笔记实测结果排在提示语后几乎不等待（P50 0、P90 0.2 s），双声 0。注意 S2S 路径上框架常把"回传工具结果"串在前导语音之后，见 [tool-calls](tool-calls.md)。
- **提示语太长**：长 cue 反而推迟正文。一份实践笔记的设计把短 cue 控制在 ≤ 300 ms，工具在 cue 开播前已完成就不播。LiveKit `with_filler` 的 `delay` 也是同一思路：短工具不触发填充语。
- **正文首句本身慢**：正文要再走一次"首段切分 → TTS 首包"，前面 1、2、4 节的手段对正文同样适用；同一回合跨"提示语 → 正文"复用同一条 TTS 连接，避免多一次建连。
- **正文要先经过模型再生成**：这是 S2S 工具回合的结构性空档（约 1.2 s），只能在 [tool-calls](tool-calls.md) 里解决（跳过再生成、委派后台流式）。

## 各解法的代价

| 手段 | 改善哪个首音 | 收益量级 | 复杂度 | 对上游的依赖 | 失败模式 |
|---|---|---|---|---|---|
| 首段短切 | 有内容 | 从"等整句"到"等 6 字左右"，推算省 0.3–0.7 s | 低 | TTS 能接受短句 | 首段太短：韵律断、质量下降或合成失败；TN 被切碎（`12` 拆进两段） |
| 逐 token / 逐词送 | 有内容 | 视 TTS 而定 | 低 | **必须**是文本流入 TTS | 对整句接口每次重 prefill；中文按空格切词会整段憋住 |
| 流式首包 | 有内容 | 从整句合成时长到首块时长 | 中（要选型、自己部署推理后端） | 自托管：推理后端（Triton / TRT-LLM）；云端：厂商协议 | 名义流式实际整段（fish-speech）；并发上去首块恶化；共享实例状态（CosyVoice `token_hop_len` 疑似 bug，见 [tts](../01-foundations/tts.md) 第 3 节） |
| 抢先生成 | 有内容（普通回合） | 推算开放麦省 0.15–0.3 s，TTFT 越长省得越多 | 中高：闸门、等价判定、和代际仲裁接线 | STT 要给稳定 preflight / eager 转写；文本 LLM 在关键路径上 | 被丢弃的 LLM / TTS 调用浪费算力；ASR 二遍纠错改动转写时命中率下降；闸门没接代际时被丢弃的输出漏播 |
| 并行建连 / 预热 | 有内容 | 推算云服务省 0.1–0.3 s；说话人缓存 218→185 ms（CosyVoice 自述） | 低 | 无 | 长连接空闲占厂商并发配额；预热请求本身可能触发实例状态 bug |
| 本地预合成提示语 | 有声音 | 一份实践笔记：2.1 s → 0.87 s | 中：缓存、裁静音、音色版本管理、仲裁 | 需要自己的出声通道（S2S 下要能本地播） | 音色和模型声音不一致听成"两个人"；长提示语推迟正文；没进上下文时模型不知道自己"说过" |
| prompt 让模型先说一句 | 有声音 | 与普通回合首音相当 | 低 | 模型要听话 | 模型跳过就干等；填充语进上下文；和 function call 同一 response 时正文还要等再生成 |
| 只驱动 UI | 有声音（视觉） | — | 低 | 有屏幕 | 无屏设备不成立 |

各手段在三条链路上的适用性：

| 手段 | 级联 | 半级联 | S2S |
|---|---|---|---|
| 首段短切 | 适用 | 适用 | 不适用（上游直出音频）；只用于本地合成的"自己的话" |
| 流式首包 | 适用 | 适用 | 由上游决定 |
| 抢先生成 | 适用（LiveKit、Pipecat 都只在这里做） | 理解端是整段音频进的请求-响应形态时没有现成实现 | LiveKit 直接 return；不适用 |
| 并行建连 / 预热 | 适用 | 适用 | 上游会话本身要提前建好；本地 TTS（播"自己的话"）同样要预热 |
| 本地预合成提示语 | 工具 / 慢决策回合 | 工具 / 慢决策回合 | 工具回合的主要手段；要求能在本地插入音频 |
| prompt 让模型先说一句 | 可用但不如本地预合成 | 同左 | openai-realtime-agents 的做法；无兜底 |

一个共同的风险：**只报一个"首音"数字**。提示语会把"有声音"做得很好看，掩盖"有内容"慢。评测时两个指标分开报，并注明起点（松键、判停还是最后一个有声帧），见 [evaluation](evaluation.md)。

## 各项目怎么做

| 项目 | 做法 | 代码路径 | 备注 |
|---|---|---|---|
| [livekit-agents](../04-projects/frameworks/livekit-agents.md) | 抢先生成默认开（仅级联，四项等价才采用，工具授权后才执行）；TTS 流式输入；预热进程池；`with_filler` 空闲触发填充语 | `voice/agent_activity.py:AgentActivity.on_preemptive_generation`、`voice/turn.py:PreemptiveGenerationOptions`、`voice/filler_scheduler.py` | 实时模型路径不抢跑；首音实测数字仓库没有，待确认 |
| [pipecat](../04-projects/frameworks/pipecat.md) | 默认按句聚合（含中文句末标点），可切 TOKEN；eager 转写 + `SpeculationGate` 抢先生成，遇工具撤销；WebSocket TTS 回合内复用 context | `utils/text/simple_text_aggregator.py:SimpleTextAggregator`、`turns/speculation_gate.py:SpeculationGate`、`services/tts_service.py:TextAggregationMode` | 有 `user_bot_latency_observer` 拆段，但仓库没有参考数字 |
| [pipecat-flows](../04-projects/frameworks/pipecat-flows.md) | 节点 `pre_actions: tts_say` 在推理前先说固定句；但经边函数切节点要两次推理，首音多一轮 | `actions.py:ActionManager._handle_tts_action`、`manager.py:FlowManager._create_transition_func` | `tts_say` 默认进 context |
| [ten-framework](../04-projects/frameworks/ten-framework.md) | LLM 流式，按标点切句后逐句 `tts_text_input`；无抢先生成、无预热 | `main_control` 的 `helper.py:parse_sentences` | 跨扩展 JSON 编解码开销无数字，待确认 |
| [openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md) | prompt 要求前台委派前先说一句中性填充语 | `agentConfigs/chatSupervisor/index.ts`（Sample Filler Phrases） | 无代码兜底；README 自述填充语后约 2 s 才有内容 |
| [qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md) | 普通回合交给上游；委派回合刻意不填充，只发 `voice.state=processing` 驱动 UI | `RealtimePresentationRuntime.markFunctionCall`；仓库根 `config/frontend-agent/PROMPT.md` | 客户端有无本地提示音待确认 |
| [unmute](../04-projects/full-duplex/unmute.md) | TTS 建连与 LLM 首 token 并行；逐词送文本流入 TTS | `_generate_response_task`、`rechunk_to_words` | 按空格切词对中文无效 |
| [moshi](../04-projects/full-duplex/moshi.md) | 结构性解决：80 ms 帧，每步都可能出声；`warmup()` + CUDA graph | `LMGen`（`graphed_main` / `graphed_depth`） | README 理论 160 ms、L4 约 200 ms |
| [xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md) | 首句遇逗号顿号即切；双流式 TTS 直接转发 LLM 增量；`direct_answer` 参数增量解析送 TTS；唤醒词回复走缓存音频；ASR 与声纹并行；前 5 包直发 | `core/providers/tts/base.py`（`first_sentence_punctuations`）、`core/connection.py:ConnectionHandler.chat`、`core/handle/sendAudioHandle.py:18` | 非流式 provider 句与句串行合成 |
| [xiaozhi-esp32](../04-projects/devices/xiaozhi-esp32.md) | 浅缓冲（解码队列 ≤ 1.2 s、播放 2 帧）；唤醒前 2 s 音频预上传 | `main/audio/audio_service.cc:AudioService`；`Application::HandleWakeWordDetectedEvent` | 60 ms Opus 帧多约 40 ms；首音大头在服务端 |
| [step-audio2](../04-projects/e2e-models/step-audio2.md) | vLLM SSE 逐块返回音频 token，`token2wav.stream` 每 25 token 合成一块；只出文本时可跳过音频生成 | `examples-vllm-stream.py:stream_client` | 无 TTFT 数字，待确认 |
| [qwen3-omni](../04-projects/e2e-models/qwen3-omni.md) | 只出文本时 `return_audio=False`；Talker 流式出音无用法 | `cookbooks/` | 无 TTFT 数字，待确认 |
| [ultravox](../04-projects/e2e-models/ultravox.md) | 省掉 ASR 定稿；音频约 6.25 token/s，预填充开销小；文本流式接 TTS | `infer.py` | 无延迟数字，待确认 |
| [cosyvoice](../04-projects/tts/cosyvoice.md) | 音频流式 + 文本流式输入；说话人缓存 | `README.Cosyvoice2.Unet.md`；`add_zero_shot_spk` | 内置切段首段 60–80 token |
| [fireredtts2](../04-projects/tts/fireredtts2.md) | 80 ms 逐帧流式 | `FireRedTTS2_Stream.generate` | 需自缓存参考 token |
| [voxcpm](../04-projects/tts/voxcpm.md) | patch 级流式 | `src/voxcpm/model/voxcpm2.py:VoxCPM2Model._inference` | 无首包数字 |
| [spark-tts](../04-projects/tts/spark-tts.md) | Triton 流式，首块 1.0 s 音频 | `runtime/triton_trtllm/model_repo/spark_tts/1/model.py` | 本地整段出 |
| [index-tts](../04-projects/tts/index-tts.md) | TRT 后端 100 code 分块流式 | `backends/trt/pipeline/streaming.py` | 只支持 2.0，无首包数字 |
| [fish-speech](../04-projects/tts/fish-speech.md) | 按 batch 出 | — | 低首包靠外部 SGLang-Omni，待确认 |
| [smart-turn](../04-projects/turn-vad/smart-turn.md) | 间接：让判停在 200 ms 静音后完成；S2S 下 Pipecat 用 `wait_for_transcript=False` 把转写移出关键路径 | `turn_analyzer_user_turn_stop_strategy.py` | 推理 10–100 ms 进关键路径 |
| [ten-vad](../04-projects/turn-vad/ten-vad.md) | 间接：句尾判定越早，后续越早开始 | — | 只有示意图，无数字 |
| [funasr](../04-projects/asr/funasr.md) | 间接：partial 可让上层预取 LLM | — | 仓库不提供 |
| [aiortc](../04-projects/audio-processing/aiortc.md) | 间接：UDP + 20 ms 帧，无额外缓冲 | `mediastreams.py` | — |
| [rnnoise](../04-projects/audio-processing/rnnoise.md) | 反向：上行约多 20 ms 算法延迟（推断） | — | 加上 Pipecat 包装的重采样和攒帧 |

[fireredasr](../04-projects/asr/fireredasr.md)、[sensevoice](../04-projects/asr/sensevoice.md)、[sherpa-onnx](../04-projects/asr/sherpa-onnx.md)、[webrtc-audio-processing](../04-projects/audio-processing/webrtc-audio-processing.md) 不涉及。

## 我们的判断

**先定指标再动手**：两个首音分开打点、分开报 P50 / P90，注明起点。不分开就无法判断下面任何一条是否生效。

**级联（含自托管中文 TTS）：按这个顺序做。**

1. 自己切分，不用 TTS 内置切句：首段遇 `。！？；，、` 且 ≥ 6 字就切，12–15 字强制切；后续 20–40 字。TN 和 Markdown 清理放在切分之前。
2. 选真正"首块即出"的流式 TTS 路径，并在自己的并发下实测首块（裁掉首部静音再计时）。目标 P50 ≤ 0.4 s 能否达到待确认。
3. 说话人特征启动时缓存，模型常驻并预热；云 TTS 用长连接，建连和 LLM 请求并行（照 unmute 的写法）。
4. 开放麦再开抢先生成，**只抢 LLM**，命中率稳定后再考虑抢 TTS；遇到工具调用一律撤销。按键说话不需要抢先生成，改成"识别定稿即起跑"。
5. 填充语只给工具回合和慢决策回合，用本地预合成短 cue（≤ 300 ms），工具在 cue 开播前已完成就不播。

**半级联**：照搬级联的第 1–3、5 条。理解端是"整段音频进"的请求-响应形态（Step-Audio2、Qwen3-Omni、Ultravox），开源项目里没有对它做抢先生成的实现，不建议自己先做。

**S2S**：普通回合不要在首音上花工程，上游已经最快。工程放在工具回合：在 function call 到达那一刻本地播预合成提示语（不进模型、不进上下文），结果排在提示语后面。不要把"prompt 让模型先说一句"当成唯一手段，它没有兜底，模型跳过就干等。"有内容"要靠 [tool-calls](tool-calls.md) 里的"跳过再生成"和委派。

**无屏设备**：沉默就是故障，"有声音"最要紧，预合成提示语是第一优先级。**有屏**的产品可以先用 UI 状态顶住"有声音"（qwen-audio-agent 的做法），语音填充语只在工具回合使用。

**参数起点**（都是建议值，要在自己的 trace 上扫）：首段 ≥ 6 字 / 12–15 字强制切；抢先生成命中率 ≥ 70% 才保留、浪费率先按 ≤ 30% 监控；短 cue ≤ 300 ms；连接复用窗口 10–60 s；下行起播门槛 1 帧、Opus 20 ms 帧。

**不要做的**：用填充语掩盖判停等待；中文照搬按空格逐词送；对整句接口逐字送；提示语和在线合成用不同模型版本或说话人缓存；结果等提示语播完才去拿。

**仍待确认**：

- 开源 S2S / 理解端（Step-Audio2、Qwen3-Omni、Ultravox）的 TTFT 和端到端首音，仓库都没有数字。
- 自托管中文 TTS 在单并发下首块 P50 ≤ 0.4 s 是否可达；IndexTTS、VoxCPM 没有首包数字。
- LiveKit、Pipecat 的抢先生成在中文和按键场景下的命中率与收益，两个仓库都没有实测数字。
- unmute "TTS 延迟" 450 / 750 ms 的口径。

## 相关

- 架构层：[级联](../02-architectures/cascade.md)、[半级联](../02-architectures/half-cascade.md)、[S2S](../02-architectures/s2s.md)、[全双工](../02-architectures/full-duplex.md)、[话筒归属](../02-architectures/floor-control.md)（提示语作为 `cue` 的仲裁与冲刷）
- 基础：[latency-budget](../01-foundations/latency-budget.md)、[tts](../01-foundations/tts.md)、[transport](../01-foundations/transport.md)
- 其他机制：[工具回合](tool-calls.md)、[判停](turn-detection.md)、[打断与截断](interruption.md)、[评测](evaluation.md)
- 项目页：[livekit-agents](../04-projects/frameworks/livekit-agents.md)、[pipecat](../04-projects/frameworks/pipecat.md)、[unmute](../04-projects/full-duplex/unmute.md)、[xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md)、[cosyvoice](../04-projects/tts/cosyvoice.md)、[openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md)
