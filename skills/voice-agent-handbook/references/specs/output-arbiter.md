# 设计模板：输出仲裁器

## 目录

1. 解决什么问题
2. 核心模型（2.1 OutputRequest / 2.2 origin 与 kind / 2.3 仲裁器状态 / 2.4 请求生命周期 / 2.5 代际与租约 / 2.6 插话窗口 / 2.7 冲刷 / 2.8 打断后的重试判定 / 2.9 对外接口）
3. 规则
4. 可调参数
5. profile 差异
6. 验收清单
7. 产出骨架时的注意事项

## 1. 解决什么问题

仲裁器回答一个问题：任一时刻谁可以向用户出声、出多少、被打断后怎么办。模型回复、本地直念的工具结果、提示语、事件播报、委派结果都以同一种"输出请求"进入它。它保证四件事：同一时刻只有一个输出在出声（双声 = 0）；打断或换回合后旧输出一帧都不再出声，包括上游迟到的帧；非本回合的播报只在不打扰用户时插话，"不打扰"以客户端真实播放回执为准；任何信号丢失都不会让它卡死或无限重播。

不在范围内：打断检测（归回合状态机，见 turn-protocol.md）、说什么（归模型和工具层）、上下文截断的执行（归上下文层，仲裁器只提供回执里的 `played_ms`）、事件的去抖和定性。

## 2. 核心模型

### 2.1 OutputRequest

```yaml
OutputRequest:
  request_id: "req-7f3a"          # 必填，会话内唯一；下游二次过滤的键
  origin: tool_result             # 必填，见 2.2
  kind: speech                    # 必填，cue | speech | narration
  priority: normal                # 必填，critical | high | normal | low
  generation: 12                  # cue / speech 必填：发起时的当前代际；narration 省略，授权时盖章
  turn_id: "c41"                  # cue / speech 必填；narration 可空（授权时绑定当时的回合）
  interruptible: true             # 默认 true；false 仅限白名单内的系统安全提示，≤ 3 s
  on_interrupt: drop              # drop | retry；用户打断一律按 drop
  max_retries: 0                  # on_interrupt=retry 时生效
  expires_at: null                # 过期未开始播即丢弃
  dedupe_key: "task-88:result"    # 同键去重
  supersedes: "task-88:progress"  # 入队时替换同键的未开始请求（新覆盖旧）
  context_policy: after_played    # none | before_speak | after_played
  content:
    type: text_tts                # prerecorded | text_tts | upstream_stream
    text: "<要念的文本>"
    voice: default                # 统一音色
    # upstream_stream 额外带 response_id、item_id，用于 cancel、truncate、迟到帧识别
  ui:                             # 仅有屏客户端
    transcript: sentence_aligned  # sentence_aligned | none
    card_ref: "card-203"          # 卡片可早于语音上屏
  trace: { tool_call_id: "call_91", task_id: "task-88" }   # 调用方填，仲裁器透传
```

| `content.type` | 典型来源 | 时长可知 | 冲刷时动作 |
|---|---|---|---|
| `prerecorded` | cue、固定问候、错误话术 | 入队即知 | 下游停播、丢缓冲 |
| `text_tts` | 直念的工具结果、到点播报、委派结果 | 按合成 chunk 累计 | 本地 TTS `tts_flush` + 下游停播 |
| `upstream_stream` | S2S 模型回复 | 流结束才知 | 上游 cancel + truncate + 下游停播 + 迟到帧过滤 |

`context_policy`：`none` 永不通知上下文层（cue 固定为此）；`before_speak` 入队即通知（适合结果类 narration，用户在播报前追问时模型能答）；`after_played` 在 `playback.ended / interrupted` 时附 `played_ms` 通知，上下文层只记用户实际听到的。重试不重复通知。

### 2.2 origin 与 kind

| origin | 默认 kind | 默认 priority | 说明 |
|---|---|---|---|
| `model` | speech | normal | 模型回复 |
| `tool_result` | speech | normal | 本回合内直念的工具结果 |
| `delegated_result` | speech / narration | normal | 委派结果；回合内到达算 speech，回合结束后到达算 narration |
| `cue` | cue | high | 旁路提示语，不进模型 |
| `task_progress` | narration | low | 长任务进度（节流见 4） |
| `task_result` | narration | normal | 后台任务结果、秒级写操作的结果 |
| `reminder` | narration | normal | 定时器 / 到点 |
| `device_event` | narration | normal | 传感器等设备事件 |
| `system_notice` | speech | high | 回合级错误话术、沉默填充 |
| `system_exit` | speech | critical | 转人工、挂断、紧急出口 |

| 属性 | cue | speech | narration |
|---|---|---|---|
| 绑定回合 | 是 | 是 | 否（授权时绑定） |
| 需要插话窗口 | 否 | 否 | 是 |
| 打断时冲刷正在播的 | 否，放完 | 是 | 是 |
| 打断时排队中的 | 低代际的丢 | 丢 | 保留，等下一个窗口 |
| 非用户原因中断后 | drop | drop | 按 `on_interrupt`：进度 drop，结果 / 提醒 retry |
| 进上下文 | 否 | 按来源 | 按来源 |

优先级只决定排队顺序（同级 FIFO），不决定抢占；只有 `critical` 能抢占，等同一次系统发起的打断。种类约束优先于优先级：`high` 的 narration 仍要等窗口，`low` 的 speech 不用等。

### 2.3 仲裁器状态

仲裁器维护一个话筒、一个有界等待队列、当前代际 `G`、窗口条件（`user_speaking`、`turn_pending`、`awaiting_tool`、未播完音频集合）、已送达的 `dedupe_key` 集合。

| 状态 | 事件 | 转移 | 动作 |
|---|---|---|---|
| `IDLE` | 队列中有可授权请求 | → `GRANTED` | 签发租约 `(lease_id, G)`；narration 此时盖章 `generation = G` |
| `IDLE` | narration 被窗口挡住 | 不变 | 每 1 s 自唤醒重检 |
| `GRANTED` | 最后一句 `playback.ended`；回执超时；无后续输出 | → `IDLE` | 结束授权；若回合所有 speech 播完，发 `turn.audio_drained` |
| `GRANTED` | `playback.interrupted` | → `IDLE` | 按 2.8 判定 drop / retry |
| `GRANTED` | 代际 +1 / 定向冲刷 / critical 到达 | → `FLUSHING` | 按 2.7 动作序列 |
| `FLUSHING` | `flush_end`，或等待 1 s 超时（记 `flush_timeout`） | → `IDLE` | 放行队列 |
| `FLUSHING` | 又一次代际 +1 | 不变 | 合并为一次冲刷，等最后一个 `flush_end` |
| `FLUSHING` | critical 请求 | → `GRANTED(critical)` | 直接授权触发者 |

可授权 = `generation ≥ G` ∧ 未过期 ∧（kind ≠ narration ∨ 插话窗口打开）。被跳过的 narration 留在队列，不阻塞后面的 cue / speech。

### 2.4 请求生命周期

| 状态 | 事件 | 转移 |
|---|---|---|
| `queued` | 授权 | `granted` |
| `queued` | 过期 / 代际过期 / 重复 / 队列溢出 | `dropped`（原因 `expired` / `stale` / `duplicate` / `overflow`） |
| `queued` | narration 窗口关 | 留在 `queued` |
| `granted` | 首帧下发 | `sent` |
| `sent` | `playback.started` | `playing`（送达以此为准） |
| `playing` | 最后一句 `playback.ended` | `done` |
| `granted / sent / playing` | 冲刷、代际过期、`playback.interrupted` | `interrupted` |
| `interrupted` | 用户原因 | `done`（视为已送达） |
| `interrupted` | 非用户原因且可重试 | `retry_wait` → `queued`；达上限 → `dropped(retry_exhausted)` |

### 2.5 代际与租约

`G` +1 只有三种情况：新用户回合开始（被合并进 `holding` 回合的 `turn.start` 不算）、用户打断、critical 抢占。打断与新回合同时发生只 +1 一次。回合正常结束、工具返回、会话重建、narration 开始或结束都不 +1。

代际在三处比较，任一处 `generation < G` 即丢：入队时（记 `stale`）、授权时、每个下行帧发出时。上游 cancel 后仍推来的音频由第三处挡住；适配层同时对已 cancel 的 `response_id` 打 `suppressed` 标记，只用于计数。

租约 `(lease_id, generation)` 在授权时签发，持有者向下游发帧必须带租约，仲裁器和下游都按租约代际过滤。

`interruptible=false` 不阻止代际 +1，但冲刷时跳过它（放完），它的帧按冲刷后的新代际盖章发出。白名单外来源或超限长时按 `true` 处理并记 warning。

跨连接的播报领取（claim）：同一条 narration（按 `dedupe_key`）在多个前台连接或断线重连之间只能被一个连接领取；连接断开只释放领取、不算送达。单设备单连接时退化为"重连后重新领取"。

### 2.6 插话窗口

窗口关闭 ⇔ 以下任一成立：

1. 用户在说：`user_speaking`（按键模式 = 按键按住）；开放麦下 `turn.endpointing{active:true}` 期间也算。
2. 回合未结束 `turn_pending`：用户开口时置位；只有回复已结束、且（无音频 ∨ 音频已收到 `playback.ended`）、且不在等工具续答时才清除。
3. 有未播完的音频：已下发但未收到 `playback.ended / interrupted`，也未判回执超时。

三者都不成立，再静默 350 ms 才授权 narration。`task_progress` 更严：另要求会话 ready 且没有任何在途输出。结束授权 ≠ 窗口打开。

### 2.7 冲刷

触发：代际 +1 的三种情况，以及按来源的定向冲刷（例如任务结束时清掉它排队中的进度，或工具已完成时撤回未授权的 cue）。

对 speech / narration 持有者的动作序列：

1. 撤销授权（回合级冲刷时代际已 +1）。
2. `upstream_stream`：通知适配层 `cancel_response(response_id)`，按回执换算的位置 `truncate(item_id, audio_end_ms)`。
3. `text_tts`：向本地 TTS 发 `tts_flush(flush_id)`。
4. 向设备发 `flush.start{flush_id, turn_ids}`，随后 `flush.end`、`tts.stop{interrupted}`、`turn.closed`（线上帧按 `turn_id`，内部按 `request_id` 记账，见 turn-protocol.md）。
5. 等本地 TTS 回 `flush_end`；设备侧不回确认帧，以网关清空发送队列、发出 `flush.end` 为准。
6. 等待期间不授权普通请求（critical 除外），上限 1 s，超时记 `flush_timeout` 后放行。

### 2.8 打断后的重试判定（第一个命中的行生效）

| 条件 | 结果 |
|---|---|
| 原因是用户打断（`user_interruption`） | 视为已送达，不重播，无论 kind 和 `on_interrupt` |
| kind = cue 或 speech | drop |
| `on_interrupt = drop` | drop |
| 已过 `expires_at` | drop，记 `expired` |
| 已被 `supersedes` 覆盖或同键已送达 | drop，记 `duplicate` |
| 重试次数达 `max_retries` | drop，释放领取，记 `retry_exhausted` |
| 其余（critical 抢占、断连、回执超时、系统冲刷） | 重新入队，重试计数 +1 |

### 2.9 对外接口

| 方向 | 事件 | 仲裁器动作 |
|---|---|---|
| 状态机 → | `turn.start{turn_id, source}` | `G` +1；冲刷；`turn_pending = true`；`user_speaking = true` |
| 状态机 → | `user.barge_in{turn_id, source}` | `G` +1；冲刷，原因 `user_interruption` |
| 状态机 → | `user_speaking` 变化、`turn.endpointing{turn_id, active}` | 更新窗口条件 1 |
| 状态机 → | `turn.awaiting_tool{turn_id, bool}` | 更新窗口条件 2 |
| 状态机 → | `turn.reply_done{turn_id, outcome}` | 清 `turn_pending`（仍需音频播完） |
| → 状态机 | `turn.audio_drained{turn_id}` | 回合所有 speech 已收到结束回执或判超时，状态机据此进 `completed` |
| → 状态机 | `turn.no_output{turn_id}` | 回合结束但没有任何授权；看门狗决定是否发 `system_notice` |
| 网关 → | `playback.started / ended / interrupted{turn_id, sentence_id, request_id, played_ms, reason}` | 线上回执不带 `request_id`，网关按 `(turn_id, sentence_id)` 查出补上；以最后一句 `ended` 作请求结束 |
| → 适配层 | `cancel_response(response_id)`、`truncate(item_id, audio_end_ms, played_text?)` | `audio_end_ms = offset_ms(sentence_id) + played_ms`，换算到条目内；`0` 表示删除该条；不支持截断的上游返回 `unsupported`，只记 trace |
| 工具层 → | 已定性为 `respond` 的事件 / 结果；定性为 `interrupt` 的以 `critical` 入队 | `handle` 与 `context` 类不进仲裁器 |

仲裁器不决定回合终态，也不自己发看门狗话术；话术作为 `system_notice` 请求回到仲裁器。适配层不得绕过仲裁器直接向下游写音频。

## 3. 规则

1. 话筒同一时刻至多一个有效授权，因为双声是最直接的体验故障。
2. 授权的是出声不是生成：上游可提前生成、TTS 可提前合成，但只有持授权者能向下游发帧，因为这样提示语和正文能排队而不串行等待。
3. 低于当前代际的输出一律丢弃，不看来源，因为迟到帧来自上游、工具、TTS 各处，按来源判断必有遗漏。
4. 代际在入队、授权、每帧发出三处比较，因为已授权的流式输出在生成途中代际可能变化。
5. 长生命周期任务（事件循环、后台回帖、定时器）不缓存租约，每次出声都入队新请求，因为拿着旧租约出声会被过滤，表现为"只有第一轮有声"。
6. narration 入队时不带代际、授权时盖章，因为它不属于任何回合，按出生代际判会永远播不出去。
7. 插话窗口以客户端回执为准，不以服务端生成完或发完为准，因为设备有缓冲、服务端有超前量，否则 narration 会插进一句话的尾巴。
8. cue 正在播时不冲刷、不被同回合结果抢占，但低代际未开始的 cue 丢弃，因为 cue 很短且本地播放，而旧回合的提示语在新回合开头冒出来没有意义。
9. 被用户打断的输出视为已送达、不重播，因为重播同一段比省略更打扰。
10. `flush_end` 之后该 `flush_id` 覆盖的 `request_id` 零音频，因为这是可断言的硬契约。
11. 每个消费端（设备下行出口、本地 TTS、客户端播放器）独立维护 `current_generation` 和 `closed_request_ids` 做二次过滤并上报丢弃数，因为消费端丢帧说明中心过滤漏了，是缺陷信号。
12. 回执缺失时靠自唤醒、开始回执超时、结束回执超时三道兜底，绝不叠出第二段，因为回执会丢、客户端会节流。
13. 同一回合同一 `dedupe_key` 只授权一次；已送达的键在会话内记住，因为同一后台任务的结果可能回来两份、重连后可能重播到点提醒。
14. 队列有界，溢出丢优先级最低、入队最早的并记 trace，因为慢设备加长回答会让队列无限增长。
15. 不依赖"不可打断"保护写操作，因为 S2S 服务端判停下该开关会被忽略；写操作靠执行前查代际（见 tool-closure.md）。
16. 节流由工具层做，仲裁器只执行 `supersedes` 和定向冲刷，因为仲裁器不理解业务含义。
17. 进度只看后台协议消息，不从工具活动推断；工具活动只驱动 UI / 灯效，因为活动推断会产生无内容的播报。

## 4. 可调参数

| 参数 | 值 | 性质 |
|---|---|---|
| 队列上限 | 无屏 32 / 有屏 16 | 建议值 |
| 插话窗口静默 | 350 ms（停顿多、语速慢的用户可能要更长） | 建议值（开源默认） |
| 被挡 narration 自唤醒 | 1 s | 建议值（开源默认） |
| 开始回执超时 | 下发完成 + 5 s | 建议值 |
| 结束回执超时 | 下发完成 + 剩余未播时长 + 2 s（与 turn-protocol.md 的 `T_drain` 同口径）；`upstream_stream` 从流结束起算 | 建议值 |
| 冲刷等待上限 | 1 s | 建议值 |
| 重试上限 `max_retries` | 结果 / 提醒 8，进度 0；有屏客户端结果改上屏兜底后可设 2 | 建议值（开源默认） |
| 播报领取 TTL | 60 s，每 TTL/3 续期 | 建议值（开源默认） |
| 消费端 `closed_request_ids` 保留 | 最近 256 个 | 建议值 |
| 不可打断限长 | ≤ 3 s | 建议值 |
| 进度首条延迟 / 全局最小间隔 | ≥ 60 s / ≥ 60 s，跨任务共用 `last_announced_at` | 建议值（开源默认） |
| 进度覆盖 | 同任务 `supersedes="{task_id}:progress"` | 规则 |
| 进度静默防抖 | quiet 2 s，上限 10 s | 建议值 |
| 沉默填充 | 提交后 2.0 s 无声由看门狗发 `system_notice` / cue | 建议值 |
| 结果排在提示语后的等待 | P50 0、P90 0.2 s | 实践值（参考基线） |

60 s 的进度首条延迟对"沉默 > 2 s 即故障"的设备来说太长：前 60 s 的"有声音"靠 cue 和收口时的承接句，不靠进度。上游 cancel 之后是否还会推音频需按上游实测，不影响规则，只影响迟到帧测试的注入方式。

## 5. profile 差异

| 维度 | 按键硬件 | 开放麦 | 浏览器 / App（有屏） |
|---|---|---|---|
| 来源 | 多：model、tool_result、cue、device_event、reminder、task_progress、task_result、system_notice | 同按键硬件 | 少：model、tool_result、delegated_result、system_notice、system_exit；cue 可选 |
| critical | 一般没有 | 一般没有 | `system_exit` 为 critical，由规则触发，不由模型触发 |
| 打断来源 | 按键为主；忽略上游 `speech_started` | VAD + 语义；附和语过滤在状态机里做 | VAD 或点"停止" |
| 窗口条件 1 | 按键按住 | `user_speaking` + `turn.endpointing` | 同开放麦 |
| 回执通道 | 自有 WS 协议，`hello` 协商是否支持 | 同左 | WebRTC data channel 或 WS |
| 额外输出面 | 灯效（旁路，不经仲裁器） | 同左 | UI 同步（见下） |
| 沉默填充 | 必须 | 无屏时必须 | 可由 UI 兜，语音填充仍建议 |
| narration 量 | 多 | 多 | 少，进度多数只上屏 |

有屏客户端的 UI 同步：每句转写带 `request_id`、`sentence_id`、代际，在该句 `playback.started` 时才上屏；冲刷时已上屏的句按 `played_ms` 标"被打断"，未上屏的丢弃；卡片和结构化字段不经话筒授权、可早于语音，但带代际，低代际的由客户端丢弃；`system_exit` 先上屏出口状态再出声。

客户端不支持回执时退化为"按下发时长 + 缓冲上限估算播放结束"，trace 标 `receipt_mode=estimated`，这是降级不是等价方案。

## 6. 验收清单

有竞态的项用重复率判，每条至少 20 次。

- [ ] 双声 0：回执时间线上任意两条请求的 [started, ended] 不重叠（cue 与同回合 speech 也不重叠）。
- [ ] 迟到帧过滤：打断后 0–2 s 内 mock 上游继续推旧 response，扬声器侧 0 帧，消费端二次过滤丢帧数 = 0。
- [ ] `flush_end` 之后下游收到被冲刷请求的帧 = 0。
- [ ] 丢弃全部 `playback.ended`：narration 在结束回执超时后仍能播出，回合能到终态。
- [ ] 丢弃全部 `playback.started`：同一 narration 重试 ≤ `max_retries` 后丢弃，任何时刻不叠声。
- [ ] narration 播放中用户打断：之后不再出现。
- [ ] narration 播放中断连再重连：重播一次且不重复。
- [ ] 模型长回答期间到点：narration 的 started 晚于回答最后一句 ended + 350 ms。
- [ ] 工具在跑（`awaiting_tool`）时后台结果到达：结果在回合终态之后才播。
- [ ] 同回合两次相同 `dedupe_key`：只授权一次，另一条记 `duplicate`。
- [ ] narration 在窗口关闭期间过期：不播，记 `expired`。
- [ ] 后台任务跨 ≥ 10 个回合多次出声：每次都能出声（租约不陈旧）。
- [ ] cue 在播时打断：放完；打断后才到的旧回合 cue 不播。
- [ ] critical 抢占：当前输出在冲刷上限内停止，出口话术紧接着播出，屏幕先于语音显示出口状态。
- [ ] 有屏客户端：每句转写上屏时刻与其 `playback.started` 偏差 ≤ 200 ms，被打断句的显示与 `played_ms` 一致。
- [ ] 慢设备 + 长回答：队列长度不超上限，溢出有记录。
- [ ] 3 分钟长任务、每 5 s 一条进度：最多 2 条进度出声（约 60 s、120 s），内容为最新版，任务完成后无残留进度。

## 7. 产出骨架时的注意事项

- 代际比较写三处（入队、授权、逐帧发送），最常漏的是逐帧发送这一处；`upstream_stream` 的每个音频 delta 都要带 `request_id` 和代际。
- narration 的 `generation` 字段在入队时为空，授权时才写；不要在构造函数里填当前代际。
- 后台任务、定时器回调里不要持有租约对象，只持有"入队一个 OutputRequest"的能力。
- 窗口条件 3 依赖回执，必须配结束回执超时；否则一个丢失的 `ended` 会让 narration 永远播不出。
- 自唤醒定时器要独立于事件驱动，事件本身会丢。
- 冲刷时 cue 与 speech / narration 分支处理：在播的 cue 不动，排队中的低代际 cue 丢；排队中的 narration 保留。
- 重试判定严格按 2.8 自上而下第一个命中；把"用户打断"放在第一行。
- 线上回执没有 `request_id`，网关要维护 `(turn_id, sentence_id) → request_id` 映射，`sentence_id` 在回合内对所有 kind 统一编号。
- `truncate` 的 `audio_end_ms` 要加上句起点 `offset_ms`；回执缺失时按估计截断并标 `truncate_estimated`。
- 仲裁器只通知上下文层，不自己写上下文；`before_speak` 和 `after_played` 在重试时不重复通知。
- 每个消费端都要实现二次过滤并上报计数，哪怕在正常路径上计数恒为 0。
- trace 至少记录：每条请求的 `request_id, origin, kind, generation, sentence_id, played_ms, status`，以及 `double_voice_count`、`stale_frames_dropped`、`drops_by_reason`、`flush_count`、`flush_timeouts`、`receipt_timeouts`、`consumer_dropped_frames`、`suppressed_frames`、`receipt_mode`、`truncate_estimated`。
