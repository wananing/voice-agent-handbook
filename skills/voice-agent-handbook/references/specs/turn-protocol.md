# 设计模板：回合模型与设备协议

## 目录

1. 解决什么问题
2. 核心模型（2.1 回合状态机 / 2.2 `turn_id` 与代际 / 2.3 边界来源与两种模式 / 2.4 回合终态 / 2.5 上行消息 / 2.6 下行消息 / 2.7 音频帧头 / 2.8 打断时序 / 2.9 断线续接）
3. 规则
4. 可调参数
5. profile 差异
6. 验收清单
7. 产出骨架时的注意事项

## 1. 解决什么问题

设备（或客户端）和网关之间要对三个问题只有一种答案：这一轮结束了没有、用户实际听到了哪里、打断后还会不会冒出旧音频。按键说话和开放麦是同一个回合状态机的两种配置，差别只在边界来源。本协议定义回合状态机、控制帧与音频帧头、下行节奏、打断与断线续接的时序。输出由谁出声见 output-arbiter.md，工具调用的收口见 tool-closure.md。

## 2. 核心模型

### 2.1 回合状态机

网关持有权威状态；客户端只维护镜像，用于门控下行音频和呈现（灯效、通话态 UI）。

| 状态 | 含义 | 进入事件 | 转移 | 动作 |
|---|---|---|---|---|
| `idle` | 无进行中的用户回合 | 会话建立；上一回合 `turn.closed` | `turn.start` → `listening` | 代际 +1（见 2.2） |
| `listening` | 正在收用户音频 | `turn.start` | `turn.end` → `holding`（仅 ptt 且判"未说完"）或 `committed`；`T_listen_max` → 强制 `turn.end{reason:"max_duration"}` | 转发上行音频 |
| `holding` | 边界已结束，留补充窗口 | `turn.end` 且语义判停 P(完成) < τ | 窗口内新 `turn.start` → 合并回 `listening`（发 `turn.merge`）；`T_hold` 到期 → `committed` | 不提交上游 |
| `committed` | 已向上游提交，等识别定稿和首个输出 | 提交 | 首个出声输出 → `responding`；过滤 → `discarded`；`T_asr` / `T_respond` 到期 → `failed`；`T_filler` 到期 → 插 cue，不转移 | 启动看门狗 |
| `responding` | 下行音频进行中（speech 或 cue） | 首个 `tts.start` | `tts.stop{done}` → `draining`；打断 → `interrupted` | 限速下发 |
| `draining` | 网关已发完，等客户端播完 | `tts.stop{reason:"done"}` | 最后一句 `playback.ended` 或 `T_drain` → `completed` | 等回执 |
| 终态 | 见 2.4 | — | — | 发 `turn.closed` |

任一非终态遇到打断、过滤、错误、取消、看门狗，直接进对应终态。状态只沿表中箭头前进；`holding → listening` 是同一回合的合并，不是新回合。

事件播报（到点提醒、后台结果）也用回合承载：网关分配 `s<n>`，下发 `turn.start{source:"event", kind:"narration"}`，从 `committed` 起步。

### 2.2 `turn_id` 与代际

- 格式：文本 `c<n>`（客户端生成）/ `s<n>`（网关生成），`n` 为会话内按来源单调递增的 31 位整数，续接后接着编。帧头里编码为 `uint32`：最高位 0 = 客户端、1 = 网关，低 31 位 = `n`；开放麦且无回合时为 0。
- 谁检测到边界开始谁生成：按键、唤醒词、文本输入由客户端生成；VAD 起说和事件播报由网关生成。
- 代际 `gen`：网关侧单调递增整数。+1 只有三种情况：新用户回合开始、用户打断、critical 抢占（见 output-arbiter.md）。打断与新回合同时发生只 +1 一次；被合并的 `turn.start` 不 +1。回合先后以 `gen` 为准，不比较 `c` 与 `s` 的数值。
- 播放代际 `play_gen`：设备本地的二级代际，不上协议（见 2.8）。

### 2.3 边界来源与两种模式

| 来源 `source` | 谁发 `turn.start` | 谁发 `turn.end` |
|---|---|---|
| `button` | 客户端 key-down | 客户端 key-up |
| `wake` | 客户端（唤醒词命中，带 `wake_word` 和置信度） | 网关判停，`source:"vad"` |
| `vad` | 网关（开放麦起说），下行告知 | 网关判停 |
| `text` | 客户端（`input`: `typed` / `tap` / `test`） | 同一帧隐含结束 |
| `event` | 网关（仅下行，事件播报回合） | — |

| 配置项 | 按键模式 `ptt` | 开放麦模式 `open_mic` |
|---|---|---|
| 边界来源 | `button`（`text` 恒可用） | `wake` / `vad`（`button` 仍可作强制边界和打断） |
| 网关 VAD 职责 | 不判停；做停顿压缩、短按过滤、补充窗口 | 起说 / 判停（VAD 静音约 200 ms + 语义判停 + 自适应等待） |
| 打断信号 | 按键 | VAD（前提：客户端 AEC 在播放期间常开） |
| 上游服务端判停 | 能关就关；关不掉用"只切段不自动回复"兜住 | 同左，网关自己判停 |
| `holding` | 启用 | 不启用 |

模式由 `hello` 协商、`listen.open{mode}` 声明。`listen.open / close` 是会话级的麦克风上行窗口（开放麦下一个窗口跨多个回合），`turn.*` 是"这一次输入"，两者不要混用。网关内部另有判停进行中事件 `turn.endpointing{turn_id, active}`（不上线），供仲裁器把"判停未决"视为用户在说。

### 2.4 回合终态

| 终态 | 含义 | 典型触发 | `by` 取值示例 | 客户端动作 |
|---|---|---|---|---|
| `completed` | 输出播完 | 最后一句 `playback.ended`；`T_drain` 到期时带 `receipt_missing:true` | — | 回 `idle` |
| `interrupted` | 被用户或新回合打断 | `abort`、`playback.interrupted`、新 `turn.start`、网关 VAD | `button` / `vad` / `text` / `link` | 已本地停播 |
| `discarded` | 不是有效输入，零出声 | 短按 < 250 ms、短语音 < 150 ms、误唤醒、规则判定不回应 | `short_press` / `short_speech` / `false_wake` / `rule_silence` | 无屏设备可播极轻提示 |
| `failed` | 无法完成 | 先发 `error{code}` 再发 `turn.closed`；断链时客户端本地判定 | 错误码 | 无屏设备必须发声（本地预合成话术） |
| `cancelled` | 主动结束 | `abort{reason:"user_cancel"}`、`listen.close`、挂断、会话关闭 | — | 无声结束 |

`turn.closed` 带 `state`、`by`、`heard_ms`、`segments`。错误码：`no_speech`、`asr_timeout`、`upstream_unavailable`、`upstream_timeout`、`upstream_rejected`、`tts_failed`、`audio_gap`、`link_lost`、`protocol_error`、`session_expired`（`fatal:true`）、`internal`。

### 2.5 上行消息（客户端 → 网关）

WebSocket 文本帧 = JSON 控制消息，二进制帧 = 音频。WebRTC 下音频走 RTP，控制消息走可靠有序 data channel，JSON 相同。所有 JSON 带 `type`；时间字段后缀 `_ms` 或 `_samples`。

```json
{ "type": "hello", "proto": "va/1", "profile": "A",
  "client": { "model": "<device-model>", "fw": "1.4.2" },
  "caps": { "transport": "ws", "aec": "device", "modes": ["ptt", "open_mic"],
            "sources": ["button", "wake", "text"], "receipts": ["started", "ended", "interrupted"],
            "audio_up": { "codec": "opus", "rate": 16000, "channels": 1, "frame_ms": 20 },
            "audio_down": { "codecs": ["opus"], "rates": [16000, 24000] },
            "play_buffer_max_ms": 1200, "preroll_ms": 250, "uninterruptible": true,
            "events": ["timer", "sensor"], "commands": ["light", "volume"] },
  "resume": null }

{ "type": "listen.open", "mode": "open_mic", "reason": "wake" }
{ "type": "listen.close", "reason": "idle_timeout" }
{ "type": "turn.start", "turn_id": "c42", "source": "button", "ts_samples": 1843200, "preroll_ms": 250 }
{ "type": "turn.end",   "turn_id": "c42", "source": "button", "ts_samples": 1891200, "reason": "release" }
{ "type": "turn.start", "turn_id": "c43", "source": "text", "text": "<用户输入>", "input": "typed" }
{ "type": "abort", "turn_id": "c41", "reason": "button" }
{ "type": "playback.started",     "turn_id": "c41", "sentence_id": 0, "played_ms": 0,   "ts_ms": 51234 }
{ "type": "playback.ended",       "turn_id": "c41", "sentence_id": 2, "played_ms": 1830, "ts_ms": 55012 }
{ "type": "playback.interrupted", "turn_id": "c41", "sentence_id": 1, "played_ms": 640, "ts_ms": 52890, "reason": "button" }
{ "type": "event", "id": "ev-1022", "name": "sensor", "ts_ms": 60012, "data": {}, "expires_ms": 30000 }
{ "type": "ping", "client_mono_ms": 61000, "ts_samples_up": 976000, "ts_samples_played": 412800 }
```

- `caps.aec`：`device` / `os` / `none`；`none` 时不允许 `open_mic`。`caps.receipts` 缺 `interrupted` → 截断用网关估计；缺 `ended` → `completed` 靠 `T_drain`。
- `turn.end.reason`：`release` / `max_duration` / `cancel`。`ts_samples` 是边界时刻的采样计数（不含预录）。
- `abort.reason`：`button` / `wake` / `text` / `user_cancel`。`user_cancel` 且无后续 `turn.start` → `cancelled`，其余 → `interrupted`。
- 回执：`played_ms` 是**句内**从扬声器送出的时长（按 DAC 已消费采样折算）；`started`、`ended` 每句各一次；`interrupted` 只对正在播的那一句发一次，之后同回合不再发 `ended`。客户端不重发回执。
- `event` 需要 ack：网关回 `{ "type": "event.ack", "id": "ev-1022", "status": "accepted" }`，`status` 为 `accepted` / `duplicate` / `rejected`。

### 2.6 下行消息（网关 → 客户端）

```json
{ "type": "hello", "session_id": "S-8f2c", "mode": "ptt",
  "audio_down": { "codec": "opus", "rate": 16000, "frame_ms": 20 }, "lead_ms": 300,
  "timers": { "T_hold": 600, "T_asr": 3000, "T_filler": 2000, "T_respond": 8000,
              "T_link_client": 15000, "ping_interval": 5000, "resume_window": 30000 },
  "next_server_turn": 1, "resume": null, "server_time_ms": 1790000000123 }

{ "type": "stt", "turn_id": "c42", "text": "<部分转写>", "final": false, "segment": 0 }
{ "type": "stt", "turn_id": "c42", "text": "<定稿转写>", "final": true }
{ "type": "tts.start", "turn_id": "c42", "kind": "speech", "codec": "opus", "rate": 16000 }
{ "type": "tts.sentence_start", "turn_id": "c42", "sentence_id": 0, "text": "<第一句>",
  "offset_ms": 0, "duration_ms": 1320, "interruptible": true }
{ "type": "tts.stop", "turn_id": "c42", "reason": "done" }
{ "type": "error", "turn_id": "c42", "code": "asr_timeout", "fatal": false, "message": "...", "retry_after_ms": null }
{ "type": "turn.closed", "turn_id": "c42", "state": "failed", "by": "asr_timeout", "heard_ms": 0, "segments": 1 }
{ "type": "flush.start", "flush_id": "f-77", "turn_ids": ["c41"], "reason": "abort", "ack_of": "abort" }
{ "type": "flush.end", "flush_id": "f-77" }
{ "type": "turn.start", "turn_id": "s17", "source": "vad", "ts_samples": 2211840 }
{ "type": "turn.end",   "turn_id": "s17", "source": "vad", "ts_samples": 2260000, "reason": "endpoint" }
{ "type": "turn.start", "turn_id": "s18", "source": "event", "kind": "narration" }
{ "type": "turn.merge", "from": "c43", "into": "c42" }
{ "type": "cmd", "id": "cmd-310", "name": "light", "args": { "pattern": "thinking" }, "timeout_ms": 3000 }
{ "type": "pong", "client_mono_ms": 61000, "server_ms": 1790000061042 }
```

- `tts.start.kind`：`speech` / `narration` / `cue`。`sentence_id` 在回合内对三种 kind 统一编号。`offset_ms` 是该句在回合下行时间轴上的起点；`duration_ms` 已知时给（预合成、本地直念），流式可省。
- `tts.stop.reason`：`done` / `interrupted` / `error`。`error.turn_id = null` 表示会话级错误。
- `cmd` 由客户端回 `{ "type": "cmd.result", "id": "cmd-310", "ok": true }`；超时记失败，不自动重发非幂等指令。

### 2.7 音频帧头（WebSocket 二进制帧，16 字节头 + 一帧 Opus）

字段语义是契约，字节布局是建议。

| 偏移 | 上行字段 | 下行字段 | 类型 |
|---|---|---|---|
| 0 | `ver` = 1 | `ver` = 1 | u8 |
| 1 | `kind` = `0x01` | `kind` = `0x02` | u8 |
| 2 | `flags`：bit0 `key_down`，bit1 `echo_tail`，bit2 `retransmit` | `flags`：bit0 `uninterruptible` | u8 |
| 3 | 保留 0 | 保留 0 | u8 |
| 4 | `seq`（会话内递增，续接不归零） | `seq` | u32 |
| 8 | `ts_samples`（本帧首采样的采样计数，回绕比较） | `turn_id` | u32 |
| 12 | `turn_id` | `sentence_id`（u16）+ `frame_idx`（u16，句内帧序号） | u32 |

### 2.8 打断时序

固定动作序列：**客户端立即停播 → 发 `interrupted` 回执 → 网关代际 +1 → 冲刷 → 上游 cancel / truncate**。

按键触发（同一 tick 内依次发出）：

1. 客户端本地立即停播、清播放缓冲，`play_gen += 1`，保留 `interruptible:false` 的句。
2. `playback.interrupted{c41, sentence_id, played_ms, reason:"button"}`。
3. `abort{c41, reason:"button"}`。
4. `turn.start{c42, source:"button"}`。
5. 网关：`gen += 1`；同一轮事件循环内发 `flush.start{ack_of:"abort"}`；清发送队列，上游旧输出按 `gen` 丢弃；`cancel` + `truncate(offset_ms(sentence_id) + played_ms)`。
6. 网关连续发 `flush.end`、`tts.stop{interrupted}`、`turn.closed{c41, interrupted, by:"button"}`。

VAD 触发（开放麦）：网关先做误打断过滤（保护窗口、最短时长、附和语），确认后 `gen += 1`，下发 `flush.start{reason:"vad"}` 和 `turn.start{s19, vad}`；客户端停播、`play_gen += 1`、回 `playback.interrupted`；网关等回执最多 `T_intr_receipt`，再 cancel + truncate。客户端不因本地 VAD 自行停播。

文本触发与按键相同，`reason:"text"`。

客户端两级门控：一级按 `turn_id`（只播当前回合，镜像为 `responding / draining`）；二级按 `play_gen`（帧进解码流水线时打上当时的 `play_gen`，出队前比对，不等即丢），防同回合 abort 后已在解码队列里的残余包。

截断位置 `audio_end_ms = offset_ms(sentence_id) + played_ms`。`T_intr_receipt` 内无回执时用网关估计（已发出时长 − `lead_ms`），trace 标 `truncate_estimated`。上游不支持 truncate 时，本地上下文按"已播完的句 + 当前句按比例"记用户听到的文本，并保留打断标记。

### 2.9 断线续接

```json
{ "type": "hello", "proto": "va/1", "profile": "A", "caps": {},
  "resume": { "session_id": "S-8f2c", "last_tx_seq": 48210, "last_rx_seq": 9021,
              "current_turn": "c42", "turn_state": "listening" } }

{ "type": "hello", "session_id": "S-8f2c",
  "resume": { "ok": true, "last_rx_seq": 48177, "turns": [ { "turn_id": "c42", "state": "listening" } ] } }
```

客户端从网关回的 `last_rx_seq` 之后补发（`flags.retransmit=1`）；`resume.ok:false` → 网关回 `error{session_expired, fatal:true}`，客户端丢弃全部回合状态，走新会话。

| 断线时回合状态 | 续接成功 | 续接失败 |
|---|---|---|
| `listening`（仍按住） | 补发 + 缺口规则，继续 | 客户端本地 `failed{link_lost}`，播本地话术 |
| `listening`（断线期间已松键） | 补发 `turn.end`（`ts_samples` 为实际松键时刻） | 同上 |
| `holding` / `committed` | 继续；已超 `T_asr` / `T_respond` 则网关补发 `error` + `turn.closed{failed}` | 同上 |
| `responding` / `draining` | 客户端对正在播的句发 `playback.interrupted{reason:"link"}`；网关不重发已发音频（已过时），按回执截断，回合 `interrupted{by:"link"}` | 同上；网关按估计截断 |

重连缺口 = 续接后首帧 `ts_samples` − 断前末帧 `ts_samples` − 帧长（扣除补发帧）：≤ 60–100 ms 用 Opus PLC 补；100 ms–1 s 不补，回合标 `audio_damaged`；> 1 s 且在 `listening` → `failed{audio_gap}`。

## 3. 规则

1. 每个回合相关帧（上行帧头、`turn.*`、`stt`、`tts.*`、`playback.*`、`error`、`flush.*`、`turn.closed`）都带 `turn_id`，因为迟到的识别结果和上游输出要据此归属，设备要据此丢弃过期回合的音频。
2. 每个 `turn.start` 恰好对应一个 `turn.closed`，之后该回合任何下行帧都是协议违规（客户端计数），因为悬空回合会让设备卡在通话态、看门狗失效；会话在续接窗口外失效时由 `session.closed` 一并了结为 `failed{link_lost}`。
3. 网关是终态的唯一发出方，客户端只报事实（回执、abort、边界），因为两端各自宣布终态必然出现分歧。
4. 同一会话同一时刻最多一个用户回合处于 `listening / holding / committed / responding`；新 `turn.start` 到达时旧回合按打断处理（`holding` 走合并），因为两个活动回合会双声。
5. 客户端只在镜像为 `responding / draining` 且 `turn_id` 等于当前回合时把下行音频入播放队列，因为这是防旧音频的第一道闸。
6. 按键模式下网关只认按键为边界和打断信号，忽略上游 `speech_started`，因为环境噪声会打断播报。
7. 开放麦下音频归属以 `ts_samples` 区间为准，帧头 `turn_id` 只是提示，因为边界由网关判定、客户端当时并不知道回合归属。
8. 一次输入被上游切成多段不产生新 `turn_id`，回复只在回合提交时请求一次；上游自行开始的回复由网关取消并按 `gen` 丢弃，因为多次回复会让模型在松键前调工具。
9. 模式切换只在 `idle` 时生效；需要改上游判停开关时走会话重建，因为多数上游不能运行中切换判停。
10. 收到 `abort` 后在同一轮事件循环内发 `flush.start`，`flush.end` 之后被冲刷回合零音频帧，因为打断干脆度依赖这条硬契约。
11. 截断位置用 `offset_ms + played_ms`，不用单句 `played_ms`、不用墙钟，因为墙钟估计在设备缓冲下偏大，单句值丢了前面句子的时长。
12. 网关按实时节奏下发、超前不超过 `lead_ms`，落后时重新对齐不突发补发，禁止一次推整段音频，因为限速是打断干脆和播放位置可估的前提。
13. TCP 卡顿后不补静音（客户端不往上行插、网关不往上游插），下行欠载时设备停在欠载处等待、不发 `playback.ended`，因为关不掉的上游 VAD 会把静音当句尾切段；PLC 只用于重连缺口，因为卡顿不丢数据。
14. 无屏设备的每个 `failed` 都必须发声，话术本地预合成，因为网关挂了也要能出声，沉默即故障。
15. 播放期间麦克风和 AEC 一直在跑、实例跨轮保留，因为"按键才开采集"会让 AEC 永远收敛不了；这是 `open_mic` 的硬前提。
16. `interruptible:false` 只用于白名单内的系统安全提示，单句 ≤ 3 s，不用于保护写操作，因为 S2S 服务端判停下"禁止打断"不可靠，写操作要靠执行前查代际（见 tool-closure.md）。
17. 未知 `type` 和未知字段忽略；缺必填字段回 `error{protocol_error}`、不断连，因为两端可能由不同团队分别升级。
18. 设备事件带 `id` 需 ack，客户端未收到 ack 时按指数退避重发直到 `expires_ms`，网关按 `id` 幂等，因为可靠事件路径不能丢也不能重复处理。

## 4. 可调参数

计时器生效值由网关在 `hello` 应答的 `timers` 下发，客户端不自定。

| 参数 | 默认 | 性质 |
|---|---|---|
| `T_hold` 补充窗口 | 600 ms，上限 1000 ms | 建议值 |
| `T_listen_max` | 无屏硬件 30 s / 有屏客户端 60 s | 建议值 |
| `T_asr` 提交 → 识别定稿 | 3 s（实测定稿约 0.37 s） | 建议值 |
| `T_filler` 提交 → 首个出声 | 无屏 2.0 s；有屏语音填充 2.5 s | 建议值 |
| `T_respond` 定稿 → 首个 speech | 8 s；工具回合进度收口时视为已开始 speech | 建议值 |
| `T_drain` | 剩余未播时长 + 2 s | 建议值 |
| `T_intr_receipt` | 300 ms | 建议值 |
| `T_link_client` 提交 → 无 `stt` 回显 | 15 s | 实践值 |
| `T_write` 网关写阻塞 / `T_hello` | 10 s / 10 s | 建议值（开源框架默认） |
| ping 间隔 / 判失效 | 5 s / 连续 3 次无 `pong` | 建议值 |
| 下行超前量 `lead_ms` | 200–500 ms，起点 300 ms；须 ≤ `play_buffer_max_ms` − 200 ms | 建议值 |
| 设备播放缓冲上限 | ≤ 1.2 s，满了丢新帧并计数 | 实践值 |
| 起播预缓冲 / 预录 `preroll_ms` | 每句可直发 ≤ `lead_ms`，设备起播前攒 100–200 ms / 200–300 ms | 建议值 |
| 短按 / 短语音过滤 | < 250 ms / < 150 ms | 建议值 |
| 上行编码 | Opus 单声道 16 kHz，20 ms 帧，DTX 关，FEC 关（WS），16–24 kbps | 建议值 |
| 下行编码 | Opus 16 kHz 或 24 kHz（按 `hello` 协商），20 ms | 建议值 |
| 不可打断句限长 | ≤ 3 s | 建议值 |
| 回声尾巴标记 | 播放中按键后上行前 150–300 ms 置 `echo_tail`，只标不丢 | 建议值 |
| VAD 打断保护 | AEC 预热 3.0 s、最短 0.5 s、误打断恢复 2.0 s | 建议值（开源框架默认） |
| 续接窗口 | 30 s（有屏客户端切后台可延长到 120 s） | 建议值 |
| 上行补发环形缓冲 | ≥ 2 s | 建议值 |
| 事件重发 / `cmd` 超时 | 2 s 起指数退避至 `expires_ms` / 3 s | 建议值 |

上游对突发上行（快于实时）和上行空洞的反应需按上游实测，决定网关是否要按实时重新整形上行。

## 5. profile 差异

| 项 | 按键硬件（WS） | 开放麦（硬件或客户端） | 浏览器 / App（WebRTC） |
|---|---|---|---|
| 模式与来源 | `ptt`；`button`、`text` | `open_mic`；`wake` / `vad`，`button` 作强制边界 | 通常 `open_mic`；按住说话为降级 |
| 打断信号 | 按键，客户端先停播 | 网关 VAD，客户端等 `flush.start` 才停 | 同开放麦；点"停止"走 `abort` |
| `caps.aec` | `device` 或 `none` | 必须 ≠ `none` | `os`（由浏览器 / libwebrtc 提供） |
| `listen.open / close` | 可选 | 必填 | 必填 |
| `holding` | 启用 | 不启用 | 不启用 |
| 上行帧头 `seq / ts_samples / turn_id` | 必填 | 必填（WS 时） | 不适用，RTP 序号与时间戳承担，按时间戳映射回合区间 |
| 下行帧头 | 必填 | 必填（WS 时） | 不适用，句边界靠 data channel 的 `tts.sentence_start` 与 RTP 时间戳对齐 |
| `playback.started / ended` | 必填 | 必填 | 可选（客户端缓冲浅，网关可估计） |
| `playback.interrupted` | 必填（`played_ms` 是唯一可靠位置） | 必填 | 本地停播时必填，网关主动冲刷时可选 |
| 下行限速 | 网关实现 | 网关实现（WS 时） | 媒体栈队列反压 |
| 抖动缓冲 / PLC | 设备浅缓冲，PLC 仅重连缺口 | 同左 | NetEQ，仅当两端都是 libwebrtc |
| `ping` 时钟锚点 | 必填 | 必填（WS 时） | 可选（RTCP 承担） |
| `play_gen` | 必填 | 必填 | 建议实现 |
| `event` + ack | 必填（设备事件多） | 视设备 | 可选 |
| 失败必须发声 | 必须，本地话术 | 无屏时必须 | UI 兜底 + 语音填充 |

换成 WebRTC 只换掉帧头、下行限速、抖动缓冲和 PLC；`turn_id` 与终态、边界事件、回执（至少 `interrupted`）、冲刷契约、错误码、看门狗、会话续接仍由本协议承担。

## 6. 验收清单

- [ ] 长稳压测（≥ 1000 回合，含随机断线、打断、上游故障注入）悬空回合数 = 0；`turn.closed` 之后该 `turn_id` 下行帧数 = 0。
- [ ] 上游不回应：`error{upstream_timeout}` 在 `T_respond ± 200 ms` 内到达；无屏设备在 `T_filler ± 200 ms` 内有声，全程无 > 2.5 s 静默。
- [ ] ASR 空结果：100% 收到 `error{no_speech}` + `turn.closed{failed}`，无屏设备 100% 播本地话术；按键模式注入上游 `speech_started` 不触发打断。
- [ ] 上游在按键期间切段：不产生新 `turn_id`，松键前的上游回复被丢弃，松键前调工具率 = 0。
- [ ] 补充窗口内再按得到一个回合（`turn.merge`），`stt` 合并文本完整；短按 < 250 ms 终态 `discarded`、零出声。
- [ ] 1000 次随机时刻打断，`flush.end` 后被冲刷回合音频帧 = 0（双向抓包核对）。
- [ ] 按键打断：key-down 到扬声器降到底噪 ≤ 60 ms（不可打断句除外）；`played_ms` 与录音实测位置误差 ≤ 40 ms。
- [ ] abort 后 200 ms 内仍到达的同 `turn_id` 帧播出 = 0（`play_gen` 生效）。
- [ ] 打断后上游上下文只含 `heard_ms` 以内内容；会话开头 3 s 保护窗口内的起说不触发 `flush.start`。
- [ ] 下行任意时刻超前量 ≤ `lead_ms` + 一帧；注入 2 s 上行卡顿后送上游的新增静音帧 = 0，上游切段次数与对照组无显著差异。
- [ ] 重连缺口 80 ms：PLC 生效无 `audio_damaged`；500 ms：有 `audio_damaged`；2 s 且在 `listening`：`failed{audio_gap}`。
- [ ] 续接补发的重复 `seq` 被去重；设备播放缓冲从不超过 `play_buffer_max_ms`，正常网络下溢出计数 = 0。
- [ ] 断网期间的设备事件续接后送达、网关恰好处理一次、重复发送收到 `duplicate` ack；`cmd` 无应答在 `timeout_ms` 后记失败、不重复执行。
- [ ] 不支持回执的旧客户端能完成完整回合，截断标 `truncate_estimated`。
- [ ] 两端由不同团队实现时，能互跑对方的协议用例集。

## 7. 产出骨架时的注意事项

- 状态机写成显式枚举 + 转移表，非法转移直接报错。`turn_id` 编解码（文本 `c<n>` / `s<n>` ↔ `uint32` 最高位）写成一对函数并配单测，续接后计数不归零。
- 网关看门狗要覆盖每个非终态，到期动作统一走"发 `error` → 发 `turn.closed`"，不要散落在各处。
- 客户端播放流水线要同时实现 `turn_id` 门控和 `play_gen` 门控；只做前者会漏掉同回合残余包。
- 回执里的 `played_ms` 从 DAC 消费的采样数算，不要用解码或入队时长；网关侧换算 `audio_end_ms` 时加上 `offset_ms`。
- `flush.start` 在处理 `abort` 的同一轮事件循环内发出，不放进异步队列；随后 `flush.end`、`tts.stop{interrupted}`、`turn.closed` 三帧连续发，旧客户端把 `tts.stop{interrupted}` 当冲刷完成。
- 下行发送器按音频时间轴节拍发，落后时重置节拍，不写"能发多快发多快"的循环；上行接收在卡顿后原样按 `seq` 转发突发数据，不补静音帧。
- 续接逻辑要处理"断线期间已松键"：客户端缓存松键时刻的 `ts_samples`，续接后补发 `turn.end`。
- 客户端本地判定的 `failed{link_lost}` 只作临时状态，续接后以网关补发的 `turn.closed` 为准，纠正状态但不再发声。- 无屏设备的错误话术打包进固件或本地资源，不依赖网关合成。`hello` 里没有能力声明的旧客户端要能降级运行：截断用估计、无回执时按估计开插话窗口。
