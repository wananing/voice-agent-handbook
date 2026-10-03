# 打断与截断（interruption）

手册：03-mechanisms/interruption.md

## 问题

agent 说话时用户开口（或按键、点击、打字），系统要依次：判定是真打断（不是回声、咳嗽、附和）→ 停播 → 冲刷 LLM / TTS / 上游 S2S 并丢掉之后才到的旧帧 → 把上下文截成用户实际听到的部分 → 决定被打断的回复怎么记。任一步失败：误打断说不完一句话；双声或旧音频冒出；模型按没播出的全文理解"第二个"，或以为已告知；悬着的工具调用让模型反复调同一工具（实践笔记实测闲聊约 21%，打断后更高）。

体感打断延迟 = 扬声器静音时刻 − 用户开口时刻 = 触发源延迟 + 过滤门槛 + 服务端到设备往返 + 设备缓冲里已解码音频长度。

## 解法分类

**触发源。** 越早越灵敏也越易误触。
- 本地 VAD 起说：最快；回声、咳嗽、附和都会触发，必须有 AEC 和过滤。
- 转写出字（unmute 任何新词；TEN 部分转写 > 2 字符）：滤掉纯噪声，多一段 ASR 延迟，"嗯"照样出字。
- 整句识别完成（小智服务端设备端 AEC 时）：几乎不误触，但体感是打断不了。
- 上游 `speech_started`：零实现；本地门槛被绕过（LiveKit 开服务端判停时 `allow_interruptions=False` 也不成立）。
- 按键 / 点击 / 文本 / 唤醒词：确定信号，客户端发 `abort` / `interrupt()`。
- 模型内部（moshi）：没有打断事件。

**误打断过滤**（在冲刷之前）：
- 最短时长：LiveKit `min_duration=0.5` s（仓库默认）。代价：每次真打断晚 0.5 s 停。
- 最少字数：LiveKit `min_words`（中文按单字）、Pipecat `MinWordsUserTurnStartStrategy`。
- AEC 预热：LiveKit `aec_warmup_duration=3.0` s、unmute 开头 3 s（仓库默认）。
- 暂停-恢复：输出支持 pause 时先暂停，`false_interruption_timeout=2.0` s 内没回合就 resume。代价：要输出支持 pause，设备深缓冲难做。
- 附和识别：LiveKit `mode="adaptive"`，`backchannel_boundary` 默认发言首尾各 1 s。
- 空转写回合：Pipecat `EmptyUserTurnConfig` 让 LLM 请用户重说。
- 不可打断片段：LiveKit `allow_interruptions=False`、Pipecat 逐帧 `interruptible`。

**冲刷。** LLM 取消在途任务，S2S 发 `response.cancel`（单槽位上游超时未释放就重连）；TTS 清分句器、取消 context，不支持取消的 WebSocket TTS 断开重连；播放队列服务端清空，客户端缓冲靠下行指令（`flush`、`PLAYBACK_CLEAR`、`output_audio_buffer.clear`）。迟到帧两种处理：
- 一次性清空（Pipecat）：简单，处理不了清空后才到的帧（推断，需实测）。
- 代际 / 回合 id 过滤：每次打断或新回合 +1，生产端和消费端按编号判新旧。代价：每个出声来源都要带编号，长生命周期任务每次出声前要重领租约，否则"每个连接只有第一轮有声音"（实践笔记记录的 bug）。代际还用于：迟到识别结果归属、工具结果过期（回 `{status:'superseded'}`；写操作执行前再查一次）、设备只播当前回合。

**截断上下文。**
- 级联词级：TTS 按播放时刻释放带时间戳的词（Pipecat、unmute）。
- 级联估算：按语速匀速推进（LiveKit `synchronized_transcript`，中文按单字）。
- S2S 按播放位置：`conversation.item.truncate{audio_end_ms}`，位置为 0 时 `conversation.item.delete`（LiveKit）。
- S2S 按墙钟：从首个 delta 起算（Pipecat）。代价：没扣播放缓冲，偏大。
- 只记开没开始播（qwen-audio-agent）/ 不截断（小智服务端、TEN、Gemini / AWS 不支持）：等于不截断，指代错位、以为已告知、重连时把没播的内容当历史灌回上游。

播放位置最可靠的来源是设备播放回执。WebRTC 客户端缓冲浅，服务端估计误差小；WebSocket 加设备深缓冲时只能靠设备回报 `played_ms`。

**历史写入。** 只写已播 + 打断标记（LiveKit、Pipecat `interrupted=True`）；只写已播但去标记（unmute 送 LLM 前去掉 `—`）；一帧没播的删掉（LiveKit `skipped`）；全文写入（小智服务端、qwen-audio-agent 已开始播的）。标记写法对模型后续输出的影响没有项目验证过，需实测。

## 推荐

触发：

| 场景 | 选 | 理由 |
|---|---|---|
| 按键设备 | **只认按键**：设备本地立即停播、清缓冲、本地播放代际 +1，再上报播放位置和 `abort`，不等服务端 | 确定信号无需过滤；忽略上游服务端判停的打断事件，否则环境噪声会打断播报 |
| 开放麦、AEC 可靠 | **服务端 VAD 起说 + 过滤**：AEC 预热 3 s、最短时长 0.5 s；噪声大加最少字数（中文先取 2 字，需实测） | LiveKit 默认值是唯一完整实现的起点；客户端不因本地 VAD 自行停播，否则端云状态不一致 |
| 开放麦、AEC 不可靠 | 改按键，或只用整句识别后触发 | 误打断比打断不了更伤体验 |
| S2S 上游开着服务端判停 | 用上游 `speech_started` 发现，cancel、截断、代际走自己的逻辑 | 上游只负责发现，不负责收口 |

误打断恢复：输出支持暂停（WebRTC、本地播放器）就用 LiveKit 暂停-恢复；设备深缓冲硬件先不做，"压低音量"原语需原型验证。

冲刷：**一律用代际，不要只清空队列**。每次新回合或打断 +1 一次，在入队、授权、每个下行帧发出前三处比对；设备再按回合 id 和本地播放代际做第二道过滤。消费端二次过滤丢了帧说明中心过滤漏了，当缺陷看。自托管 TTS 选型把"能否中途停止"当硬指标（cosyvoice、fish-speech、spark-tts 停不下来，要自加停止标志或接受空转）。

截断：
- 级联：TTS 输出带播放时刻的词，只写播到的词；没有字级时间戳就退化为"已完整播放的句 + 当前句按比例"。
- S2S 支持 truncate：`cancel` + `truncate(audio_end_ms)`，`audio_end_ms = offset(句) + played_ms` 以设备回执为准；回执 300 ms 内没到就用服务端估计并在 trace 标出。不要用首个 delta 起的墙钟时间。
- S2S 不支持 truncate（Gemini、AWS）：本地上下文记已播文本，下次同步以本地为准。

历史：写已播部分并**保留显式打断标记**（不学 unmute 去掉标记）；一帧没播的删掉。模型要知道"用户没听完"才会接着问。标记写法在目标模型上验证（会不会被模仿进回复）。工具调用：结果没回传就不再回传，已回传补极短的 `{"interrupted": true}`（实践笔记实测出声率 20/20 对 15/20，40 次打断卡死 0、重复调用 0）。

被打断的播报不重播，视为已送达；必须送达的内容不该走播报。

## 各项目怎么做

- livekit-agents：级联本地 VAD 触发、S2S 用 `input_speech_started`；门槛 + AEC 预热 + 暂停-恢复；`clear_buffer()`，`partial` 按播放位置 truncate、`skipped` 删除，本地标 `interrupted=True`。`voice/agent_activity.py:AgentActivity._interrupt_by_audio_activity`、`voice/turn.py:InterruptionOptions`、`llm/_realtime/openai.py:RealtimeSession.truncate`。服务端镜像未处理 `truncated` 事件（推断）。
- pipecat：广播 `InterruptionFrame`，各 processor 重建 task；级联按 pts 词级截断；OpenAI Realtime 按墙钟 truncate，无代际过滤。`processors/frame_processor.py:FrameProcessor.broadcast_interruption`、`services/tts_service.py:TTSService._handle_interruption`、`transports/base_output.py:MediaSender.handle_interruptions`、`services/openai/realtime/llm.py:_truncate_current_audio_response`。
- pipecat-flows：函数 `cancel_on_interruption` 默认 False，插话不取消函数。`types.py:FlowsFunctionSchema`。
- ten-framework：部分转写 > 2 字符触发，`flush_llm` → `tts_flush` → 给 `agora_rtc` 发 `flush`，TTS 按 `request_id` 丢迟到文本；不截断。`ex/voice-assistant/.../main_python/extension.py:MainControlExtension._interrupt`。
- qwen-audio-agent：三个入口动作一致：回合代际 +1、`PLAYBACK_CLEAR`、响应队列代际 +1 并 `response.cancel`；过期工具调用回 `superseded`；不发 truncate。`RealtimeInputRuntime.#startSpeech`、`RealtimeResponseSlot.recover`。
- xiaozhi-esp32：按键 / 唤醒词发 `abort`，`playback_generation_` 拒旧包；无播放回执。`Application::AbortSpeaking`、`main/audio/audio_service.cc:AudioService`。
- xiaozhi-esp32-server：`abort` 置 `client_abort`、清队列；每轮 uuid（`sentence_id`）丢旧音频；全文写历史。`abortHandle.py:handleAbortMessage`、`receiveAudioHandle.py:startToChat`。
- unmute：新词或停顿概率 < 0.4（会话超 3 s）打断；替换输出队列、历史长度当代际。`unmute/unmute_handler.py:UnmuteHandler.interrupt_bot`。

## 相关

- 架构：02-architectures/floor-control.md（代际、租约、冲刷规则，§3、§6）、02-architectures/turn-model.md（打断帧序列、`interrupted` 终态、`turn_id` 门控）、02-architectures/full-duplex.md、02-architectures/state-and-context.md、02-architectures/s2s.md
- 基础：01-foundations/vad.md、01-foundations/transport.md（客户端缓冲深度）、01-foundations/tts.md
- 机制：03-mechanisms/turn-detection.md、03-mechanisms/tool-calls.md（打断时工具收口）、03-mechanisms/audio-preprocessing.md、03-mechanisms/session-recovery.md、03-mechanisms/evaluation.md
