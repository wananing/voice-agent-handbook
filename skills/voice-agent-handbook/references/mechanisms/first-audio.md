# 首音优化（first audio）

手册：03-mechanisms/first-audio.md

## 问题

首音是"用户说完 → 听到第一个字"，分两种，必须分开算：**有声音**（任何声音，含填充语、提示音）和**有内容**（回答第一句）。填充语能把有声音提到 1 s 内，有内容一点没变。约 2 s 听不到声音用户就以为坏了、重复提问或抢话。实践笔记的产品约束（建议值）：有内容首句 ≤ 2.5 s、目标约 1 s；超过 2 s 无声即故障；首句到正文空档 ≤ 1.5 s。

实践笔记实测（从松键起算）：级联普通回合有内容 P50 1.4 s；S2S 普通回合 0.87–0.96 s、工具回合有内容 2.1–2.3 s；半级联无实测，建议验收线 P50 ≤ 1.1 s。判停等待不在本页（见 turn-detection）。

## 解法分类

三类思路：压缩某段等待、让后一段重叠提前跑、用别的声音占位。每个手段写明改善哪个首音。

**1. TTS 分句聚合（压缩攒首段，改善有内容）**
- 句末标点切：Pipecat 默认 `TextAggregationMode.SENTENCE`，`SimpleTextAggregator` 攒到句末（docstring 估计每句多 200–300 ms）。
- 首段特殊处理：小智服务端首句遇 `，、~：` 就切，之后只在句号类切（`first_sentence_punctuations`）。
- 最短首段（实践笔记建议值）：首段遇 `。！？；，、` 且累计 ≥ 6 字就切，12–15 字强制切；后续段 20–40 字按句号类切。推算省 0.3–0.7 s。
- 代价：首段太短伤韵律或合成失败（CosyVoice 分段短于参考文本一半告警），TN 被切碎（`12` 拆进两段）。
- 逐 token / 逐词送（Pipecat `TOKEN`、unmute `rechunk_to_words`）只对文本流入 TTS 有意义，对整句接口每次重 prefill；unmute 按空格切词对中文无效，整段憋到流结束。
- TTS 内置切句不能直接用：CosyVoice 首段可达 60–80 token。

**2. TTS 流式首包（改善有内容）**。数字为仓库自述、未复现：
- CosyVoice：Triton + TRT-LLM 首块 P50 218 ms，说话人缓存后 185 ms；bistream 不能配 vLLM、跳过 TN；官方 FastAPI / gRPC 示例没开流式。
- FireRedTTS2：80 ms 逐帧，"as low as 140ms"（L20）；每次重编码参考音频，要自缓存参考 token。
- VoxCPM：patch 级流式，要建 prompt cache、`optimize()` 并预热；无首包数字。
- Spark-TTS：本地整段出，不能实时；Triton 并发 1 首块 P50 210 ms。
- IndexTTS：只有 TRT 后端（仅 2.0）100 code 分块流式。fish-speech：本仓库名义流式、实际整段。
- 首块随并发恶化快（CosyVoice2 并发 4 时约 0.9–1.2 s）；共享实例状态有疑似 bug（CosyVoice `token_hop_len`）。
- S2S 首块由上游决定：Moshi 理论 160 ms、L4 约 200 ms；Step-Audio2 每 25 token（约 1 s 音频）一块。

**3. 抢先生成（重叠判停与 LLM 首 token，只改善普通回合有内容）**
- 判停未确认就用当前转写跑 LLM，输出扣在闸门里，确认后输入不变就放行，变了丢弃重跑。
- LiveKit：默认开、仅级联（实时模型路径直接 return）。转写（忽略大小写标点）、chat_ctx、tools、tool_choice 四项等价才采用；默认只抢 LLM，`preemptive_tts=True` 连 TTS；上限 `max_speech_duration=10 s`、`max_retries=3`。
- Pipecat：`EagerUserTurnStopStrategy` + `SpeculationGate`，依赖 STT 的 eager 转写（Deepgram Flux、Cartesia）。
- **遇工具调用撤销**（副作用撤不回）：LiveKit 工具在发言授权后才执行，Pipecat 投机中出现工具就撤销整次。
- 收益上限 min(触发点到判停确认间隔, LLM TTFT)，推算开放麦省 0.15–0.3 s；有了它 `min_delay` 可取保守些换更低截断率（推算）。
- 代价：浪费算力；ASR 二遍纠错降低命中率；闸门没接代际时被丢弃的输出漏播。
- 按键说话没有判停可抢，等价做法是"识别定稿即起跑"。

**4. 预热与并行建连（改善有内容）**
- 并行建连：unmute 先挂 TTS quest 再发 LLM 请求，拿到第一个词才 await。
- 连接复用：Pipecat WebSocket TTS 回合内复用 context id；不支持取消的 TTS 打断后重连，下一句多一次建连（推断）。
- 预热：LiveKit 进程池 `prewarm_fnc`；Moshi `warmup()` + CUDA graph；CosyVoice `add_zero_shot_spk` 缓存说话人。推算云服务省 0.1–0.3 s。代价：长连接占厂商并发配额。
- 下行：小智服务端前 5 包直发作预缓冲；60 ms Opus 帧比 20 ms 多约 40 ms。

**5. 填充语 / 提示音（占位，只改善有声音）**
- 本地预合成：会话开始同音色批量合成、裁首尾静音、按文本缓存，不进上下文。实践笔记实测有声音 2.1 s → 0.82–0.87 s，有内容仍 2.1 s。
- 框架 API：LiveKit `session.say(..., add_to_chat_ctx=...)`（上游 `supports_say` 时参数被忽略）；Pipecat `TTSSpeakFrame`、pipecat-flows `tts_say` 默认进 context。
- 空闲触发：LiveKit `RunContext.with_filler`，空闲满 `delay` 才播，短工具不触发。
- prompt 让模型先说：openai-realtime-agents，无兜底，模型跳过就干等。
- 只驱动 UI：qwen-audio-agent `voice.state=processing`，无屏设备不成立。
- 措辞"我 + 动作 + 对象"（"我查一下路线。"），不用空泛的"我想想"，每工具一组随机轮换。
- 代价：音色不一致听成"两个人"；长提示语推迟正文；进上下文会被模型模仿。

**6. 首句到正文的空档**
- S2S 工具回合（实践笔记实测，松键起算）：0.37 s 识别完成 → 0.8 s function call（提示语开播，有声音约 0.87 s）→ 再生成约 1.2 s → 正文（有内容约 2.1 s）。
- 结果立即产出、只在播放时排在提示语后（实测排队 P50 0、P90 0.2 s，双声 0）。
- 短 cue ≤ 300 ms，工具在 cue 开播前已完成就不播。
- 同回合"提示语 → 正文"复用 TTS 连接；正文首句同样适用 1、2、4 的手段。
- 再生成的结构性空档只能靠 tool-calls 解决（跳过再生成、委派后台流式）。

## 推荐

**先定指标**：两个首音分开打点、分开报 P50 / P90，注明起点（松键、判停、最后有声帧）。只报一个首音数字会让提示语掩盖有内容慢。

**级联（含自托管中文 TTS），按顺序做：**
1. 自己切分，不用 TTS 内置切句：首段 ≥ 6 字遇 `。！？；，、` 切、12–15 字强制切；后续 20–40 字。TN 和 Markdown 清理放在切分前。
2. 选真正首块即出的流式 TTS 路径，在自己的并发下实测首块（裁首部静音再计时）。P50 ≤ 0.4 s 是否可达需实测。
3. 说话人特征启动时缓存，模型常驻预热；云 TTS 长连接，建连与 LLM 请求并行（照 unmute）。
4. 开放麦再开抢先生成，只抢 LLM，命中率稳定后再考虑抢 TTS；遇工具一律撤销。按键说话改成识别定稿即起跑。
5. 填充语只给工具回合和慢决策回合，用本地预合成短 cue（≤ 300 ms），工具在 cue 开播前完成就不播。

**半级联**：照搬级联 1–3、5。理解端是整段音频请求-响应形态（Step-Audio2、Qwen3-Omni、Ultravox）时没有抢先生成的开源实现，不建议先做。

**S2S**：普通回合不在首音上花工程，上游已最快。工程放在工具回合：function call 到达那一刻本地播预合成提示语（不进模型、不进上下文），结果排在提示语后。不把"prompt 让模型先说一句"当唯一手段。有内容靠 tool-calls 的跳过再生成和委派。

**无屏设备**：沉默就是故障，预合成提示语第一优先级。**有屏**：先用 UI 状态顶住有声音，语音填充语只在工具回合用。

参数起点（建议值，在自己 trace 上扫）：抢先生成命中率 ≥ 70% 才保留、浪费率按 ≤ 30% 监控；短 cue ≤ 300 ms；连接复用窗口 10–60 s；下行起播门槛 1 帧、Opus 20 ms 帧。

不要做：用填充语掩盖判停等待（判停期间出声就是抢话，还会被 VAD 当人声）；中文按空格逐词送；对整句接口逐字送；提示语和在线合成用不同模型版本或说话人缓存；结果等提示语播完才去拿。

## 各项目怎么做

- livekit-agents：抢先生成默认开（仅级联，四项等价，工具授权后才执行）；`with_filler` 空闲填充。`voice/agent_activity.py:AgentActivity.on_preemptive_generation`、`voice/turn.py:PreemptiveGenerationOptions`、`voice/filler_scheduler.py`。
- pipecat：默认按句聚合，可切 TOKEN；eager 转写 + 闸门，遇工具撤销；回合内复用 TTS context。`utils/text/simple_text_aggregator.py:SimpleTextAggregator`、`turns/speculation_gate.py:SpeculationGate`、`services/tts_service.py:TextAggregationMode`。
- unmute：TTS 建连与 LLM 首 token 并行，逐词送文本流入 TTS（中文无效）。`_generate_response_task`、`rechunk_to_words`。
- xiaozhi-esp32-server：首句遇逗号顿号即切；唤醒词回复走缓存音频；前 5 包直发。`core/providers/tts/base.py`（`first_sentence_punctuations`）、`core/connection.py:ConnectionHandler.chat`、`core/handle/sendAudioHandle.py:18`。
- openai-realtime-agents：prompt 要求委派前先说填充语，无代码兜底，README 自述之后约 2 s 才有内容。`agentConfigs/chatSupervisor/index.ts`。
- qwen-audio-agent：委派回合刻意不填充，只驱动 UI。`RealtimePresentationRuntime.markFunctionCall`、仓库根 `config/frontend-agent/PROMPT.md`。
- pipecat-flows：`pre_actions: tts_say` 推理前先说固定句（默认进 context）；边函数切节点多一轮推理。`actions.py:ActionManager._handle_tts_action`。
- cosyvoice：音频流式 + 文本流式输入，`add_zero_shot_spk` 说话人缓存；首段 60–80 token 需自己切。

## 相关

- 架构：02-architectures/cascade.md、02-architectures/half-cascade.md、02-architectures/s2s.md（4.4 节模型侧手段）、02-architectures/full-duplex.md、02-architectures/floor-control.md（提示语作为 `cue` 的仲裁与冲刷）
- 基础：01-foundations/latency-budget.md（1.1 节两种首音定义、第 2 节完整分解）、01-foundations/tts.md（1.1–1.3 节）、01-foundations/transport.md
- 机制：03-mechanisms/tool-calls.md、03-mechanisms/turn-detection.md、03-mechanisms/interruption.md、03-mechanisms/evaluation.md
