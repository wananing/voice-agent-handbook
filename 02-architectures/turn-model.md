# 回合模型：设备协议与 turn_id

> 状态：草稿
> 最后更新：2026-10-01

**要点**：把"回合"（turn）当成设备和服务端之间协议的基本单位：一次用户输入加上对应的 agent 输出，从边界开始到终态结束。三条规则支撑起整个协议：**每一帧都带 `turn_id`**（打断后可以无脑丢掉旧回合的音频，迟到的识别结果能归到正确回合）；**每个回合必须有且只有一个终态**（设备能区分"agent 在想"和"agent 挂了"）；**识别回显作为软确认**（设备据此判断链路是否还活着）。这一层与选级联还是 S2S 无关，换链路时设备不感知。

---

## 1. 这个决策是什么

```
                 turn.start(source)
   ┌──────┐  ───────────────────────►  ┌───────────┐
   │ idle │                            │ listening │  收用户音频
   └──────┘                            └─────┬─────┘
      ▲                         turn.end     │
      │                     （可选 holding：留补充窗口，用户可能接着说）
      │                                      ▼
      │                                ┌───────────┐
      │                                │ committed │  已提交，等识别定稿和首个输出
      │                                └─────┬─────┘
      │                     首个出声输出     ▼
      │                                ┌────────────┐
      │                                │ responding │  下行进行中
      │                                └─────┬──────┘
      │                        tts.stop      ▼
      │                                ┌──────────┐
      │                                │ draining │  服务端发完，设备还在播
      │                                └─────┬────┘
      │      turn.closed{state}              │ 最后一句播放回执 / 看门狗
      └──────────────────────────────────────┘
   任一非终态 ──(打断 / 过滤 / 错误 / 取消 / 看门狗)──► turn.closed{interrupted|discarded|failed|cancelled}
```

回合从用户开口（按键）开始，到 agent 的语音**在设备上播完**结束，而不是服务端生成完。服务端持有权威状态，设备维护一个镜像，只用来门控下行音频和呈现灯效或 UI。

为什么要把它做成显式的协议单位：语音交互里"这一轮结束了没有""用户听到了哪里""打断后还会不会有旧音频"这几个问题，如果协议里没有回合，每个组件都会给出自己的答案，而且彼此不一致。

---

## 2. 四条核心规则

### 2.1 `turn_id` 贯穿每一帧

所有与回合相关的帧都带 `turn_id`：上行音频帧头、边界事件、识别回显、TTS 分句、播放回执、错误、冲刷、终态。

- **谁检测到边界开始，谁生成 `turn_id`**：按键、唤醒词、文本输入由设备生成；开放麦下的 VAD 起说和服务端发起的事件播报由服务端生成。这样按键回合不用等一个往返才能打 id。一种做法是设备生成的用 `c<n>`、服务端生成的用 `s<n>`，回合先后以服务端的代际编号为准，不比较两边的数值。
- **设备只播当前回合的音频**：镜像不在 `responding / draining`、或 `turn_id` 不是当前回合的下行音频一律丢弃。
- **迟到的输出按 `turn_id` 归属**：已关闭回合的迟到识别结果或上游输出由服务端丢弃并记 trace，不下发。

### 2.2 回合必须有终态

五种终态，每个回合恰好一个，由服务端发出：

| 终态 | 含义 | 典型触发 |
|---|---|---|
| `completed` | agent 输出播完 | 最后一句播放回执；回执缺失时看门狗到期也发，并标记 |
| `interrupted` | 被用户或新回合打断 | 按键、VAD 打断、文本输入 |
| `discarded` | 不是有效输入，本回合没有出声 | 短按（< 250 ms）、误唤醒、规则判定不回应 |
| `failed` | 回合无法完成 | 识别为空、识别超时、上游不可用、合成失败 |
| `cancelled` | 设备或会话主动结束 | 挂断、App 切后台 |

关键是 `failed`：识别失败、上游不可用都要发一帧回合级错误（如 `no_speech`、`asr_timeout`、`upstream_unavailable`），然后发终态。没有屏幕的设备上，**每个 `failed` 都必须发声**，话术在设备本地预合成，服务端挂了也能出声。否则设备无法区分"agent 在想"和"agent 挂了"，用户只会觉得设备坏了。

### 2.3 识别回显作为软确认

服务端在识别出用户的话后，下发一帧带 `turn_id` 的回显（`stt`），意思是"我收到了这一句"。设备在提交后一定时间内（一份实践用 15 s）收不到匹配的回显，就判定链路失效、走重连。回显可以分段发（`final:false`），定稿时再发一条合并文本。

### 2.4 看门狗保底

回合不能因为任何一方不回应而悬空。计时器的值由服务端在握手时下发，设备不自定。一组起点值（建议值，需实测）：

| 计时器 | 区间 | 默认 | 到期动作 |
|---|---|---|---|
| 识别超时 | 提交 → 识别定稿 | 3 s | `failed{asr_timeout}` |
| 填充 | 提交 → 首个出声 | 2 s（无屏幕设备） | 插一段提示语，不关闭回合 |
| 响应超时 | 定稿 → 首个正文输出 | 8 s | `failed{upstream_timeout}` |
| 排空 | 发完 → 最后一句播放回执 | 剩余时长 + 2 s | `completed`，标记回执缺失 |
| 链路 | 设备提交 → 收到回显 | 15 s | 设备判链路失效 |

---

## 3. 边界来源与两种模式

| 来源 | 谁发开始 | 谁发结束 |
|---|---|---|
| `button` | 设备（按下） | 设备（松开） |
| `wake` | 设备（唤醒词命中） | 服务端（判停） |
| `vad` | 服务端（开放麦检测到起说） | 服务端（判停） |
| `text` | 设备（文本、测试、点选） | 同一帧隐含结束 |

**按键说话和开放麦是同一个状态机的两种配置**，差别只在边界来源。按键模式下服务端**只认按键为边界和打断信号**，忽略上游 S2S 的服务端判停事件，否则环境噪声会打断播报。

两个边界上的细节：

- **一次输入被切成多段**：上游服务端 VAD 把一次按键切成几段时，段不是回合，不产生新 `turn_id`；回复只在回合提交时请求一次。
- **一句话被两次按键分开**（松键太早）：服务端在 `holding`（补充窗口）期间收到新的按键开始，把它合并进上一回合（下发 `turn.merge`），之后都用旧的 `turn_id`；已经 `committed` 的不合并，按新回合打断旧回合处理。

---

## 4. 适合与不适合

**几乎所有实时语音 agent 都需要某种形式的回合模型**，区别在于做得多显式。

- 必须显式做：无屏幕的硬件（沉默即故障，终态和错误帧是唯一的反馈）；多来源出声（模型回复、本地播报、提示语并存）；要做打断后迟到帧过滤；要按回合统计延迟和评测。
- 可以从简：单一链路、有屏幕兜底、不在意迟到帧的原型。小智就是例子（见第 6 节）。

---

## 5. 关键取舍

| 取舍 | 说明 |
|---|---|
| 协议复杂度 vs 可诊断性 | 显式回合要多几种帧、多一套计时器；换来的是每个回合都有 trace、每种失败都有名字 |
| 回执驱动 vs 服务端估算 | 以设备的播放回执判定"播完"和"用户听到哪里"更准，但要设备实现回执；不支持时退化为按下发时长估算，并在 trace 标出 |
| 设备生成 id vs 服务端生成 id | 设备生成省一个往返；两端都会生成时需要用代际定先后 |
| 链路无关 | 协议不随链路变：级联、S2S、半级联在设备侧看到同一套帧，链路可以按设备、按环境切换 |

---

## 6. 开源项目怎么做

### 6.1 小智：没有 turn id，靠状态机和隐式代际

| 项 | 小智 | 显式回合模型 |
|---|---|---|
| 开始 / 结束说话 | `listen{state:start, mode:auto\|manual\|realtime}` / `listen{state:stop}`；文本或唤醒词用 `listen{state:detect, text}` | `turn.start / turn.end`，带 `turn_id` |
| 识别回显 | `stt{text}`，定稿后只发一次，只做显示，没有超时判定 | 带 `turn_id`，作为软确认 |
| TTS | `tts{state:start}` / `sentence_start{text}` / `stop`，不带回合 id | 每句带 `turn_id` 和句 id |
| 打断 | `abort{reason?}`，不带回合或句子标识；服务端立即回 `tts stop` | `abort` 带 `turn_id`，冲刷有起止帧 |
| 回合错误 | 无。识别为空时服务端什么都不发 | 回合级 `error` + 终态 |
| 超时 | 只有会话级（通道 120 s 无下行、服务端 120 s 无人声） | 回合级看门狗 |

它能跑通，靠的是三件事：**设备松键即回 Idle、下行音频只在 Speaking 态入队**（`application.cc` 的 `OnIncomingAudio`）、**服务端用一个每轮生成的 uuid（代码里叫 `sentence_id`，实为回合级）丢弃旧回合的残留音频**（`sendAudioHandle.py`）。设备内部另有 `playback_generation_` 计数防残余包，但不上协议。代价是：用户分不清"没听见"和"还在想"；设备无法把迟到的帧归到正确回合。

### 6.2 其他项目

- **unmute**：会话状态只有三态（`bot_speaking` / `user_speaking` / `waiting_for_user`），完全由对话历史最后一条推出；一轮结束时追加一个空 user 条目表示"轮到用户"。没有显式 turn id，用历史长度充当代际。
- **LiveKit**：以 `SpeechHandle` 表示"一次发言"，可以跨多个步骤（LLM → 工具 → LLM），带优先级、可打断、可 await。
- **一份实践笔记**的设备协议（按键说话的玩偶）就是本页规则的来源：`listen.start / stop / detect`、带 `turn_id` 的 `stt` 回显（15 s 软确认）、`tts.start / sentence_start / stop`、回合级 `error`（`no_speech / asr_timeout / upstream_unavailable`），以及带事件 id、要求 ack 的 GPS / NFC 事件。它的 `listen.*` 语义是回合边界，后续设计里改名为 `turn.*`，以免和"麦克风上行窗口"混淆。

---

## 7. 代表项目

| 项目 | 说明 |
|---|---|
| [xiaozhi-esp32](../04-projects/devices/xiaozhi-esp32.md) | 设备端状态机门控、`playback_generation_` |
| [xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md) | 服务端每轮 uuid 过滤、abort 处理 |
| [unmute](../04-projects/full-duplex/unmute.md) | 由历史推出状态 |
| [livekit-agents](../04-projects/frameworks/livekit-agents.md) | `SpeechHandle` |

---

## 8. 涉及的机制

| 机制 | 关注点 |
|---|---|
| [turn-detection](../03-mechanisms/turn-detection.md) | 边界从哪来：按键、VAD、语义判停、补充窗口 |
| [interruption](../03-mechanisms/interruption.md) | 打断的帧序列：设备停播 → 回执 → 代际 +1 → 冲刷 → 上游 cancel / truncate |
| [session-recovery](../03-mechanisms/session-recovery.md) | 断线时未关闭回合怎么了结 |
| [evaluation](../03-mechanisms/evaluation.md) | 每回合一条 trace，终态和触发原因进 trace |

相关结构决策：[floor-control](floor-control.md)（回合内外谁能出声）、[state-and-context](state-and-context.md)（回合结束时写快照）。基础概念见 [transport](../01-foundations/transport.md)。

---

## 待确认

- 看门狗各计时器的默认值都是建议值，没有在设备和网络上实测；只有 15 s 回显超时是现有实践值。
- 小智 `Speaking → Listening` 切换是否一定清空本地缓冲（取决于当时 AudioProcessor 状态），点击打断路径下可能残留最多约 1.2 s 已缓冲音频。
- 小智 auto / realtime 模式下识别失败时设备有无 UI 兜底（只读了主流程）。
- `holding` 补充窗口的时长（建议 600 ms，上限 1 s）对儿童语音是否够。

---

素材来源：一份实践笔记（未发布）§2.1、§5.1、§5.6；设计规范 `specs/01-turn-model-and-client-protocol.md`（未发布）；调研笔记 `notes/xiaozhi.md`（未发布） §1、§2.1、§2.3；调研笔记 `notes/unmute.md`（未发布） §1；`xiaozhi-esp32/main/protocols/protocol.cc`、`xiaozhi-esp32-server/main/xiaozhi-server/core/handle/sendAudioHandle.py`。
