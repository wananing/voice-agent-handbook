# 判停：VAD、语义判停、服务端判停

> 状态：草稿
> 最后更新：2026-10-02

## 问题是什么

判停（turn detection / end-of-turn）回答的是"用户这一轮说完了没有，agent 现在该不该接话"。它决定的是一个时刻：判早了，用户话说一半就被抢答，或者一句话被切成两轮、只识别出一个字；判晚了，每一轮都白白多等，等的时间直接加在首音上。

它和 VAD 不是一回事。VAD 回答"这一帧有没有人声"，是帧级的声学信号；判停是轮级的决策，输入可以是 VAD 事件、音频韵律、ASR 文本、对话上下文、按键。最简单的判停是"VAD 静音满 N ms 就算说完"，所以两者常被混为一谈，但多数框架里 VAD 只是判停的**触发点**：VAD 先报静音，语义模型再决定"马上回"还是"再等等"。VAD 本身的原理和端点参数见 [vad](../01-foundations/vad.md)，本页不重复。

判停也不是终点。判停之后，流式 ASR 往往还有几百毫秒的尾巴没吐出来，要等转写**定稿**才能把完整文本交给 LLM。这一层经常被忽略，却是判停延迟里稳定的一段。

难的人群有两类：

- **儿童**。一份实践笔记的场景里大量是 4–10 岁的孩子：句中停顿多、拖音、自我重复（"然后……然后……"）、声音小、识别错误率高、话没说完就松键。
- **句中停顿多的成人**：想词、口吃、用语气词撑场（"嗯……那个"）。

对这两类人，"静音多久算说完"没有一个好值：设短会频繁截断，设长又处处多等。所有调研过的仓库都**没有**儿童、口吃语料的处理或评测，语义判停模型的中文准确率也都没有公开数字。各方案具体在哪里出问题，见下文"儿童和停顿多的用户"。

## 解法分类

一条开放麦判停链路通常长这样，五类解法分别替换其中某一段：

```
 用户音频 ─► VAD（帧级）─► "结束说话"事件
                              │
                              ▼
                      语义判停（可选）：P(说完了)
                       ├─ P 高 → 等 min_delay
                       └─ P 低 → 等 max_delay（期间用户再开口，本次作废）
                              │
                              ▼
                       回合结束信号 ─► 等转写定稿（或冲刷 STT）─► 交给 LLM
```

按"谁来判"分五类：

### 1. VAD 静音判停

VAD 报"结束说话"后再等一个固定静音时长就算说完。ASR 自带的声学端点（FunASR / SenseVoice 的 FSMN-VAD、sherpa-onnx 的解码端点）本质相同，只是 VAD 和 ASR 打包在一起。

### 2. 语义判停

VAD 先报一段短静音，再问一个模型"说完的概率"，按概率决定立即提交还是继续等。按输入又分三种：

- **只看音频**：smart-turn v3（Pipecat 默认，Whisper-tiny 编码器 + 分类头，int8 版约 8 MB，取最后 8 s 音频）；LiveKit 新版 `inference.TurnDetector`（v1 云端、v1-mini 本地，只吃最后 1.2 s 音频）。学的是韵律和句尾词：以语气词、连接词、拖音结尾的算"未说完"。
- **只看文本**：TEN Turn Detection（ASR final 文本去标点 → LLM 只生成 1 个 token：`finished / unfinished / wait`）；LiveKit 旧插件（最近 6 轮对话、最多 128 token，已废弃）；Pipecat 的 LLM 判停标记（让主 LLM 在回复开头输出 ●/◐/○，未完成就不回复）。
- **STT 附带的停顿头**：unmute 用 Kyutai STT 的停顿预测 `prs[2]`，不需要单独的 VAD 和判停模型。

"未说完"之后怎么办，各家不同：LiveKit 把等待从 `min_delay` 换成 `max_delay`；Pipecat 继续收音，用户再开口就整轮重判，一直不开口则 3 s 兜底；TEN 启动 5 s 强制提交计时，`wait` 则直接丢掉这一轮。

### 3. 服务端判停

S2S 上游自己做（OpenAI `server_vad` / `semantic_vad`、Qwen `smart_turn`、Gemini 等），客户端只送音频。框架面对它只有三种姿态：

- **关掉**，自己判停后 `commit` + `response.create`（OpenAI 类 `turn_detection=null`；Gemini Live 的 `activity_start / activity_end` 手动划窗）。
- **半关**：服务端照样切段，但不自动回复（OpenAI 类 `create_response=False`），回复由客户端在回合提交时请求一次。
- **关不掉**：只能调参（Nova Sonic 的 `endpointing_sensitivity`），或在上游前做停顿压缩，让服务端看不到够长的静音。

### 4. 按键 / 手动判停

松键、点击、`commit` 就是回合结束。VAD 退化成辅助工具：

- **短按过滤**：按键时长和累计语音时长都很短就不提交。
- **停顿压缩**：按键期间静音超过 K ms 的帧不再转发，防止上游服务端判停切段。
- **补充窗口**：松键太早时留一小段时间等用户再按，再按就合并进同一轮（[turn-model](../02-architectures/turn-model.md) 的 `holding` 状态）。

### 5. 模型内部判停

原生全双工模型（moshi）每 80 ms 在文本流上采样一个 token，填充 token 就是"这一步不说话"。系统里没有"判停"事件，"说完了没有"是模型自己的事。

### 横切：定稿等待

几乎所有链路在判停之后都还要加一层：等 STT 的 final，或者主动冲刷 STT 的延迟尾巴。它不改变"什么时候算说完"，但决定"什么时候能开始生成"。

## 各解法的代价

| 解法 | 延迟 | 复杂度 / 依赖 | 主要失败模式 |
|---|---|---|---|
| VAD 静音判停 | 每轮固定多等 end 静音时长（各实现从 200 ms 到约 1.85 s 不等） | 最低，一个 VAD 加计时器 | 静音短则截断句中停顿，静音长则处处慢；只能在两者间来回调 |
| 语义判停（音频） | VAD 短静音 + 一次推理（smart-turn README 自述 CPU 上 10–100 ms）；判"未完成"时多等到兜底上限 | 一个小模型；不依赖 ASR | 训练数据是成人，中文、儿童效果未知；拖音、重复可能被判"未完成"，导致频繁多等 |
| 语义判停（文本） | 要先等 ASR final 再推理；TEN 的模型需要 GPU 服务（示例部署在 A10），客户端超时 5 s | 依赖 ASR 质量和一个 LLM 服务 | ASR 错字带偏判断；模型服务出错时 TEN 默认按 `unfinished` 处理，等到 5 s 兜底 |
| STT 停顿头（unmute） | 判停 + 冲刷约 0.5 s 延迟尾巴 | 绑定特定 STT 模型，换 ASR 这套判停就没了 | `prs[2]` 是否真是语义级判停，仓库未说明，待确认 |
| 服务端判停 | 上游内部决定，通常最快 | 零实现成本，但参数受限、多数不能运行中切换 | 关不掉时：句中停顿被切成两段、松键前就调工具、偶尔只识别出一个字（一份实践笔记实测） |
| 按键 / 手动 | 从松键算，没有判停等待 | 需要设备有按键或 UI | 松键太早截掉尾巴；误触短按；用户要学会按 |
| 模型内部判停 | 最低（moshi 自述理论 160 ms，L4 上约 200 ms） | 没有可控面，唯一旋钮是采样偏置 `pad_mult` | 没有事件可挂工具、审核、日志；与按键模型冲突 |

### 定稿等待的代价

这一段各家处理不同，但都真实存在：

- **Pipecat**：smart-turn 判完成后，还要等 finalized 转写，或者从实际停说时刻起算的 STT P99 时延到期。所以 STT 的 `ttfs_p99_latency` 直接进入判停延迟，代码会在 VAD `stop_secs` 不是 0.2 时打警告，提示重跑 stt-benchmark。S2S 模式下聚合器把 `wait_for_transcript` 置 False，这层不在关键路径上。
- **LiveKit**：有 STT 时，VAD 报静音但还没有 final 转写，`_run_eou_detection` 直接返回；final 到达时再以 `trigger="stt"` 重新判。手动 `commit_user_turn` 会往 STT 灌静音帧冲刷缓冲，再最多等 `transcript_timeout=2.0` s，超时就把 interim 当 final 用（`voice/audio_recognition.py:AudioRecognition._commit_user_turn`）。
- **unmute**：STT 是延迟流模型（`asr_delay_in_tokens = 6`，约 0.48 s），判停时最后约 0.5 s 的话还没转写，判停后往 STT 灌零帧冲刷完才开始生成。
- **TEN、小智流式 ASR**：直接以 ASR 的 final / `definite` 为回合结束，定稿和判停是同一个事件，端点完全由 ASR 厂商决定。
- **一份实践笔记实测**：S2S 上游从提交到识别定稿约 0.37 s。它的做法是"收到提交完成且已有识别结果时立即定稿，不再等宽限期"，并设 3 s 识别超时，到期按 `asr_timeout` 失败收口。

抢先生成（转写未定稿就先跑 LLM，判停后转写不变就复用）是把这段等待藏起来的办法，见 [first-audio](first-audio.md)。

### 儿童和停顿多的用户

这类用户放大了每一类方案的失败模式：

| 方案 | 为什么难 |
|---|---|
| 静音判停 | 句中停顿常常超过 end 阈值。小智服务端的配置注释只是建议"说话停顿长的用户调大 `min_silence_duration_ms`" |
| 语义判停（音频） | 模型学的是成人韵律。smart-turn 按句中 / 句末语气词分别出指标，说明专门学过这类情况，但中文、儿童没有数字；数据规范要求少背景噪音，户外效果未知 |
| 语义判停（文本） | 孩子的识别错误率高，错字直接进判停输入 |
| 带音量门槛的 VAD | Pipecat VAD 要求置信度和音量同时过阈值（`min_volume=0.6`），小声、离麦远的孩子可能被判成非语音（待确认，需样本验证） |
| 服务端判停 | 关不掉时，孩子的长停顿正好被切段，松键前就触发工具调用 |
| 按键 | 话没说完就松键，或松键后马上再按一次补充 |

唯一现成的自适应机制是 LiveKit 的 `DynamicEndpointing`：用指数滑动平均学习同一用户两段话之间的停顿，以及"刚判完停就被用户打断"时的停顿，把 `min_delay` 往上抬，上限是 `max_delay`。

## 各项目怎么做

| 项目 | 做法 | 代码路径 | 备注 |
|---|---|---|---|
| [livekit-agents](../04-projects/frameworks/livekit-agents.md) | 三层：VAD → turn detector 给 EOU 概率 → 端点等待。概率低于该语言阈值时，等待从 `min_delay` 换成 `max_delay`；`mode="dynamic"` 按用户停顿学习抬高 `min_delay`。有 STT 时等 final 转写才判 | `voice/audio_recognition.py:AudioRecognition._run_eou_detection`、`voice/turn.py:EndpointingOptions`、`voice/endpointing.py:DynamicEndpointing`、`inference/eot/detector.py:TurnDetector` | 新检测器只用最后 1.2 s 音频，`zh` 阈值 0.355；中文准确率待确认。`turn_detection="manual"` 配 `commit_user_turn()` 做按键 |
| （同上，实时模型） | 上游声明 `can_disable_turn_detection` 且用户配了本地判停时，建会话传 `turn_detection_disabled=True`；OpenAI 可"半关闭"（`create_response=False`，服务端切段但不自动回复） | `voice/agent_activity.py:AgentActivity._resolve_rt_turn_detection_enabled`、`llm/_realtime/openai.py:_server_turn_taking_enabled` | 会话开始时决定一次，运行中不能切；Gemini、AWS、Ultravox 关不掉，本地检测器被忽略 |
| [pipecat](../04-projects/frameworks/pipecat.md) | user 聚合器按策略列表判停，默认 stop 是 `TurnAnalyzerUserTurnStopStrategy` + 本地 smart-turn v3.2：VAD 停 → 模型判 → 等 finalized 转写或 STT P99 超时 | `turns/user_turn_strategies.py:UserTurnStrategies`、`turns/user_stop/turn_analyzer_user_turn_stop_strategy.py`、`audio/turn/smart_turn/local_smart_turn_v3.py:LocalSmartTurnAnalyzerV3` | 可换成纯静音（`SpeechTimeoutUserTurnStopStrategy`）、LLM 标记（`FilterIncompleteUserTurnStrategies`）、服务端判停（`ExternalUserTurnStrategies`） |
| （同上，S2S） | OpenAI Realtime `turn_detection=False` 时本地判停后 `input_audio_buffer.commit` + `response.create`；Gemini Live 可 `GeminiVADParams(disabled=True)` 用 `activity_start / end` 手动划窗 | `services/openai/realtime/llm.py:OpenAIRealtimeLLMService._handle_user_stopped_speaking` | Nova Sonic 只能调 `endpointing_sensitivity`，关不掉 |
| [pipecat-flows](../04-projects/frameworks/pipecat-flows.md) | 不涉及，沿用 pipecat；只用 `respond_immediately=False` 让节点等用户先说 | `examples/restaurant_reservation.py:create_initial_node` | |
| [ten-framework](../04-projects/frameworks/ten-framework.md) | 默认示例直接用 ASR 的 final，端点由 ASR 厂商决定；可选 TEN Turn Detection 接在 ASR 之后，出 `finished / unfinished / wait`，`unfinished` 后 5 s 强制提交 | `main_control._on_asr_result`、`ext/ten_turn_detection/`（`TurnDetector.eval`、`_eval_force_chat`、`_process_new_turn`） | 模型不在仓库里，需要 vLLM 服务；`wait` 会丢掉这一轮不回复。ten-vad 示例里 VAD 只用来打断 |
| [openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md) | 完全用服务端 `server_vad`（`threshold 0.9`、`prefix_padding_ms 300`、`silence_duration_ms 500`）；按键模式把 `turn_detection` 置 null，松开时 commit + `response.create` | `App.tsx:updateSession`、`App.tsx:handleTalkButtonUp` | 没有语义判停 |
| [qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md) | 完全交给上游；按模型目录写 `session.turn_detection`：Qwen Audio 用 `smart_turn`，Qwen Omni 用 `semantic_vad`，部分模型 `server_vad` | 仓库根 `shared/realtime-model-catalog.mjs`、Provider `buildSession` | 这几个类型在服务端的含义待确认；仓库没有本地 VAD |
| [xiaozhi-esp32](../04-projects/devices/xiaozhi-esp32.md) | 端侧不判停。manual 由松键发 `listen stop`；auto / realtime 交给服务端 | `Application::HandleStopListeningEvent` | 端侧 VADNet 只用于 LED |
| [xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md) | 纯静音判停：Silero 双阈值 0.5 / 0.3 + 5 帧滑窗 + 默认 200 ms 静音；非流式 ASR 要求至少约 0.9 s 音频；流式 ASR 以厂商端点（豆包 `definite`）为准 | `vad/silero.py:VADProvider.is_vad`、`asr/doubao_stream.py` | 静音时长用墙钟算；唤醒后 2 s 忽略 VAD；没有语义判停 |
| [unmute](../04-projects/full-duplex/unmute.md) | STT 停顿预测头 `prs[2]` 经 EMA 平滑，超过 0.6 判停；判停后灌零帧冲刷约 0.5 s 的延迟尾巴 | `unmute/unmute_handler.py:UnmuteHandler.determine_pause`、`UnmuteHandler.receive` | 没有独立 VAD，协议里没有 `commit` |
| [moshi](../04-projects/full-duplex/moshi.md) | 没有外部判停。每 80 ms 文本流出填充 token 就是"不说话"；只能用 `pad_mult` 偏置沉默倾向 | `lm.py:LMGen._step`、`lm_generate_multistream.rs:State` | Web 会话可通过 `SessionConfigReq` 传 `pad_mult` |
| [smart-turn](../04-projects/turn-vad/smart-turn.md) | 只看音频的二分类：VAD 短静音后对整轮音频（最后 8 s）推理一次，P > 0.5 判完成 | `inference.py`、`train.py` | 自称支持中文，中文准确率仓库里没有；`inference.py` 仍指向 v3.1，Pipecat 内置 v3.2 |
| [ten-vad](../04-projects/turn-vad/ten-vad.md) | 只给帧级语音概率，不含状态机 | `src/aed.cc` | 自述句尾检测比 Silero 快几百 ms；状态机见 TEN 的 `ten_vad_python` 扩展和 sherpa-onnx |
| [funasr](../04-projects/asr/funasr.md) | 内置 FSMN-VAD 声学端点，C++ 服务默认尾部静音 800 ms，段结束触发离线定稿；Python 侧有按已说时长调阈值的动态 VAD | `runtime/onnxruntime/src/e2e-vad.h`、`fsmn_vad_streaming/dynamic_vad.py` | 2pass 下一个用户回合可能被切成多个 `2pass-offline` 段，上层要自己拼 |
| [sensevoice](../04-projects/asr/sensevoice.md) | `sensevoice-server` 内置 FSMN-VAD 端点，尾部静音按累计时长分档，开头一档约 1850 ms（推断）；`turn_detection.type:"none"` 可改为客户端 `commit` | `sensevoice-server.cpp:VadStream` | |
| [sherpa-onnx](../04-projects/asr/sherpa-onnx.md) | 两套：流式识别器的解码端点（出字后尾部静音 1.2 s），和独立声学 VAD（Silero / TEN VAD） | `endpoint.cc:Endpoint::IsEndpoint`、`voice-activity-detector.h:VoiceActivityDetector` | 都不是语义判停 |
| [fireredasr](../04-projects/asr/fireredasr.md) | 不涉及，需要上游先切好整句 | — | FireRedASR2S 带 FireRedVAD，未分析，待确认 |
| [ultravox](../04-projects/e2e-models/ultravox.md) | 模型不判停；demo 用 `gradio_webrtc` 的 `ReplyOnPause` 停顿后整段提交 | `ultravox/tools/gradio_voice.py:make_demo` | 半级联的典型分工：判停在模型外，可接任何方案 |
| [qwen3-omni](../04-projects/e2e-models/qwen3-omni.md) / [step-audio2](../04-projects/e2e-models/step-audio2.md) | 不涉及，开源仓库一次请求处理一段完整音频，demo 是手动 Submit | `web_demo.py`、`web_demo_vllm.py` | 云端 Realtime 的判停不在仓库里 |
| [webrtc-audio-processing](../04-projects/audio-processing/webrtc-audio-processing.md) / [rnnoise](../04-projects/audio-processing/rnnoise.md) / [aiortc](../04-projects/audio-processing/aiortc.md) | 不涉及。APM 内部 VAD 不经公共配置暴露；RNNoise 的语音概率理论上能当 VAD，但没有状态机 | `audio_processing.h`、`rnnoise_process_frame` | |
| TTS 各项目 | 不涉及 | — | [cosyvoice](../04-projects/tts/cosyvoice.md) 等 6 个 TTS 项目页均标"不涉及" |

**判停层的默认参数**（VAD 自身的 start / end / 滞回参数见 [vad](../01-foundations/vad.md) §1.3）：

| 框架 / 组件 | 触发点 | 语义阈值 | "未说完"时的等待上限 | 无语义模型时 |
|---|---|---|---|---|
| Pipecat + smart-turn | VAD `stop_secs=0.2` | P > 0.5（写死在模型侧） | `SmartTurnParams.stop_secs=3` s 静音强制判完 | `SpeechTimeoutUserTurnStopStrategy` |
| LiveKit | VAD 结束说话；新检测器静音 ≥200 ms 时预取 | 各语言 `unlikely_threshold`，`zh: 0.355` | `max_delay=3.0` s（流式检测器 2.5 s） | `min_delay=0.5` s（流式 0.3 s） |
| TEN Turn Detection | 每个 ASR final | 1 token 分类 | `force_threshold_ms=5000` | 直接用 ASR final |
| unmute | 每帧 STT 输出 | 停顿概率 > 0.6 | 不适用 | — |
| OpenAI Realtime（openai-realtime-agents 的配置） | 服务端 | `threshold 0.9` | 不适用 | `silence_duration_ms 500` |
| 小智服务端 | Silero | 无 | 不适用 | `min_silence_duration_ms: 200`（代码缺省 1000） |

注意 Pipecat 里有两个 `stop_secs`：VAD 的 0.2 s 是"何时跑模型"，smart-turn 的 3 s 是"未说完最多等多久"。

## 我们的判断

**先按边界来源分，再选判停方案。** 按键和开放麦是同一个回合状态机的两种配置（见 [turn-model](../02-architectures/turn-model.md)），判停只在开放麦下是真问题。

| 场景 | 选 | 理由 |
|---|---|---|
| 儿童、户外、无 AEC 的硬件 | **按键判停**。VAD 只做三件事：停顿压缩（按键期间静音超过 K≈250 ms 的帧不转发，K 要明显小于上游服务端判停的静音时长）、短按过滤（按键 < 250 ms 或语音 < 150 ms 丢弃）、补充窗口（松键时跑一次 smart-turn，P < τ 就留 600 ms、上限 1 s 等再按，再按合并成同一轮） | 语义模型在中文儿童上的效果未知，只让它决定"多等几百毫秒"，判错的最坏结果是多等，不会吞话。参数是一份实践笔记的起点值，未在儿童集上实测 |
| 开放麦、成人、级联或半级联 | **VAD 短静音（0.2–0.3 s）+ 音频语义判停 + min/max 等待**，起点 LiveKit 的 0.5 / 3.0 s；有条件就开动态端点 | 音频模型不依赖 ASR、CPU 能跑；min/max 结构把"误判"的代价限制在可控区间 |
| 开放麦、S2S 上游 | 上游能关服务端判停（或能"只切段不自动回复"）就关，自己判停后 commit；关不掉就用上游的 `semantic_vad` 类判停，并在上游前加停顿压缩 | 自己判停才能和打断、工具回合、回合 id 共用一套状态；关不掉时只"判"不"改音频"治不了切段 |
| 原型 / 演示 | ASR 自带端点（FunASR 800 ms、sherpa 1.2 s）或纯 VAD 静音 | 够用，但别在上面调体验 |
| 追求附和、重叠的研究演示 | 原生全双工（moshi） | 产品里不用：没有判停事件就没有工具、审核、回合 trace |

几条不随场景变的规则：

- **不要用文本判停做中文儿童场景**。孩子的 ASR 错误率高，TEN Turn Detection 这类文本模型的输入本身就不可靠，而且要 GPU 服务。
- **把定稿等待算进预算**。判停之后要么等 final（设超时，超时用 interim 兜底，LiveKit 的做法），要么主动冲刷 STT（unmute、LiveKit 手动 commit 的做法）；S2S 下收到提交确认且已有识别结果就立即定稿。
- **不用绝对音量门槛**，或者先做 AGC；静音时长按音频采样数算，不用墙钟。
- **上线前建自己的儿童 / 停顿样本集**，在上面扫 VAD 的 PR 曲线、画语义模型 P(完成) 的分布再定阈值。目前所有仓库都没有这类数据，任何默认值都只是起点。

**按键 + 服务端判停关不掉的组合**是最容易做错的一种，单独展开：

```
设备按下 ─► 上行音频 ─► 网关
                         ├─ VAD（TEN VAD 或 Silero，16 ms 一跳）
                         │    ├─ 静音累计 > K 后的静音帧不转发（停顿压缩）
                         │    └─ 恢复说话时补发最近 ~200 ms，防吞字头
                         ├─ 转发上游（实时流式）
                         └─ 松键：
                              ├─ 按键 < 250 ms 或语音 < 150 ms → 丢弃，不 commit
                              ├─ smart-turn(整段音频) ≥ τ → 立即 commit
                              └─ < τ → 等 W，再按就合并；超时 commit
```

| 参数 | 起点 | 说明 |
|---|---|---|
| 停顿压缩 K | 250 ms | 必须明显小于上游服务端判停的静音时长；上游是 OpenAI 类默认 500 ms 时 K 不超过 300 ms。上游实际值待实测 |
| 短按丢弃 | 按键 < 250 ms 或语音 < 150 ms | "好""嗯"约 200–350 ms；刚问过用户问题时降到 100 ms，否则会吞掉"好的""不去" |
| τ | 0.5 | smart-turn 默认值，看 trace 分布再调 |
| 补充窗口 W | 600 ms，上限 1000 ms | 只对 P < τ 的轮次生效 |

以上都是一份实践笔记的推荐起点，都还没在儿童样本上测过。上线后进每轮 trace 的指标：按键期间上游切段次数、被压缩的静音总时长、短按丢弃次数（以及丢弃后 5 s 内再按的比例，用来估计误杀）、语义模型的 P 和实际等待时长、补充窗口命中率。

**语义模型怎么选**：开放麦中文场景先用 smart-turn v3.2（只看音频、8 MB、CPU、BSD 许可、训练脚本和数据集开放，可以用自己的数据微调）；LiveKit v1-mini 有 `zh` 阈值但模型闭源、常驻约 108 MB、只看最后 1.2 s，作为对照；TEN Turn Detection 只在 ASR 质量可靠、有 GPU 时考虑。

## 相关

- 架构层：[turn-model](../02-architectures/turn-model.md)（边界来源、`holding` 补充窗口、一次输入被切成多段）、[full-duplex](../02-architectures/full-duplex.md)（三种双工与 moshi 的模型内判停）、[s2s](../02-architectures/s2s.md)（服务端判停关不掉）、[floor-control](../02-architectures/floor-control.md)（"判停进行中"算用户在说，关闭插话窗口）
- 基础：[vad](../01-foundations/vad.md)、[asr](../01-foundations/asr.md)、[latency-budget](../01-foundations/latency-budget.md)
- 其他机制：[interruption](interruption.md)（用户开口即打断，与判停共用 VAD）、[first-audio](first-audio.md)（抢先生成：转写未定稿就先跑 LLM）、[audio-preprocessing](audio-preprocessing.md)（降噪、AGC 对 VAD 的影响）、[evaluation](evaluation.md)（判停准确率和每轮等待时长进 trace）
- 项目页：[livekit-agents](../04-projects/frameworks/livekit-agents.md)、[pipecat](../04-projects/frameworks/pipecat.md)、[ten-framework](../04-projects/frameworks/ten-framework.md)、[openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md)、[qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md)、[unmute](../04-projects/full-duplex/unmute.md)、[moshi](../04-projects/full-duplex/moshi.md)、[smart-turn](../04-projects/turn-vad/smart-turn.md)、[ten-vad](../04-projects/turn-vad/ten-vad.md)、[xiaozhi-esp32](../04-projects/devices/xiaozhi-esp32.md)、[xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md)、[funasr](../04-projects/asr/funasr.md)、[sensevoice](../04-projects/asr/sensevoice.md)、[sherpa-onnx](../04-projects/asr/sherpa-onnx.md)、[ultravox](../04-projects/e2e-models/ultravox.md)
