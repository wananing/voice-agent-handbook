# 代码评审清单：按 7 个机制核对现成语音 agent

手册：03-mechanisms/ 七页（"解法分类""各解法的代价""我们的判断""各项目怎么做"）；验收条目取自设计规范 01–06 的"验收要点"一节

拿到一个现成语音 agent 代码库时按本文件走。每个检查项都写成能对着代码回答"是 / 否"的问题；答"否"就是一条发现，按第 9 节的模板记录。

## 目录

0. 评审前先定三件事
1. 判停
2. 打断与截断（含输出仲裁）
3. 首音
4. 工具回合
5. 会话恢复与上下文同步
6. 音频前处理
7. 评测与 trace
8. 跨机制的常见反模式
9. 评审输出模板

---

## 0. 评审前先定三件事

1. **链路形态**：级联 / 半级联 / 半双工 S2S / 原生全双工。找 ASR、TTS 客户端和实时模型客户端（`RealtimeModel`、`*RealtimeLLMService`、`session.update`、`input_audio_buffer.append`）。很多检查项只对某条链路成立，表里标了适用范围。
2. **边界来源**：按键 / 开放麦 / 两者都有。找 `manual`、`commit_user_turn`、`turn_detection`、`listen`、`push_to_talk`。按键产品的判停、打断检查项大幅简化。
3. **框架**：livekit-agents、pipecat、ten-framework、openai-realtime-agents、qwen-audio-agent、unmute、xiaozhi-esp32-server，或自研。框架默认做了的不要重复报，框架已知的缺口要重点看有没有补。

不适用的检查项标"不适用"并写一句理由，不要跳过不记。

严重程度三档：
- **功能错误**：会导致错误行为（双声、吞话、重复执行写操作、回合悬空、上下文与用户听到的不一致）。
- **影响体验**：功能对但慢、误触、不自然。
- **可改进**：可观测性、可维护性、未来风险。

---

## 1. 判停

手册：03-mechanisms/turn-detection.md

### 在代码里找什么

关键词：`vad`、`silence`、`stop_secs`、`min_silence_duration_ms`、`end_of_turn`、`eou`、`endpointing`、`min_delay`、`max_delay`、`turn_detection`、`semantic_vad`、`server_vad`、`commit`、`final`、`transcript_timeout`、`smart_turn`。

| 框架 | 位置 |
|---|---|
| livekit-agents | `voice/audio_recognition.py:AudioRecognition._run_eou_detection`、`voice/turn.py:EndpointingOptions`、`voice/endpointing.py:DynamicEndpointing`；实时模型 `voice/agent_activity.py:_resolve_rt_turn_detection_enabled` |
| pipecat | `turns/user_turn_strategies.py`、`turns/user_stop/turn_analyzer_user_turn_stop_strategy.py`、`audio/turn/smart_turn/local_smart_turn_v3.py`；S2S `services/openai/realtime/llm.py:_handle_user_stopped_speaking` |
| ten-framework | `main_control._on_asr_result`、`ext/ten_turn_detection/` |
| openai-realtime-agents | `App.tsx:updateSession`、`handleTalkButtonUp` |
| qwen-audio-agent | `shared/realtime-model-catalog.mjs`、Provider `buildSession` |
| xiaozhi-esp32-server | `vad/silero.py:VADProvider.is_vad`、`asr/doubao_stream.py` |
| unmute | `unmute_handler.py:determine_pause`、`receive` |

### 检查项

| # | 检查什么 | 为什么重要 / 没做到会怎样 | 推荐做法 | 默认严重度 |
|---|---|---|---|---|
| 1.1 | 按键和开放麦是否走同一个回合状态机，只是边界来源不同？ | 两套状态机会在打断、终态、trace 上各自给答案，彼此不一致 | 一个状态机，`source` 区分 button / vad / text | 可改进 |
| 1.2 | 按键模式下，是否只认按键为回合边界，忽略上游 `speech_started` / 服务端切段事件？ | 不忽略时噪声会打断播报；一次按键被切成几段、松键前就调工具 | 段不是回合，不产生新 `turn_id`；回复只在松键提交时请求一次 | 功能错误 |
| 1.3 | S2S 上游能关服务端判停时，是否关掉（或"只切段不自动回复"，OpenAI 类 `create_response=false`）并自己 commit？ | 自己判停才能和打断、工具、回合 id 共用一套状态 | 关掉；关不掉的上游在上游前做停顿压缩（静音超过 K≈250 ms 的帧不转发，K 明显小于上游判停静音时长） | 功能错误（按键 / 儿童）；影响体验（其余） |
| 1.4 | 是否有短按过滤（按键 < 250 ms 或语音 < 150 ms 丢弃，终态 `discarded`、零出声）？ | 误触短按会产生一轮莫名其妙的回答 | 刚问过用户问题时阈值降到 100 ms，否则会吞掉"好的""不去" | 影响体验 |
| 1.5 | 开放麦是否是"VAD 短静音 + 语义判停 + min/max 等待"，而不是单一固定静音阈值？ | 固定阈值只能在"截断句中停顿"和"处处慢"之间二选一 | VAD 0.2–0.3 s + 音频语义判停 + min/max 0.5 / 3.0 s 起步；有条件开动态端点 | 影响体验 |
| 1.6 | 判停之后的定稿等待是否有超时和兜底？ | 无超时会等 final 等到天荒地老；无兜底会丢掉最后半句 | 等 final 设超时，超时用 interim（LiveKit `transcript_timeout=2.0` s）；或主动冲刷 STT；S2S 收到提交确认且已有识别结果就立即定稿，3 s 识别超时按 `asr_timeout` 失败收口 | 功能错误 |
| 1.7 | 静音时长是否按音频采样数算，而不是墙钟？ | 网络卡顿时墙钟静音会误判说完 | 按收到的音频样本累计 | 影响体验 |
| 1.8 | VAD 是否没有绝对音量门槛（或门槛之前已做 AGC）？ | 小声、离麦远的用户（儿童）被判成非语音（Pipecat `min_volume=0.6` 是这类风险） | 去掉门槛或先做 AGC | 影响体验 |
| 1.9 | 中文儿童场景是否没有用文本判停？ | 孩子 ASR 错误率高，错字直接进判停输入；还要 GPU 服务 | 只用音频判停，且只让它决定"多等几百毫秒" | 影响体验 |
| 1.10 | 阈值是否在自己的样本上定过，判停相关指标是否进每轮 trace？ | 仓库默认值都是成人英文起点；儿童、中文准确率需实测 | trace 记：按键期间上游切段次数、压缩静音时长、短按丢弃次数、语义 P 值、实际等待时长、补充窗口命中率 | 可改进 |

### 常见反模式

- 用一个固定静音阈值服务所有用户，然后在"太快被截断"和"太慢"之间反复调。
- 按键设备上照样响应上游服务端 VAD，松键前就开始回答或调工具。
- 规则短路（如"≤ 2 字算噪音"）在模型已开始调工具或说话后仍生效，吞掉被切出来的半句话。
- 填充语在判停等待期间出声：等于抢话，还可能被自己的 VAD 当成人声。

---

## 2. 打断与截断（含输出仲裁）

手册：03-mechanisms/interruption.md；架构见 02-architectures/floor-control.md、02-architectures/turn-model.md

### 在代码里找什么

关键词：`interrupt`、`abort`、`barge`、`cancel`、`flush`、`clear_buffer`、`truncate`、`audio_end_ms`、`played_ms`、`playback_position`、`generation`、`gen`、`turn_id`、`request_id`、`sentence_id`、`interrupted=True`、`allow_interruptions`、`min_duration`、`aec_warmup`。

| 框架 | 位置 |
|---|---|
| livekit-agents | `voice/agent_activity.py:_interrupt_by_audio_activity`、`_on_input_speech_started`、`voice/turn.py:InterruptionOptions`、`voice/generation.py:forward_generation`、`llm/_realtime/openai.py:RealtimeSession.truncate` |
| pipecat | `processors/frame_processor.py:broadcast_interruption`、`services/tts_service.py:_handle_interruption`、`transports/base_output.py:MediaSender.handle_interruptions`、`services/openai/realtime/llm.py:_truncate_current_audio_response` |
| ten-framework | `main_control` 扩展 `_interrupt`、`openai_mllm_python/extension.py`（truncate 被注释掉） |
| openai-realtime-agents | SDK `openaiRealtimeWebRtc.mjs:interrupt` |
| qwen-audio-agent | `RealtimeInputRuntime.#startSpeech`、`RealtimeResponseSlot.recover`、`announcement-manager.mjs` |
| xiaozhi-esp32 / -server | 设备 `Application::AbortSpeaking`、`audio_service.cc`；服务端 `abortHandle.py`、`sendAudioHandle.py` |
| unmute | `unmute_handler.py:interrupt_bot`、`_stt_loop` |

### 检查项

| # | 检查什么 | 为什么重要 / 没做到会怎样 | 推荐做法 | 默认严重度 |
|---|---|---|---|---|
| 2.1 | 打断后过滤旧输出是否用单调递增的代际（或回合 id），而不只是"清空队列"？ | 清空只处理已在队列里的帧；上游 cancel 后、TTS 取消后才到的帧会被当成新回复播出 | 新回合或打断时代际 +1（同时发生只 +1 一次）；回合正常结束、工具返回、重建都不 +1 | 功能错误 |
| 2.2 | 代际是否在入队、授权、每个下行帧发出前三处比较？ | 只在入队比较时，已授权仍在生成的上游音频流漏过 | 三处都比；设备侧再按 `turn_id` 和本地播放代际做第二道过滤 | 功能错误 |
| 2.3 | 设备 / 客户端是否只播当前回合的音频，并有本地播放代际防同回合 abort 后的残余包？ | 同回合残余包的 `turn_id` 合法，只有本地代际挡得住 | 每次本地停播 `play_gen += 1`，出队播放前比对（小智 `playback_generation_` 的做法） | 功能错误 |
| 2.4 | 冲刷之后被冲刷回合是否零音频（`flush.end` 后计数为 0）？ | 双声或旧音频在新回合开头冒出来 | 冲刷顺序：撤销授权 → 上游 cancel + truncate → 本地 TTS flush → 清发送队列 → 通知设备丢缓冲 | 功能错误 |
| 2.5 | 被打断的回复是否只把已播出的部分写进历史，并带显式打断标记；一帧没播的是否删掉？ | 不截断：用户说"第二个"时模型按全文理解；没播的注意事项模型以为已告知 | 级联按 TTS 播放时刻的词截断；没有字级时间戳退化为"已播完的句 + 当前句按比例"；保留打断标记（不学 unmute 去掉标记） | 功能错误 |
| 2.6 | S2S 的 `truncate(audio_end_ms)` 是否以设备播放回执为准，而不是"收到首个 delta 起的墙钟"？ | 墙钟没扣播放缓冲，偏大，模型以为用户多听了一截（pipecat 的现状） | `audio_end_ms = offset(句) + played_ms`；回执 300 ms 内没到用服务端估计并在 trace 标 `truncate_estimated` | 功能错误（设备深缓冲）；影响体验（WebRTC） |
| 2.7 | 上游不支持 truncate（Gemini、AWS 等）时，本地上下文是否仍记已播文本，下次同步以本地为准？ | 否则重建时把用户没听到的内容灌回上游 | 本地记录始终是"用户听到的"版本 | 功能错误 |
| 2.8 | 开放麦声学打断是否有过滤：AEC 预热（约 3 s）、最短时长（约 0.5 s）、噪声大时最少字数？ | 回声、咳嗽、附和打断 agent，一句话说不完 | LiveKit 默认值是唯一完整实现的起点；能暂停的输出加暂停-恢复（2 s 无回合则恢复） | 影响体验 |
| 2.9 | 按键设备是否本地先停播、清缓冲、代际 +1，再上报 `abort` 和播放位置？ | 等服务端往返才停播，打断不干脆 | 规范门槛：key-down 到扬声器降到底噪 ≤ 60 ms（建议值，需实测） | 影响体验 |
| 2.10 | 判停和打断是否都在服务端，客户端不因本地 VAD 自行停播（开放麦）？ | 端云状态不一致 | 客户端只执行服务端的停播 / flush 指令 | 功能错误 |
| 2.11 | 有多个出声来源（模型回复、工具结果、提示语、播报）时，是否有一个仲裁器保证同一时刻只授权一个？ | 双声 | 单话筒 + 有界队列；优先级只决定排队顺序，不抢占（`critical` 除外） | 功能错误 |
| 2.12 | 长生命周期任务（事件分发循环、后台回帖协程、定时器）是否每次出声前重新领取租约，而不是缓存？ | 典型 bug："每个连接只有第一轮有声音" | 每次出声以新请求入队，授权时拿当前代际 | 功能错误 |
| 2.13 | 回合外的播报是否只在插话窗口打开时授权，窗口以设备播放回执为准？ | 不看回执会把播报插进一句话的尾巴 | 窗口关闭 ⇔ 用户在说 / 回合未结束 / 有已下发未回执的音频；打开后再静默约 350 ms；被挡住的定时自检 | 影响体验 |
| 2.14 | 被用户打断的播报是否不重播；回执丢失是否有兜底（开始回执超时视为未送达、有上限重试；结束回执超时按剩余时长 + 2 s 视为播完）？ | 重播打扰；回执丢失时插话窗口永不打开 | 非用户原因中断才有上限重试，任何时刻不叠声 | 影响体验 |
| 2.15 | 自托管 TTS 能否中途停止？停不下来的（CosyVoice、fish-speech、spark-tts）是否自加了停止标志？ | 打断后后台继续合成占算力，残包可能漏到下一回合 | TTS "能否中途停止"当选型硬指标 | 影响体验 |

### 常见反模式

- 打断只"清空队列"，不按代际或回合 id 过滤迟到帧（pipecat 默认）。
- 被打断的回复全文写入历史，没有标记（小智服务端；qwen-audio-agent 开始播放后即写全文）。
- 截断位置按服务端墙钟估算，不扣播放缓冲。
- 打断标记送 LLM 前去掉（unmute），模型不知道自己被打断。
- 用"不可打断"保护写操作。写操作靠执行前检查代际。
- 维护服务端镜像却不处理 `conversation.item.truncated`，重连重放时被截断的回复以全文回到上游（LiveKit 推断风险）。

---

## 3. 首音

手册：03-mechanisms/first-audio.md

### 在代码里找什么

关键词：`aggregator`、`sentence`、`split`、`punctuation`、`first_sentence`、`chunk`、`stream`、`preemptive`、`eager`、`speculat`、`prewarm`、`warmup`、`filler`、`say(`、`TTSSpeakFrame`、`cue`、`ttfb`。

| 框架 | 位置 |
|---|---|
| livekit-agents | `voice/agent_activity.py:on_preemptive_generation`、`voice/turn.py:PreemptiveGenerationOptions`、`voice/filler_scheduler.py` |
| pipecat | `utils/text/simple_text_aggregator.py`、`turns/speculation_gate.py`、`services/tts_service.py:TextAggregationMode` |
| ten-framework | `main_control` 的 `helper.py:parse_sentences` |
| xiaozhi-esp32-server | `core/providers/tts/base.py`（`first_sentence_punctuations`）、`core/handle/sendAudioHandle.py` |
| unmute | `_generate_response_task`、`rechunk_to_words` |

### 检查项

| # | 检查什么 | 为什么重要 / 没做到会怎样 | 推荐做法 | 默认严重度 |
|---|---|---|---|---|
| 3.1 | "有声音"和"有内容"两个首音是否分开打点、分开报，并注明起点？ | 提示语把"有声音"做得好看，掩盖"有内容"慢 | 分开报 P50 / P90，带锚点（松键 / 判停 / 音频结束）和 n | 可改进 |
| 3.2 | 首段是否自己切且有首段特例，而不是等整句或用 TTS 内置切句？ | 等整句多 0.3–0.7 s（推算）；CosyVoice 内置切段首段可长达 60–80 token | 首段遇 `。！？；，、` 且 ≥ 6 字切，12–15 字强制切；后续 20–40 字；TN 和 Markdown 清理放在切分之前 | 影响体验 |
| 3.3 | 中文是否没有按空格切词逐词送 TTS？ | 中文没有空格，整段回复憋到流结束才送（unmute 的实现） | 按中文标点和字数切 | 功能错误（中文） |
| 3.4 | 逐 token / 逐词送是否只用在支持文本流入的 TTS 上？ | 对整句接口逐字送，每次都重新 prefill | 只有 CosyVoice CV2/3（bistream）这类训练过交错的 TTS 才逐词送 | 影响体验 |
| 3.5 | TTS 路径是否真正"首块即出"（不是名义流式实际整段）？ | fish-speech 本仓库路径、Spark-TTS / IndexTTS 本地 PyTorch 路径都是整段出 | 在自己的并发下实测首块，裁首部静音计时；目标 P50 ≤ 0.4 s 能否达到需实测 | 影响体验 |
| 3.6 | TTS 建连 / 说话人特征缓存 / 模型预热是否移出关键路径？ | 每回合冷建连多 0.1–0.3 s（推算） | 建连与 LLM 请求并行（unmute 写法）；说话人特征启动时缓存；回合内复用连接 | 影响体验 |
| 3.7 | 抢先生成是否只在开放麦级联开、只抢 LLM、遇工具调用撤销？ | 工具有副作用，投机被丢弃撤不回；按键松键即定稿，抢先收益小 | 按键改成"识别定稿即起跑"；LiveKit 默认开启，按键产品要确认是否关掉；命中率 ≥ 70% 才保留（需实测） | 功能错误（抢跑工具）；可改进（其余） |
| 3.8 | 抢先生成的闸门是否接了代际？ | 没接时被丢弃的输出可能漏播 | 放行前比代际 | 功能错误 |
| 3.9 | 工具回合的提示语是否本地预合成、短（≤ 300 ms）、工具在开播前已完成就不播、不进模型上下文？ | 长 cue 推迟正文；进上下文会被模型模仿 | 会话开始时同音色批量预合成、裁首尾静音、按文本缓存；措辞"我 + 动作 + 对象" | 影响体验 |
| 3.10 | 工具结果是否立即就绪、只在播放时排在提示语后面，而不是等提示语播完才去拿？ | 提示语时长整段串进有内容延迟 | 结果与提示语并行，仲裁器排队 | 影响体验 |
| 3.11 | 无屏设备是否保证 2 s 内必有声音，每个 `failed` 回合都发声（本地预合成话术）？ | 沉默即故障，用户分不清"在想"和"挂了" | 填充看门狗约 2 s；规范门槛：全程无 > 2.5 s 静默 | 功能错误（无屏） |
| 3.12 | 下行是否按实时节奏限速、客户端浅缓冲，而不是一次推送整段音频？ | 深缓冲让打断不干脆、播放位置估不准 | 前几包直发预缓冲，之后按帧限速；起播门槛 1 帧 | 影响体验 |

### 常见反模式

- 用填充语掩盖判停等待。
- 只靠 prompt 让模型"先说一句"，代码没有兜底；模型跳过就干等（openai-realtime-agents）。
- 提示语和在线合成用不同模型版本或说话人缓存，听成两个人。
- 只报一个"首音"数字。

---

## 4. 工具回合

手册：03-mechanisms/tool-calls.md

### 在代码里找什么

关键词：`function_call`、`tool_call`、`function_call_output`、`response.create`、`reply_required`、`run_llm`、`StopResponse`、`cancel_on_interruption`、`ctx.update`、`delegation`、`max_tool_steps`、`parallel_tool_calls`、`idempotency`、`superseded`、`duplicate`。

| 框架 | 位置 |
|---|---|
| livekit-agents | `voice/agent_session.py:say`、`llm/tool_context.py:ToolResult`、`voice/events.py:RunContext.update`、`voice/generation.py:make_tool_output`、`_inject_running_tool_calls` |
| pipecat | `services/llm_service.py:run_function_calls`、`llm_response_universal.py:_handle_function_call_result`、`processors/aggregators/async_tool_messages.py` |
| pipecat-flows | `manager.py:_create_transition_func`、`_check_and_execute_transition` |
| openai-realtime-agents | `supervisorAgent.ts:getNextResponseFromSupervisor`；SDK `realtimeSession.mjs:#handleFunctionToolCall` |
| qwen-audio-agent | `frontend/tools/agent-task-runtime.mjs`、`tool-call-handler.mjs`、`announcement-window.mjs` |
| xiaozhi-esp32-server | `core/connection.py:chat`、`plugins_func/register.py` |

### 检查项

| # | 检查什么 | 为什么重要 / 没做到会怎样 | 推荐做法 | 默认严重度 |
|---|---|---|---|---|
| 4.1 | 每个工具调用是否在超时加余量内必然收口（回传 output 或本地终态），没有悬着的调用？ | 悬着的调用让模型后续反复调同一工具（一份实践笔记：闲聊约 21%，打断后更高） | 六个终态：`result / interrupted / cancelled / progress / superseded / duplicate`；终态之后的结果一律拒绝 | 功能错误 |
| 4.2 | "回传结果"和"让模型再生成"是否分开决定？ | 绑死时每个工具回合都多付一圈再生成（S2S 约 1.2 s） | 可模板化、毫秒级结果：本地直念 + 回传收口 + 不再生成（LiveKit `reply_required=False`、Pipecat `run_llm=False`、小智 `RESPONSE`） | 影响体验 |
| 4.3 | S2S 上，是否先确认上游收到结果后会不会自动续答、有没有静默选项，再决定跳过再生成？ | 没有静默选项的上游照样开口（LiveKit 的 gpt-live-1 委派） | 能力声明字段表达；没有静默选项退到"回传再生成 + 提示语"或改走半级联 | 功能错误 |
| 4.4 | 秒级以上或调后端的写操作，是否先回"已提交、正在处理"收口，最终结果等插话窗口作播报？ | 长工具占住对话；不收口则悬着 | 照 LiveKit `ctx.update()` / qwen-audio-agent 插话窗口；进度 output 附"不要编造结果、不要再次调用" | 影响体验 |
| 4.5 | 写操作是否在执行前再查一次代际（是否已被打断），带幂等键，且同一回合只认第一个写调用？ | 副作用撤不回；重复执行 | 后端判定成败；执行完才被打断的，下一次空闲补一句告知；写操作重复副作用目标 0 | 功能错误 |
| 4.6 | 写操作门槛和规则是否留在委派之前，而不是交给后台模型执行？ | 后台模型执行写操作不可审核 | 委派只给只读工具 | 功能错误 |
| 4.7 | 打断时调用是否按阶段收口：结果未回传就不再回传、本地置终态；已回传正在念时补极短 `{"interrupted": true}`；过期回合的调用回 `superseded`、不触发生成？ | 一份实践笔记：补与不补出声率 20/20 对 15/20 | 只读工具直接取消；已发出的写操作不撤回 | 功能错误 |
| 4.8 | 委派进行中被打断，请求是否取消或结果被代际拦下？ | 过时答案被念出来（openai-realtime-agents 的 `fetch` 不取消，推断） | 结果回传前比代际 | 功能错误 |
| 4.9 | 委派是否只用于长任务、多步推理？< 2 s 的单步工具是否留在前台？ | 委派不省再生成，短工具反而更慢（qwen-audio-agent 实测 1.317 s → 3.363 s） | 按 `latency_class` 分流 | 影响体验 |
| 4.10 | 委派失败、超时、无输出时前台是否一定回一句（本地直念兜底）？ | 用户面对沉默 | 兜底话本地直念，不交给模型再生成 | 功能错误 |
| 4.11 | 是否有递归上限（3–5 步），到顶后禁用工具、要求直接回答？ | 工具循环 | 照小智；LiveKit `max_tool_steps` 只在级联路径生效，实时路径要自己确认 | 影响体验 |
| 4.12 | 回传的 output 是否写"实际播出的原文 + 约束说明"（如"已经告诉用户了，不要重复"），不含 ID、URL？ | 回传内容与实际播出不一致时模型指代出错 | 按播放位置截断后的文本 | 可改进 |
| 4.13 | S2S 回传结果前是否避开了正在生成的 response（单槽位上游）？ | 与上游冲突或被拒 | 框架"等前导语音播完再回传"时，把提示语时长计入有内容延迟 | 影响体验 |
| 4.14 | 工具表是否建会话时一次给全，按阶段在工具层拒绝，而不是会话中改工具表？ | Gemini、Ultravox、AWS 改工具要重连；改配置让缓存失效 | 阶段外调用在工具层拒绝并收口 | 影响体验 |

### 常见反模式

- 收口和再生成绑死（openai-realtime-agents：执行完总是 output + `response.create`）。
- "只选工具、直接念结果、不回传"：省了延迟，留下悬着的调用。
- 异步工具结果晚到，直接插进用户或模型正在说的话里。
- 把执行期提示语（`TTSSpeakFrame` 默认 `append_to_context=True`）写进上下文，被模型模仿。

---

## 5. 会话恢复与上下文同步

手册：03-mechanisms/session-recovery.md；架构见 02-architectures/state-and-context.md

### 在代码里找什么

关键词：`reconnect`、`backoff`、`resumption`、`session_id`、`conversation.item.create`、`session.update`、`instructions`、`chat_ctx`、`history`、`summar`、`snapshot`、`max_session_duration`、`fatal`、`1007`、`restore`。

| 框架 | 位置 |
|---|---|
| livekit-agents | `llm/remote_chat_context.py:RemoteChatContext`、`llm/utils.py:compute_chat_ctx_diff`、`RealtimeSession._main_task`（`_reconnect`）、`llm/realtime_fallback_adapter.py:_swap`、`_is_fatal_error` |
| pipecat | `services/openai/realtime/llm.py:_handle_context` / `reset_conversation`、`services/google/gemini_live/llm.py:_reconnect`、`services/aws/nova_sonic/session_continuation.py` |
| ten-framework | `openai_mllm_python/extension.py:_handle_reconnect` |
| qwen-audio-agent | `RealtimeFrontend.updateAgentContext`、`restoreRecentConversation`、`voice/reconnect-backoff.mjs` |
| xiaozhi-esp32-server | `utils/dialogue.py`、`mem_local_short` |

### 检查项

| # | 检查什么 | 为什么重要 / 没做到会怎样 | 推荐做法 | 默认严重度 |
|---|---|---|---|---|
| 5.1 | 本地上下文是否是对话的唯一真相，上游会话只写不读（不从镜像反推、不用镜像重放）？ | 镜像不一定跟随截断；重放会把用户没听到的全文灌回去 | 镜像如要维护，只用于判断"哪些工具结果已回传" | 功能错误 |
| 5.2 | S2S 会话中途是否只同步工具结果（加上上游支持的按回复临时指令 / 专门追加通道），不中途插入历史或状态说明？ | 实践笔记：建会话后逐条插入 0/6，中途插入 system / user 条目"基本不看" | 历史走建会话参数，状态走系统提示 | 功能错误 |
| 5.3 | 重建会话时，历史是否在建会话参数里带入（实测 6/6），assistant 一侧只放用户听到的文本、被打断条目带标记？ | 会话 id 接续 1/6；逐条插入 0/6 | 输入固定为"人设 + 状态块 + 从本地投影的历史 + 工具表"；上下文超限先缩历史 | 功能错误 |
| 5.4 | 断线、上下文超限、主动换会话是否走同一个重建器？ | 多处各写一套重建，行为不一致 | 一个重建器；非紧急触发等空档（助手说完、无未收口调用、用户没在说） | 可改进 |
| 5.5 | 错误是否分四类（重建也没用 / 可重建 / 上下文超限 / 非致命），有重建预算和退避？ | 全当断线：无谓重建、缓存全冷；全当可恢复：鉴权、配额错误无限重连；1007 原样重放必然再失败 | 鉴权、配额走白名单停止重建；超限先缩历史；非致命只让本回合失败；预算如 3 次指数退避 | 功能错误 |
| 5.6 | 重建后是否重建"已回传工具结果"登记表，旧会话的在途调用由本地收口、不回传给新会话？ | 重发旧结果、重复调用 | 新会话不认识旧 call id | 功能错误 |
| 5.7 | 断线时没有终态的回合是否按规则了结：无写操作则以最后一句 user 重答；已执行写操作则不重放、本地告知并写状态块；状态未知先用幂等键查后端？ | 重复执行写操作；或用户觉得"它没听见" | 回合终态率 100%（规范门槛） | 功能错误 |
| 5.8 | 是否记录每轮回复模态，往音频会话灌文本后出现纯文本回复时交给本地 TTS 出声？ | 纯文本回复导致无声 | trace 记 `reply_modality`；同一会话反复出现就在空档换会话 | 功能错误 |
| 5.9 | 状态块是否结构固定、带版本、有长度上限、只放自己的数据（不放业务事实），只在两轮之间更新？ | 每轮 `session.update` 让缓存失效（输入 ×3.2，首音 +150–270 ms） | 只在"不更新就会选错动作"的字段变化时立即更新，其余懒生效；级联放消息尾部保持前缀稳定 | 影响体验 |
| 5.10 | 硬信息（名字、陪同人、已选路线）是否进状态块，不依赖历史或摘要？上游有轮数 / 时长上限时是否主动换会话？ | 上游约 20 轮上限（一个闭源上游的观察值）后早期信息丢失 | 摘要自己做、从原始条目重新生成、不切断未收口的工具调用 | 影响体验 |
| 5.11 | 自有设备或 App 是否有跨连接快照，在恢复窗口内重连算同一会话？快照是否含已执行写操作的幂等键、不含上游镜像和原始音频？ | 重连从头说；重放副作用 | 每个回合终态后写一次；窗口长度按产品定（30 分钟、12 轮是一份实践笔记的值） | 影响体验 |
| 5.12 | 写操作是否以业务后端为唯一真相，只有后端判"成功"才推进本地状态？ | 本地状态与后端分叉 | 结果分成功 / 被拒 / 不可用三类；本地业务状态是可丢弃的投影 | 功能错误 |
| 5.13 | 级联 / 半级联 / 无状态端到端模型：是否每轮从本地上下文全量拼消息，且历史里有用户文本（端到端模型另跑 ASR）？ | 端到端模型不产出用户转写，KV 一丢就恢复不了 | 长会话用"状态块 + 最近 K 轮 + 摘要"控长度 | 功能错误（端到端模型） |

### 常见反模式

- 重连时把服务端镜像逐条 `conversation.item.create` 重放（LiveKit OpenAI 插件、TEN）。
- 固定 1 s 重试、无退避无上限（TEN）。
- 用会话 id 接续代替重灌。
- 摘要只改了本地 context，没有接线到重建，上游永远看不到（pipecat `LLMContextSummarizer` 要自己接）。
- 适配层偷偷替代：上游不支持按回复指令时把指令写成普通 user 条目进历史。做不到应返回 `unsupported`，由策略层显式选择。
- 通用代码里按厂商名分支，而不是读能力声明字段。

---

## 6. 音频前处理

手册：03-mechanisms/audio-preprocessing.md

### 在代码里找什么

关键词：`echoCancellation`、`noiseSuppression`、`autoGainControl`、`getUserMedia`、`aec`、`AudioProcessingModule`、`ApplyConfig`、`ProcessReverseStream`、`noise_cancellation`、`audio_in_filter`、`RNNoise`、`Krisp`、`resample`、`soxr`、`min_volume`、`aec_warmup_duration`。

| 位置 | 看什么 |
|---|---|
| 浏览器客户端 | `getUserMedia` 约束是否写全 |
| livekit-agents | `voice/room_io/_input.py`、`room_io.py`（AGC 默认）、`aec_warmup_duration` |
| pipecat | `transports/base_input.py:BaseInputTransport`、`audio/filters/`、VAD `min_volume` |
| xiaozhi-esp32 | `main/audio/engines/afe_audio_engine.cc`、编译期 AEC 选项 |
| 自有 ARM Linux 设备 | APM AEC3 的 render / capture 喂法、实例生命周期 |

### 检查项

| # | 检查什么 | 为什么重要 / 没做到会怎样 | 推荐做法 | 默认严重度 |
|---|---|---|---|---|
| 6.1 | 开放麦产品是否在扬声器所在的机器上做 AEC？ | 服务端拿不到扬声器参考；没有端侧 AEC，"说话即打断"会被自己的回声触发 | 浏览器三项约束全开；硬件用芯片 AEC 或移植 APM AEC3；做不到就退回按键 | 功能错误 |
| 6.2 | 移植 AEC3 时：播放期间麦克风和 APM 是否一直在跑？实例是否跨轮保持（不每轮 `Initialize()`）？render 是否取进 DAC 前最后一份 PCM、音量变化是否通知 APM？ | 任一不满足 AEC 不收敛 | 三条都满足；累计约 2.5 s 有效 render 才离开初始状态 | 功能错误 |
| 6.3 | 服务端是否有 AEC 预热窗口和播放期间的打断门槛？ | 收敛前残余回声造成误打断 | 预热约 3 s；最短时长或最少词数 | 影响体验 |
| 6.4 | 降噪是否默认只放在判别支路（VAD / 判停前），送 ASR / S2S 的主路不降噪，除非在目标人群录音上 A/B 证明有益？ | 降噪伪影会让在原始音频上训练的 ASR / S2S 变差 | 判别支路和主路分开（LiveKit Silero 插件的做法） | 影响体验 |
| 6.5 | 主路要降噪时，是否选有增益下限的方案（APM NS `kLow` 或 Krisp 50–75），而不是 RNNoise 或强度 100？ | RNNoise 无增益下限，有"呼吸感"，会压小声儿童语音 | 浏览器已开 `noiseSuppression` 时服务端不再叠降噪 | 影响体验 |
| 6.6 | 采样率处理顺序是否是"解码 → 降噪 → 一次降到 16 kHz → VAD / ASR"，没有多次来回重采样？ | 采样率不对 VAD 结果不可信且不报错；重采样块边界爆音、流结束不 flush 吞尾音 | Opus 能直接解到 16 kHz 就别先解 48 kHz | 功能错误（采样率错）；影响体验（其余） |
| 6.7 | 设备端处理顺序是否是"AEC → 高通 → NS → AGC"？ | 先放大再去回声会把回声和噪声一起放大 | 照 APM 内部顺序 | 影响体验 |
| 6.8 | 小声用户是否靠端侧硬件增益 + 固定数字增益打底，自适应 AGC 只做小范围修正？ | AGC2 在户外底噪下允许增益为 0 dB，恰好不放大小声孩子；1–3 s 的按键追不上增益 | VAD 不设绝对音量门槛 | 影响体验 |
| 6.9 | 按键产品无 AEC 时，按键瞬间是否本地停播，并把上行开头一段标记为"可能含回声尾巴"（只标记不丢弃）？ | 回声混进上行，上游听到设备自己的声音 | 起点 150–300 ms，按实测收敛时间定 | 可改进 |
| 6.10 | 要走电话线路时，前处理和评测是否在 8 kHz（PCMU / PCMA）上再做一遍？ | 16 kHz 上调好的阈值在 8 kHz 上不成立 | openai-realtime-agents 可切编码模拟电话线路 | 影响体验 |

### 常见反模式

- 指望服务端 AEC 救场（每轮冷启动、延迟混网络抖动、Opus 非线性失真）。
- 主路直接开 RNNoise 或强度 100 的降噪压儿童语音。
- 先把 Opus 解到 48 kHz 再降到 16 kHz，中间没做任何 48 kHz 处理。
- 降噪器在 agent 说话期间学到"回声也是噪声"，之后把真人语音一起压掉。

---

## 7. 评测与 trace

手册：03-mechanisms/evaluation.md

### 在代码里找什么

关键词：`trace`、`turn_id`、`metrics`、`latency`、`ttfb`、`observer`、`otel`、`span`、`eval`、`judge`、`repeat`、`mock_tools`、`prompt_version`、`p50`、`mean`。

| 框架 | 位置 |
|---|---|
| livekit-agents | `voice/run_result.py:RunResult`、`evals/judge.py`、`evals/evaluation.py:JudgeGroup`、`simulation.py`、`metrics/` |
| pipecat | `src/pipecat/evals/`、`evals/release/`、`observers/user_bot_latency_observer.py`、`tests/utils.py:run_test` |
| qwen-audio-agent | `examples/smart-cockpit/bench/`、`server/test/realtime-provider-behavior.test.mjs` |
| unmute | `loadtest_result.py`、`unmute/metrics.py` |
| xiaozhi-esp32-server | `performance_tester.py`（只算均值，只能当冒烟） |

### 检查项

| # | 检查什么 | 为什么重要 / 没做到会怎样 | 推荐做法 | 默认严重度 |
|---|---|---|---|---|
| 7.1 | 是否每回合恰好一条 trace，所有链路共用一个 schema，每条记录带 `turn_id`？ | 线上问题无法定位到判停、识别、决策还是 TTS；迟到事件归不到回合 | 终态、触发原因、`rebuild_reason`、版本号进 trace | 可改进 |
| 7.2 | 延迟是否写明起点锚点（T_ae 音频结束 / T_rel 松键 / T_ep 判停），有声音和有内容分开？ | 口径不同的"首音 P50 1.0 s"可以差几百毫秒 | 离线对比用 T_ae；线上按产品形态选主锚点，其余能测的都记 | 可改进 |
| 7.3 | 报表是否只报 P50 / P90（容量另报 P95），附 n 和失败率，不报平均值？ | 平均值被一次冷连接拉偏 | P50 每组 n ≥ 20、P90 n ≥ 50；不够的标"样本不足"、不进门禁 | 可改进 |
| 7.4 | 首音是否拆段（stt / 判停 + 冲刷 / llm / tts_start），工具回合是否拆"工具前 / 工具后"，排队等待是否单列？ | 不拆段就不知道优化哪一段 | 照 unmute 压测和 qwen-audio-agent bench 的拆法 | 可改进 |
| 7.5 | 是否有冻结盲测集（从未用于调参），每句配 2–3 个 ASR 噪声变体，含写操作负例，每次提交跑？ | 规则在训练句上 100%、没见过的句子上 54%（一份实践笔记） | 指标分开命名（动作命中、槽位命中、无工具轮正确率、写操作误触率），不合成一个"准确率" | 影响体验 |
| 7.6 | 打断、异步工具、判停这类竞态用例是否多次重复、按通过率进门禁？ | 单次通过不说明问题 | 手册写 `--repeat ≥ 10`，设计规范要求每条 ≥ 20 次；取高者 | 可改进 |
| 7.7 | 多轮脚本是否覆盖：插话打断、工具各阶段的打断、空转写和噪声不回应、不支持的请求、断线重连、指代和早期信息召回？ | 单句文本测试测不到这些 | 格式借 Pipecat eval（`send_after`、`absent`、`within_ms`），断言借 LiveKit（事件序列 + `mock_tools`） | 影响体验 |
| 7.8 | 是否有音频模态的评测，而不只是文本模态？ | 框架自带评测默认文本模态，"有评测"不等于测过语音链路 | 换 ASR / TTS / VAD 时跑单项：同一归一化口径的 CER、TTS 回转 CER 和首包、VAD PR 曲线、判停准确率；加噪声注入（SNR 20 / 10 / 5 dB） | 影响体验 |
| 7.9 | 人设、工具表、状态块的版本是否进 trace（`prompt_version`）？ | 换一版 prompt 无法按版本分批比较 | 每轮写入 | 可改进 |
| 7.10 | judge 是否看得到工具结果，判 yes 的结果是否定期人工抽查？ | judge 会把"我查一下"判成已回答；中文、儿童口语、同音字会误判 | 抽查 yes 而不只看 no | 可改进 |
| 7.11 | 儿童、老人等目标人群是否以真人录音冻结集为准，模拟用户只用来找回归？ | LLM persona 说话太完整、太礼貌，绝对分不可信 | 模拟用户不报绝对分 | 影响体验 |

### 常见反模式

- 只算平均值、5 次左右的"性能测试"当基准用。
- 按键和开放麦、送出口径和播放回执口径混在一起算分位数。
- 竞态脚本单次运行就进门禁。
- 用文本模态评测结果宣称"语音链路测过了"。

---

## 8. 跨机制的常见反模式

这些问题跨了几个机制，单看某一节容易漏：

- **协议里没有回合**：没有 `turn_id`、没有终态、没有播放回执（小智协议）。后果连锁：迟到帧无法归属、截断没有依据、被打断全文写历史、设备分不清"在想"和"挂了"。看到这一条，第 2、3、5 节相关项多半都是"否"。
- **回合可以悬空**：没有看门狗（识别超时约 3 s、填充约 2 s、响应超时约 8 s、排空"剩余时长 + 2 s"，均为建议值需实测）。规范门槛：长稳压测中悬空回合数为 0。
- **适配层里有策略**：上游适配代码里有重试循环、定时回收、回合计数、按内容分支。这些应在链路无关层，适配层只做 I/O 和明确拒绝。
- **"自己的话"和模型声音不统一**：S2S 原生声音加本地 TTS 播报，用户听成两个人。要么全部走 TTS（半级联），要么同音色预合成。

---

## 9. 评审输出模板

按机制列发现，每条带严重程度、代码位置、建议。先给总览，再给明细。没有发现的机制也要写一行"已核对，无发现"，让读者知道查过。

```markdown
# <项目名> 语音链路评审

## 总览
- 链路：<级联 / 半级联 / 半双工 S2S / 原生全双工>；边界来源：<按键 / 开放麦>；框架：<名称与版本或 commit>
- 发现数：功能错误 N，影响体验 N，可改进 N
- 最先要修的三条：<编号 + 一句话>

## 1. 判停
| 编号 | 严重程度 | 发现 | 代码位置 | 建议 | 对应检查项 |
|---|---|---|---|---|---|
| T-1 | 功能错误 | 按键模式下仍响应上游 `speech_started`，一次按键被切成两轮 | `gateway/session.py:142` `on_upstream_event` | 按键模式忽略上游切段事件，松键时统一提交 | 1.2 |

## 2. 打断与截断
（同上表，编号 I-1 起）

## 3. 首音
（编号 F-1 起）

## 4. 工具回合
（编号 U-1 起）

## 5. 会话恢复与上下文同步
（编号 S-1 起）

## 6. 音频前处理
（编号 A-1 起）

## 7. 评测与 trace
（编号 E-1 起）

## 不适用的检查项
- <编号>：<理由>

## 需实测才能下结论的项
- <检查项>：<要做的实验，例如"注入 cancel 后 0–2 s 的迟到 delta，统计扬声器侧帧数，重复 20 次">
```

写发现时注意：

- 代码位置写到文件和函数（有行号更好），不要只写模块名。
- "建议"写具体做法和参照实现（例如"照 LiveKit `ToolResult(reply_required=False)`"），不要写"建议优化"。
- 发现依赖手册里"推断"而非实测的结论时（例如 pipecat 迟到帧会被播出），写明"推断，需用故障注入验证"，并把实验放进最后一节。
- 同一个根因导致多条发现时，在总览里合并说明根因（最常见的是"协议里没有回合"），明细里互相引用编号。
