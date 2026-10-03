# 判停（turn detection）

手册：03-mechanisms/turn-detection.md

## 问题

判停决定"用户这一轮说完了没有，agent 该不该接话"这个时刻。判早了抢答、一句话被切成两轮；判晚了每轮白等，等待直接加在首音上。VAD 是帧级信号，判停是轮级决策，VAD 多半只是判停的触发点。判停之后还有一段转写定稿等待，常被漏算。

难点人群：儿童（句中停顿多、拖音、重复、声音小、话没说完就松键）和句中停顿多的成人。对他们"静音多久算说完"没有好值。所有调研仓库都没有儿童 / 口吃语料，语义判停的中文准确率也没有公开数字。

## 解法分类

链路：VAD 报"结束说话" → （可选）语义模型给 P(说完) → P 高等 min_delay、P 低等 max_delay（期间用户再开口则作废）→ 回合结束 → 等转写定稿 → 交给 LLM。

**1. VAD 静音判停。** VAD 报结束后再等固定静音时长。ASR 自带端点（FunASR / SenseVoice 的 FSMN-VAD、sherpa-onnx 解码端点）本质相同。适用：原型、演示。代价：每轮固定多等 end 静音（各实现 200 ms 到约 1.85 s）；短则截断句中停顿，长则处处慢，只能二选一。仓库默认值：FunASR C++ 服务尾部静音 800 ms；sherpa-onnx 出字后 1.2 s；小智服务端 `min_silence_duration_ms: 200`（代码缺省 1000）。

**2. 语义判停。** VAD 先报短静音，再由模型给"说完的概率"。
- 只看音频：smart-turn v3（Pipecat 默认，Whisper-tiny 编码器，int8 约 8 MB，取最后 8 s，P > 0.5 判完成，README 自述 CPU 推理 10–100 ms）；LiveKit `inference.TurnDetector`（v1 云端 / v1-mini 本地，只看最后 1.2 s，`zh` 阈值 0.355）。不依赖 ASR。代价：训练数据是成人，中文、儿童效果需实测；拖音、重复可能被判"未完成"导致多等。
- 只看文本：TEN Turn Detection（ASR final → LLM 出 1 token：`finished / unfinished / wait`）；Pipecat LLM 判停标记。代价：要先等 ASR final，ASR 错字带偏判断；TEN 需 GPU vLLM 服务，出错按 `unfinished` 处理，等到 5 s 兜底；`wait` 直接丢掉这一轮。
- STT 停顿头：unmute 用 Kyutai STT 的 `prs[2]`，EMA 后 > 0.6 判停。代价：绑定特定 STT，换 ASR 就没了。

"未说完"后的处理：LiveKit 从 `min_delay` 换成 `max_delay`；Pipecat 继续收音、再开口整轮重判、3 s 兜底；TEN 启动 5 s 强制提交。

**3. 服务端判停。** S2S 上游自己判（OpenAI `server_vad` / `semantic_vad`、Qwen `smart_turn`）。三种姿态：关掉（`turn_detection=null` 后自己 `commit` + `response.create`；Gemini Live 用 `activity_start / end`）；半关（`create_response=False`，服务端切段但不自动回复）；关不掉（只能调参，如 Nova Sonic `endpointing_sensitivity`，或在上游前做停顿压缩）。代价：零实现但参数受限、多数不能运行中切换；关不掉时句中停顿被切两段、松键前就调工具、偶尔只识别出一个字（实践笔记实测）。

**4. 按键 / 手动判停。** 松键、点击、`commit` 即回合结束，没有判停等待。VAD 退化为辅助：短按过滤、停顿压缩（按键期间静音超过 K 的帧不转发）、补充窗口（松键过早时等再按，合并成同一轮，见 turn-model 的 `holding`）。代价：需要按键或 UI；松键太早截尾；误触。

**5. 模型内部判停。** moshi 每 80 ms 出 token，填充 token 即不说话，唯一旋钮 `pad_mult`。延迟最低（自述理论 160 ms，L4 约 200 ms），但没有事件可挂工具、审核、日志。

**横切：定稿等待。** 判停后要等 STT final 或冲刷 STT 尾巴。Pipecat 等 finalized 转写或 STT P99（`ttfs_p99_latency`）到期；LiveKit 无 final 不判，手动 commit 灌静音冲刷后最多等 `transcript_timeout=2.0` s，超时用 interim；unmute 延迟约 0.48 s（`asr_delay_in_tokens = 6`），判停后灌零帧冲刷；实践笔记实测 S2S 提交到定稿约 0.37 s，设 3 s 识别超时。

默认参数速查（仓库默认值）：

| 组件 | 触发 | 语义阈值 | 未说完上限 | 无语义模型时 |
|---|---|---|---|---|
| Pipecat + smart-turn | VAD `stop_secs=0.2` | P > 0.5 | `SmartTurnParams.stop_secs=3` s | `SpeechTimeoutUserTurnStopStrategy` |
| LiveKit | VAD 结束 | `zh: 0.355` | `max_delay=3.0` s（流式 2.5） | `min_delay=0.5` s（流式 0.3） |
| TEN Turn Detection | ASR final | 1 token | `force_threshold_ms=5000` | ASR final |
| unmute | 每帧 STT | > 0.6 | — | — |
| openai-realtime-agents | 服务端 | `threshold 0.9` | — | `silence_duration_ms 500` |

注意 Pipecat 两个 `stop_secs`：VAD 的 0.2 s 是何时跑模型，smart-turn 的 3 s 是未说完最多等多久。

## 推荐

先按边界来源分：按键和开放麦是同一回合状态机的两种配置，判停只在开放麦下是真问题。

| 场景 | 选 | 理由 |
|---|---|---|
| 儿童、户外、无 AEC 硬件 | **按键判停**，VAD 只做停顿压缩、短按过滤、补充窗口 | 语义模型只决定"多等几百毫秒"，判错最坏是多等，不吞话 |
| 开放麦、成人、级联或半级联 | **VAD 短静音 0.2–0.3 s + 音频语义判停 + min/max 等待**，起点 LiveKit 0.5 / 3.0 s；有条件开动态端点（`DynamicEndpointing`） | 音频模型不依赖 ASR、CPU 能跑；min/max 把误判代价限在可控区间 |
| 开放麦、S2S 上游 | 能关（或半关）服务端判停就关，自己判停后 commit；关不掉就用上游 `semantic_vad` 类并在上游前加停顿压缩 | 自己判停才能和打断、工具回合、回合 id 共用状态；关不掉时只"判"不"改音频"治不了切段 |
| 原型 / 演示 | ASR 自带端点（FunASR 800 ms、sherpa 1.2 s）或纯 VAD 静音 | 够用，别在上面调体验 |
| 附和、重叠研究演示 | moshi | 产品不用：没有判停事件就没有工具、审核、trace |

不随场景变的规则：
- 中文儿童场景不用文本判停（ASR 错误率高，且要 GPU）。
- 把定稿等待算进预算：等 final 设超时、超时用 interim 兜底，或主动冲刷 STT；S2S 收到提交确认且已有识别结果就立即定稿。
- 不用绝对音量门槛（Pipecat VAD `min_volume=0.6` 可能把小声孩子判成非语音，需实测），或先做 AGC；静音时长按采样数算，不用墙钟。
- 上线前建自己的儿童 / 停顿样本集，扫 VAD PR 曲线、画 P(完成) 分布再定阈值。所有默认值都只是起点。

按键 + 服务端判停关不掉（最易做错）：网关上 VAD（TEN VAD 或 Silero，16 ms 一跳）做停顿压缩，恢复说话时补发最近约 200 ms 防吞字头；松键时短按丢弃 → smart-turn ≥ τ 立即 commit → 否则等 W，再按合并、超时 commit。起点（实践笔记建议值，未在儿童集实测）：

| 参数 | 起点 | 说明 |
|---|---|---|
| 停顿压缩 K | 250 ms | 必须明显小于上游判停静音；上游 500 ms 时 K ≤ 300 ms。上游实际值需实测 |
| 短按丢弃 | 按键 < 250 ms 或语音 < 150 ms | 刚问过用户问题时降到 100 ms，否则吞掉"好的""不去" |
| τ | 0.5 | smart-turn 默认，看 trace 分布再调 |
| 补充窗口 W | 600 ms，上限 1000 ms | 只对 P < τ 的轮次生效 |

进 trace：按键期间上游切段次数、被压缩静音总时长、短按丢弃次数及丢弃后 5 s 内再按比例、P 值和实际等待、补充窗口命中率。

语义模型选型：中文开放麦先用 smart-turn v3.2（8 MB、CPU、BSD、可用自有数据微调）；LiveKit v1-mini 作对照（闭源、常驻约 108 MB、只看 1.2 s）；TEN Turn Detection 只在 ASR 可靠且有 GPU 时考虑。

## 各项目怎么做

- livekit-agents：VAD → turn detector EOU 概率 → 低于语言阈值时 `min_delay` 换 `max_delay`；`mode="dynamic"` 学习抬高 `min_delay`；有 STT 时等 final。`voice/audio_recognition.py:AudioRecognition._run_eou_detection`、`voice/endpointing.py:DynamicEndpointing`、`inference/eot/detector.py:TurnDetector`。S2S 关服务端判停：`voice/agent_activity.py:AgentActivity._resolve_rt_turn_detection_enabled`（会话开始决定一次；Gemini、AWS、Ultravox 关不掉）。
- pipecat：默认 `TurnAnalyzerUserTurnStopStrategy` + 本地 smart-turn v3.2，VAD 停 → 模型判 → 等 finalized 转写或 STT P99。`turns/user_turn_strategies.py:UserTurnStrategies`、`audio/turn/smart_turn/local_smart_turn_v3.py:LocalSmartTurnAnalyzerV3`；S2S 本地判停后 commit：`services/openai/realtime/llm.py:OpenAIRealtimeLLMService._handle_user_stopped_speaking`。
- ten-framework：默认直接用 ASR final；可选 TEN Turn Detection，`unfinished` 后 5 s 强制提交。`main_control._on_asr_result`、`ext/ten_turn_detection/`（`TurnDetector.eval`、`_eval_force_chat`）。
- openai-realtime-agents：服务端 `server_vad`（0.9 / 300 / 500 ms）；按键模式 `turn_detection` 置 null，松开 commit + `response.create`。`App.tsx:updateSession`、`App.tsx:handleTalkButtonUp`。
- xiaozhi-esp32-server：Silero 双阈值 0.5 / 0.3 + 5 帧滑窗 + 200 ms 静音（墙钟计时）；流式 ASR 以厂商端点为准。`vad/silero.py:VADProvider.is_vad`、`asr/doubao_stream.py`。
- unmute：`prs[2]` EMA > 0.6 判停，灌零帧冲刷尾巴。`unmute/unmute_handler.py:UnmuteHandler.determine_pause`。
- funasr：FSMN-VAD 尾部静音 800 ms；Python 侧动态 VAD。`runtime/onnxruntime/src/e2e-vad.h`、`fsmn_vad_streaming/dynamic_vad.py`（2pass 下一回合可能被切成多个 `2pass-offline` 段）。
- moshi：填充 token 即不说话，`pad_mult` 偏置。`lm.py:LMGen._step`。

## 相关

- 架构：02-architectures/turn-model.md（边界来源、`holding`）、02-architectures/full-duplex.md、02-architectures/s2s.md（服务端判停关不掉）、02-architectures/floor-control.md（判停中算用户在说）
- 基础：01-foundations/vad.md、01-foundations/asr.md、01-foundations/latency-budget.md
- 机制：03-mechanisms/interruption.md、03-mechanisms/first-audio.md（抢先生成）、03-mechanisms/audio-preprocessing.md、03-mechanisms/evaluation.md
