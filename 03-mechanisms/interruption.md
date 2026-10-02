# 打断与截断：按实际播放位置收口

> 状态：草稿
> 最后更新：2026-10-02

## 问题是什么

agent 正在说话，用户开口了（或者按了键、点了屏幕、打了字）。这时系统要依次做完五件事：

1. **判定这是打断**，而不是回声、咳嗽、附和（"嗯嗯"）或背景人声。
2. **停播**：扬声器尽快安静下来。
3. **冲刷**：让 LLM、TTS、上游 S2S 停止生成，丢掉队列里和**之后才到**的旧帧。
4. **截断上下文**：模型以为自己说完了整段回复，用户其实只听到一部分。上下文要改成"用户实际听到的部分"。
5. **决定被打断的回复怎么记**：写不写进历史、写多少、带不带"被打断"标记。

任何一步做不好都有明显后果：

- 误打断：agent 被自己的回声或听众的附和打断，一句话说不完。
- 冲刷不干净：双声，或者旧回合的音频在新回合开头冒出来。
- 不截断：模型列了三个选项、只播出一个就被打断，用户说"第二个"，模型按自己"说过"的全文去理解；或者模型认为某件事已经告知用户，后面不再提。
- 工具调用悬着：一份实践笔记实测，悬着的调用会让模型在后续几轮里反复调同一个工具（闲聊时约 21%，打断后更高）。

一次开放麦打断的时间线（各段是否存在、多长，取决于下文的选择）：

```
 t0 用户开口
  │  ← 过滤：AEC 预热期？有声够 0.5 s？转写够 N 字？是不是附和？
 t1 确认打断 ─► 代际 +1
  │  ├─ 下行：发停播 / flush 指令 ─► 设备清缓冲、停播 ─► 回报 played_ms
  │  ├─ LLM / TTS：取消任务、清分句器
  │  └─ 上游 S2S：response.cancel
 t2 扬声器静音（体感上的"打断延迟" = t2 − t0）
  │  ← 迟到帧：cancel 之后仍到达的 delta、TTS 残包，按代际丢弃
 t3 拿到播放位置 ─► truncate(audio_end_ms) / 改写本地 assistant 消息
 t4 旧回合终态 interrupted；新回合继续收音
```

打断本身是两个架构决策的交汇点：谁有权出声、旧输出怎么过滤属于 [floor-control](../02-architectures/floor-control.md)；打断在协议上的帧序列和回合终态属于 [turn-model](../02-architectures/turn-model.md)。本页讲每一步有哪些做法、各项目怎么做，不重复那两页的规则。

## 解法分类

### 1. 触发：谁先知道用户要插话

| 触发源 | 做法 | 例子 |
|---|---|---|
| 本地 VAD 起说 | agent 说话期间 VAD 判到有声 | LiveKit 级联、TEN 的 ten-vad 示例、小智服务端 AEC 路径 |
| 转写出字 | STT 吐出新词，或部分转写超过若干字 | unmute（任何新词）、TEN 默认（部分转写 > 2 字符）、Pipecat `TranscriptionUserTurnStartStrategy` |
| 整句识别完成 | 判停 + ASR 完成后才打断 | 小智服务端 auto / realtime（设备端 AEC 时） |
| 上游服务端事件 | S2S 上游发 `speech_started` | LiveKit / Pipecat / qwen-audio-agent / TEN 的 S2S 路径 |
| 按键、点击、文本、唤醒词 | 客户端显式发 `abort` / `interrupt()` | 小智固件、openai-realtime-agents 按键模式、qwen-audio-agent 手动文本 |
| 模型内部 | 没有打断事件，模型同一步就"听到"用户，自己决定停不停 | moshi |

越早的触发越灵敏，也越容易误触；越晚的触发（整句识别完成）越准，但用户要说完一整句 agent 才停，体感上就是"打断不了"。

### 2. 误打断过滤

在冲刷之前完成，常见手段：

- **最短时长**：有声持续不够长不算打断（LiveKit `min_duration=0.5` s）。
- **最少字数**：有 STT 时转写出足够的词才算（LiveKit `min_words`，中文按单字切；Pipecat `MinWordsUserTurnStartStrategy`）。
- **AEC 预热**：会话开头一段时间不允许声学打断，等回声消除收敛（LiveKit `aec_warmup_duration` 默认 3.0 s；unmute 开头 3 s，`UNINTERRUPTIBLE_BY_VAD_TIME_SEC`）。
- **false interruption 恢复**：LiveKit 在输出支持暂停时，声学打断先 `audio_output.pause()`，用户回合真正提交时才打断；`false_interruption_timeout=2.0` s 内用户没说出东西就 `resume()` 接着播（`voice/agent_activity.py:AgentActivity._interrupt_by_audio_activity`、`_start_false_interruption_timer`）。
- **附和语识别**：LiveKit `mode="adaptive"` 用 `AdaptiveInterruptionDetector` 区分真打断和附和、重叠；`backchannel_boundary` 默认在每段发言首尾各 1 s 内压掉被判为附和的重叠说话。
- **空转写回合**：Pipecat 的 `EmptyUserTurnConfig`，打断了 bot 却没有转写的回合（咳嗽、噪声）默认让 LLM 跑一次，请用户重说或接着讲。
- **不可打断片段**：LiveKit `allow_interruptions=False` / `RunContext.disallow_interruptions()`；Pipecat 逐帧 `interruptible` 标志。

### 3. 冲刷：三段各自怎么停

| 段 | 做法 |
|---|---|
| LLM | 取消在途流式请求的任务（Pipecat 重建处理 task、unmute 取消 quest、TEN `flush_llm`、小智每个 chunk 检查 `client_abort`）；S2S 发 `response.cancel`，单槽位上游超时未释放就断开重连（qwen-audio-agent） |
| TTS | 清分句器和待合成文本，取消音频 context（Pipecat `TTSService._handle_interruption`）；不支持取消的 WebSocket TTS 断开重连（`InterruptibleTTSService`）；双流式云 TTS 调 `cancel_session`（小智）；自托管 TTS 能否中途停，取决于模型实现（见表中 TTS 各行） |
| 播放队列 | 服务端发送队列清空（Pipecat `MediaSender.handle_interruptions`、小智 `clear_queues`、LiveKit `clear_buffer()`）；客户端缓冲要靠下行指令或本地停播（TEN 给 `agora_rtc` 发 `flush`、qwen 发 `PLAYBACK_CLEAR`、OpenAI WebRTC 发 `output_audio_buffer.clear`） |

**迟到帧**是冲刷最容易漏的地方：上游 cancel 之后、TTS 取消之后，还可能有帧在路上。两种处理思路：

- **一次性清空**：打断瞬间清掉所有队列里的可打断帧。实现简单，但处理不了清空之后才到的帧（Pipecat）。
- **代际 / 回合 id 过滤**：每次打断或新回合编号 +1，生产端和消费端都按编号判新旧，任何时候到达的旧帧都能丢。显式编号有 qwen-audio-agent 的响应代际、TEN TTS 的 `request_id`、小智服务端每轮生成的 uuid（代码里叫 `sentence_id`）和固件的 `playback_generation_`；unmute 用"生成开始时的历史长度"当隐式代际，并在打断时**换掉输出队列**，旧协程只能写进没人读的旧队列。

代际在哪几处比较、什么时候 +1，规则见 [floor-control](../02-architectures/floor-control.md) §3。

### 4. 代际 / turn_id 在打断里的作用

打断是代际最主要的使用场合。它解决的不只是"旧音频"：

- **丢迟到帧**：上游 cancel 之后仍推来的音频 delta、TTS 在取消前已合成的残包、设备解码队列里等空位的旧包。
- **迟到识别结果归属**：上一回合的 final 转写在新回合开始后才到，按回合 id 归回旧回合或丢弃，不会被当成新回合的输入（[turn-model](../02-architectures/turn-model.md) §2.1）。
- **工具结果过期**：工具返回时检查代际，不通过就不回传、不出声。qwen-audio-agent 对过期回合的工具调用回 `{status:'superseded'}` 收口；一份实践笔记要求写操作类工具在执行前再检查一次是否已被打断。
- **设备只播当前回合**：下行帧带 `turn_id`，设备丢掉非当前回合的帧；设备内部再用本地播放代际防"同一回合 abort 后的残余包"，小智固件的 `playback_generation_` 就是这一层。
- **长生命周期任务**：事件分发循环、后台任务的回帖协程每次出声前要按当前代际重新领取租约，不能缓存。

没有显式编号的项目靠别的东西兜：unmute 用历史长度，小智服务端用每轮 uuid，Pipecat 只靠打断瞬间清空队列。

### 5. 截断：按实际播放位置修正上下文

| 粒度 | 做法 | 例子 |
|---|---|---|
| 级联、词级 | TTS 按播放时刻释放带时间戳的词，只有播到的词进 assistant 消息 | Pipecat（按 pts 释放 `TTSTextFrame`）、unmute（`TextToSpeech.__aiter__` 按 `start_s` 排队） |
| 级联、估算 | 文本和音频同步器按语速匀速推进，算出"已播文本" | LiveKit `synchronized_transcript`（中文按单字切） |
| S2S、按播放位置 truncate | 按播放器回报的位置发 `conversation.item.truncate{audio_end_ms}`；位置为 0 时删除条目 | LiveKit（`playback_position` 来自播放器） |
| S2S、按墙钟估算 truncate | 从收到首个音频 delta 起算墙钟时间，`audio_end_ms = min(墙钟经过时间, 已收音频时长)` | Pipecat（没扣播放缓冲，偏大） |
| 只记"开没开始播" | 收到 `playback.started` 才写转写，开始播了就写全文 | qwen-audio-agent |
| 不截断 | 上下文保留全文 | 小智服务端、TEN（S2S 的 truncate 代码被注释掉）、Gemini / AWS（上游不支持） |

不截断的后果是具体的，不只是"上下文不准"：

- **指代错位**：模型说"有三条路线：A、B、C"，只播到 A 就被打断，用户说"第二个吧"，模型按全文理解成 B，用户其实不知道 B 是什么。
- **以为已告知**：注意事项、价格、确认问题在没播出的部分里，模型后面不会再说。
- **重连回放全文**：上下文镜像里存的是全文，断线重建会话时把用户从没听到的内容当作历史灌回上游（LiveKit 的推断风险，见下文"各解法的代价"）。

OpenAI Realtime 的截断原语在 LiveKit 里的落法可作参考（`llm/_realtime/openai.py:RealtimeSession.truncate`）：

- 音频模态：发 `conversation.item.truncate{item_id, audio_end_ms}`，服务端按音频位置裁掉转写。
- 播放位置为 0：改发 `conversation.item.delete`，整条删掉。
- 文本模态：用已播文本"删了再建"这条 item。

播放位置最可靠的来源是**设备的播放回执**。WebRTC 客户端缓冲很浅，服务端用"已发出时长"估计误差小；WebSocket 加设备深缓冲时，服务端估计会明显偏大，只能靠设备回报 `played_ms`。

### 6. 被打断的回复写不写进历史

- **只写已播部分，带打断标记**：LiveKit（`interrupted=True`）、Pipecat assistant 聚合器（`interrupted=True`）。
- **只写已播部分，去掉标记**：unmute 在消息里追加打断符 `—`，送 LLM 前 `preprocess_messages_for_llm` 又去掉，模型看不到自己被打断。
- **一帧都没播的删掉**：LiveKit 把 `skipped` 的消息用 `update_chat_ctx` 从服务端删除。
- **全文写入**：小智服务端（无标记）、qwen-audio-agent（已开始播放的）、TEN S2S（上游保留全文，只在字幕追加 `[interrupted]`）。

工具调用在打断时的收口另有规则：结果没回传就不再回传，已回传则补一个极短的 `{"interrupted": true}`。一份实践笔记实测，补与不补的出声率是 20/20 对 15/20，40 次打断卡死 0、重复调用 0。详见 [tool-calls](tool-calls.md)。

## 各解法的代价

**打断延迟**（t2 − t0）由几段串起来：触发源本身的延迟（VAD 起说确认几十毫秒；转写出字要加 ASR 延迟；整句识别要等用户说完）+ 过滤门槛（最短时长 0.5 s 就是硬加 0.5 s）+ 服务端到设备的往返 + 设备缓冲里已解码音频的长度。按键设备本地先停播，可以把后两段压到一帧加 DAC 缓冲；开放麦下判停和打断放在服务端，代价是停播要多等一个往返加过滤时长。

| 选择 | 收益 | 代价 / 失败模式 |
|---|---|---|
| VAD 起说触发 | 最快，用户一开口就停 | 回声、咳嗽、附和都会触发；必须有 AEC 和过滤 |
| 转写出字触发 | 过滤掉纯噪声 | 多一段 ASR 延迟；"嗯"这类附和照样会出字 |
| 整句识别后触发 | 几乎不误触 | 用户要说完一句 agent 才停，体感是打断不了 |
| 服务端事件触发 | 零实现 | 参数受上游控制；本地的打断门槛被绕过（LiveKit 开服务端判停时直接忽略本地音频打断，`allow_interruptions=False` 也不成立） |
| 最短时长 0.5 s | 挡掉短噪声 | 每次真打断都晚 0.5 s 停 |
| 暂停-恢复 | 误打断不丢内容 | 需要输出支持 pause；设备深缓冲下难做；恢复后用户可能已错过上下文 |
| 一次性清空 | 简单 | 迟到帧会被当成新回复播出（Pipecat 读代码推断，是否真会发生取决于上游 cancel 后还发不发 delta，待确认） |
| 代际过滤 | 任何时刻到达的旧帧都能判 | 每个出声来源都要带编号；长生命周期任务要每次重领租约，否则会出现"每个连接只有第一轮有声音"（一份实践笔记记录的 bug） |
| 按回执截断 | 上下文和用户听到的一致 | 设备要实现播放回执；回执超时要有估算兜底 |
| 按墙钟截断 | 不依赖设备 | 没扣播放缓冲，`audio_end_ms` 偏大，模型以为用户多听了一截 |
| 不截断 | 零实现 | 模型以为用户听到了全文：指代错位、该说的不再说；LiveKit 推断还有一层：服务端镜像没处理 `conversation.item.truncated`，重连重放时被截断的回复可能以全文回到上游（待确认） |
| 写已播 + 标记 | 模型知道自己被打断，可以接着问"你想说什么" | 标记写法各家不同，对模型后续输出的影响没有项目验证过（待确认） |
| 全文写入 | 简单 | 等于不截断 |

## 各项目怎么做

| 项目 | 做法 | 代码路径 | 备注 |
|---|---|---|---|
| [livekit-agents](../04-projects/frameworks/livekit-agents.md) | 级联由本地 VAD 触发，S2S 开服务端判停时由 `input_speech_started` 触发；门槛 `min_duration` / `min_words` / adaptive / AEC 预热；支持暂停的输出先暂停、2 s 无回合则恢复；打断时 `clear_buffer()`，按播放器回报记 `partial` / `skipped`，`partial` 调 `rt_session.truncate(audio_end_ms=播放位置)`，`skipped` 从服务端删除；本地消息只写已转发文本并标 `interrupted=True` | `voice/agent_activity.py:AgentActivity._interrupt_by_audio_activity`、`_on_input_speech_started`、`voice/turn.py:InterruptionOptions`、`voice/generation.py:forward_generation`、`llm/_realtime/openai.py:RealtimeSession.truncate` | 中文已播文本按单字匀速估算；Gemini、AWS 不支持截断，只改本地；服务端镜像未处理 `truncated` 事件（推断） |
| [pipecat](../04-projects/frameworks/pipecat.md) | 用户回合开始即广播 `InterruptionFrame`；各 processor 重建处理 task、丢可打断帧；TTS 清缓冲取消 context；输出端清音频和时钟队列；级联按 pts 词级截断；OpenAI Realtime 按墙钟发 truncate，Gemini Live 不截断 | `processors/frame_processor.py:FrameProcessor.broadcast_interruption`、`_start_interruption`、`services/tts_service.py:TTSService._handle_interruption`、`transports/base_output.py:MediaSender.handle_interruptions`、`services/openai/realtime/llm.py:_truncate_current_audio_response` | 没有代际或回合 id 过滤迟到帧；`_handle_evt_audio_delta` 不比对已取消的 `response_id`（推断） |
| [pipecat-flows](../04-projects/frameworks/pipecat-flows.md) | 沿用 pipecat；特有的是函数 `cancel_on_interruption` 默认 False，插话不取消函数，边函数仍完成节点切换 | `types.py:FlowsFunctionSchema`、`FlowsDirectFunctionWrapper._initialize_metadata` | 在独立包锁定的 pipecat 1.4.x 上的确切行为待确认 |
| [ten-framework](../04-projects/frameworks/ten-framework.md) | 级联：部分转写 > 2 字符（或 VAD 起说、判停扩展的 `flush`）触发；`_interrupt` 依次 `flush_llm`、`tts_flush`、给 `agora_rtc` 发 `flush`；TTS 按 `request_id` 丢迟到文本。S2S：上游 `speech_started` → 只 flush 播放 | `ex/voice-assistant/.../main_python/extension.py:MainControlExtension._interrupt`、`_on_asr_result`、`openai_mllm_python/extension.py:start_connection` | 不截断上下文；S2S 的 truncate 被注释掉；`agora_rtc` flush 的行为（预编译包）待确认 |
| [openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md) | 语音打断交给服务端 VAD；按键按下、发文本时 `interrupt()`：WebRTC 下只发 `response.cancel` + `output_audio_buffer.clear`，不发 truncate；收到 `conversation.item.truncated` 时 retrieve 刷新本地镜像 | SDK `openaiRealtimeWebRtc.mjs:OpenAIRealtimeWebRTC.interrupt` | 服务端是否按播放位置自动截断待确认；委派中被打断时 `fetch` 不取消，过时答案可能被念出来（推断） |
| [qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md) | 服务端 `speech_started`、手动文本、客户端 `interrupt` 三个入口动作一致：回合代际 +1、关闭播报窗口、发 `PLAYBACK_CLEAR`、响应队列代际 +1 并 `response.cancel`；单槽位上游超时则重连；取消期间的事件标 `suppressed` | `RealtimeInputRuntime.#startSpeech`、`RealtimeResponseSlot.recover`、`announcement-manager.mjs:AnnouncementManager.dismissActive` | 不发 truncate；收到 `playback.started` 才写转写，开始播了就写全文；过期回合的工具调用回 `{status:'superseded'}` |
| [xiaozhi-esp32](../04-projects/devices/xiaozhi-esp32.md) | 按键、点击、唤醒词都发 `abort`；按键路径切到 Listening 并 `ResetDecoder`，`playback_generation_` 拒掉等队列空位的旧包 | `Application::AbortSpeaking`、`main/audio/audio_service.cc:AudioService` | 点击只发 `abort` 不切状态，已缓冲的最多约 1.2 s 音频可能播完（推断，待实机确认）；没有播放回执 |
| [xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md) | `abort` → 置 `client_abort`、清队列、回 `tts stop`；语音打断在判停 + ASR 完成后才发生，设备声明服务端 AEC 时 VAD 有声即打断；每轮 uuid（`sentence_id`）丢旧音频 | `abortHandle.py:handleAbortMessage`、`receiveAudioHandle.py:startToChat`、`sendAudioHandle.py` | 全文写入历史，无打断标记；manual 模式不做语音打断 |
| [unmute](../04-projects/full-duplex/unmute.md) | 机器人说话时 STT 出任何新词，或停顿概率 < 0.4（会话已超 3 s）就打断；`interrupt_bot` 清 FastRTC 队列、替换输出队列、取消 TTS / LLM；历史长度当代际 | `unmute/unmute_handler.py:UnmuteHandler.interrupt_bot`、`_stt_loop`、`receive` | assistant 消息只含已开始播放的词；打断符送 LLM 前去掉；不支持附和 |
| [moshi](../04-projects/full-duplex/moshi.md) | 没有打断事件，用户音频照常进模型，由模型决定停不停 | `lm.py:LMGen._step` | 没有截断概念，被打断的内容都在模型的 token 缓存里 |
| [ultravox](../04-projects/e2e-models/ultravox.md) | 模型不涉及；上层取消文本流和 TTS | — | 助手历史是普通文本，截到已播位置写回即可 |
| [step-audio2](../04-projects/e2e-models/step-audio2.md) | 模型不涉及；上层取消 HTTP 流、停 token2wav | — | 历史存 `tts_content`（文本 + 音频 token），理论上可只写回已播部分，仓库没有示例，待确认 |
| [qwen3-omni](../04-projects/e2e-models/qwen3-omni.md) | 不涉及，请求-响应式；demo 的 Stop 只取消 Gradio 事件 | `web_demo.py` | |
| [webrtc-audio-processing](../04-projects/audio-processing/webrtc-audio-processing.md) | 间接：开放麦下能否"说话即打断"取决于端侧 AEC；收敛前的残余回声会造成误打断 | — | APM 不做打断逻辑 |
| [aiortc](../04-projects/audio-processing/aiortc.md) | 间接：Pipecat SmallWebRTC 的 `RawAudioTrack` 每次只交出 10 ms，服务端清队列即可；数据通道可传打断信号 | — | |
| [smart-turn](../04-projects/turn-vad/smart-turn.md) / [ten-vad](../04-projects/turn-vad/ten-vad.md) | smart-turn 只判回合结束，不涉及打断；ten-vad 的"开始说话"在 TEN 示例里用来触发打断 | — | |
| [funasr](../04-projects/asr/funasr.md) / [sensevoice](../04-projects/asr/sensevoice.md) / [sherpa-onnx](../04-projects/asr/sherpa-onnx.md) | 不涉及；VAD 段开始（`slice_type:0`、`speech_started`、`IsSpeechDetected`）可被上层当 barge-in 信号 | — | [fireredasr](../04-projects/asr/fireredasr.md) 不涉及 |
| [cosyvoice](../04-projects/tts/cosyvoice.md) / [fish-speech](../04-projects/tts/fish-speech.md) | 没有取消接口，停止迭代或客户端断开后后台仍生成到结束（推断） | `cosyvoice/cli/model.py:CosyVoice2Model.tts`、`fish_speech/models/text2semantic/inference.py:launch_thread_safe_queue` | 要中途停需自己加停止标志；没有字级时间戳 |
| [fireredtts2](../04-projects/tts/fireredtts2.md) / [voxcpm](../04-projects/tts/voxcpm.md) / [index-tts](../04-projects/tts/index-tts.md) | 同步 generator，停止迭代即停，粒度分别约 80 ms、约 160 ms、整段（推断） | `fireredtts2/fireredtts2.py:FireRedTTS2_Stream.generate`、`voxcpm2.py:VoxCPM2Model._inference`、`indextts/infer_v2_5.py:IndexTTS2.infer_generator` | voxcpm 有事后对齐的字级时间戳（非流式）；fireredtts2 可按固定块长换算已播时长 |
| [spark-tts](../04-projects/tts/spark-tts.md) | 本地一次性 `generate`，生成中无法中止 | `cli/SparkTTS.py:SparkTTS.inference` | Triton 流式能否取消待确认 |

## 我们的判断

**触发**

| 场景 | 选 | 理由 |
|---|---|---|
| 按键设备 | **只认按键**。设备本地立即停播、清缓冲、本地播放代际 +1，再上报播放位置和 `abort`，不等服务端 | 按键是确定信号，不需要过滤；忽略上游服务端判停的打断事件，否则环境噪声会打断播报 |
| 开放麦、有可靠 AEC | **服务端 VAD 起说 + 过滤**：AEC 预热 3 s、最短时长 0.5 s；噪声大的场景加最少字数（中文先取 2 字，未实测） | LiveKit 默认值是唯一有完整实现的起点；判停和打断都放服务端，客户端不因本地 VAD 自行停播，否则端云状态不一致 |
| 开放麦、AEC 不可靠 | 改按键，或只用"整句识别后"触发 | 误打断比打断不了更伤体验 |
| S2S 上游开着服务端判停 | 用上游的 `speech_started`，但 cancel、截断、代际仍走自己的逻辑 | 上游只负责发现，不负责收口 |

误打断恢复：输出支持暂停（WebRTC、本地播放器）就用 LiveKit 的暂停-恢复；设备深缓冲的硬件先不做，是否引入"压低音量"原语待原型验证。

**冲刷**：一律用**代际**，不要只"清空队列"。每次新回合或打断 +1 一次，入队、授权、每个下行帧发出前三处比对；设备再按回合 id 和本地播放代际做第二道过滤。消费端二次过滤丢了帧，说明中心过滤漏了，当缺陷看。自托管 TTS 选型时把"能否中途停止"当硬指标：cosyvoice、fish-speech、spark-tts 这类停不下来的，要自己加停止标志或接受空转。

**截断**

- 级联：TTS 输出带播放时刻的词，只把播到的词写进 assistant 消息（Pipecat / unmute 的做法）。没有字级时间戳的 TTS，退化为"已完整播放的句 + 当前句按比例"。
- S2S 支持 truncate：`cancel` + `truncate(audio_end_ms)`，`audio_end_ms` 以设备回执为准（`offset(句) + played_ms`）；回执 300 ms 内没到就用服务端估计并在 trace 标出。不要用"收到首个 delta 起的墙钟时间"。
- S2S 不支持 truncate（Gemini、AWS 等）：本地上下文记已播文本，下一次同步上下文时以本地为准。

**历史写入**：写已播部分，并**保留显式打断标记**（LiveKit、Pipecat 的做法，不学 unmute 去掉标记）；一帧没播的删掉。理由是模型需要知道"用户没听完"，才会接着问而不是假设已告知。标记的写法要在目标模型上验证（例如会不会被模仿进回复）。工具调用按收口规则补结果。

**被打断的播报不重播**，视为已送达；一定要让用户知道的内容不该走播报（见 [floor-control](../02-architectures/floor-control.md) §6）。

## 相关

- 架构层：[floor-control](../02-architectures/floor-control.md)（代际、租约、冲刷规则、按种类冲刷）、[turn-model](../02-architectures/turn-model.md)（打断帧序列、`interrupted` 终态、`turn_id` 门控）、[full-duplex](../02-architectures/full-duplex.md)（可打断的半双工、AEC 与误打断）、[state-and-context](../02-architectures/state-and-context.md)（被打断的输出怎么进上下文）、[s2s](../02-architectures/s2s.md)
- 基础：[vad](../01-foundations/vad.md)（start 阈值与打断灵敏度）、[transport](../01-foundations/transport.md)（客户端缓冲深度决定停播延迟和位置估计误差）、[tts](../01-foundations/tts.md)
- 其他机制：[turn-detection](turn-detection.md)、[tool-calls](tool-calls.md)（打断时工具调用的收口）、[audio-preprocessing](audio-preprocessing.md)（AEC 放在哪）、[session-recovery](session-recovery.md)（重连时被截断的回复别以全文回放）、[evaluation](evaluation.md)（打断时的调用状态、截断是否估算进 trace）
- 项目页：[livekit-agents](../04-projects/frameworks/livekit-agents.md)、[pipecat](../04-projects/frameworks/pipecat.md)、[pipecat-flows](../04-projects/frameworks/pipecat-flows.md)、[ten-framework](../04-projects/frameworks/ten-framework.md)、[openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md)、[qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md)、[unmute](../04-projects/full-duplex/unmute.md)、[moshi](../04-projects/full-duplex/moshi.md)、[xiaozhi-esp32](../04-projects/devices/xiaozhi-esp32.md)、[xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md)
