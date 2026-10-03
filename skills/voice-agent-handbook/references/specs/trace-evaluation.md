# trace 与评测：延迟口径、trace 字段表、评测分层、judge rubric、门禁

## 目录

1. 解决什么问题
2. 核心模型
   2.1 时钟与时间戳写法　2.2 三个锚点与 T0　2.3 有声音 / 有内容　2.4 延迟分段
   2.5 trace 字段表　2.6 评测分层与用例格式　2.7 judge rubric
3. 规则（不变量）
4. 可调参数
5. profile 差异
6. 验收清单
7. 产出骨架时的注意事项

---

## 1. 解决什么问题

"首音多少""这版比上版好不好""这轮算不算通过"必须只有一个答案。这个组件定义：每个回合一条字段固定的 trace（各条链路共用同一 schema）；从哪个时刻起算、到哪个时刻算"有声音"和"有内容"；L0 / L1 / L2 / 音频层四层评测的用例格式；逐轮二元的 judge rubric；以及 CI、发布前、线上三级门禁。评测先于方案：所有方案实验都用这里的 trace 和用例格式出数。

## 2. 核心模型

### 2.1 时钟与时间戳写法

1. 规范时钟是**网关单调时钟**。所有 `*_at_ms` 是相对本回合主锚点 T0 的毫秒整数（可为负）；另记 `t0_epoch_ms`（网关墙钟 Unix 毫秒）用于跨系统对齐。
2. 客户端时间点换算到网关时钟，并记 `anchors.clock_quality`：有时钟锚点 → `synced`；无锚点 → 网关收帧时刻减 RTT/2 → `rx_minus_half_rtt`；连 RTT 都没有 → `gateway_rx`。汇总按它分组，不混算。
3. 音频内部时间点用**采样计数**倒推，不用网关收帧时刻（网络卡顿后数据突发到达，收帧时刻不代表说话时刻）。
4. 单位：时长字段 `_ms`、时间点字段 `_at_ms`，都是毫秒整数；token、字符、次数为整数；比例为 0–1 浮点。
5. **不适用就省略，应测未测写 `null`**。汇总时 `null` 计入数据缺失率，不当 0。

### 2.2 三个锚点与 T0

| 锚点 | 字段 | 定义 | 怎么测 |
|---|---|---|---|
| T_ae 用户音频流结束 | `anchors.audio_end_at_ms` | 用户最后一个有声帧结束时刻，**不含尾部静音** | 网关本地 VAD 找最后一段语音的结束采样点，按距松键 / 判停的采样数换算；离线脚本直接给出合成音频里的语音结束位置 |
| T_rel 松键 | `anchors.release_at_ms` | 用户松开按键；合并多次按键时取最后一次 | 设备帧，按 2.1 换算 |
| T_ep 判停 | `anchors.endpoint_at_ms` | 系统判定"说完了"并开始请求回复的时刻 | 判停模块打点 |

| 场景 | T0（`anchors.primary`） |
|---|---|
| 按键说话（含有屏 profile 的按住说话降级） | T_rel（`release`） |
| 开放麦 / 唤醒 / 常听 | T_ep（`endpoint`） |
| 触控输入（点选、改字段） | 点击时刻 `anchors.tap_at_ms` |
| 事件回合（到点、传感器） | 事件到达网关 `anchors.event_at_ms` |
| 离线基准、测试脚本、跨 profile 对比 | T_ae（只有它在所有模式下都有定义） |

`anchors.primary` 的取值与所选锚点字段名对应：`release` / `endpoint` / `audio_end` / `tap` / `event`。能测到的锚点**全部**写进 trace。两个派生量必须报：`ptt_tail_silence_ms` = T_rel − T_ae（按键模式下用户说完到松键的静音，只监控不设预算）；`endpoint_ms` = T_ep − T_ae（开放麦判停段）。报数一律注明锚点，如"有内容 P50 1.05 s（T_rel）"。

### 2.3 有声音与有内容

- **有声音** `sound_first_at_ms`：本回合第一次有**非静音音频**开始送给用户，来源不限（提示语、前导语、错误话术都算）。屏幕变化不算。
- **有内容** `content_first_at_ms`：本回合第一次有**回应用户请求的音频**开始送给用户。任何报表不得只写"首音"。
- **计时点**：该段输出第一帧**送出网关**的时刻 + 开头静音时长（裁掉首部静音后计时）。预合成提示语已裁静音，偏移为 0；流式 TTS / S2S 输出在编码前 PCM 上检测首个非静音样本：10 ms 窗 RMS 高于 −45 dBFS（建议值）。播放回执到了另记 `*_played_at_ms`；门禁用送出口径。
- 因为有内容的输出也是有声音的输出，**有内容 ≥ 有声音**恒成立。

| 输出 | `kind` | `content_role` | 有声音 | 有内容 |
|---|---|---|---|---|
| 预合成提示语（"我查一下"） | `cue` | `filler` | 是 | 否 |
| 模型在 function call 之前说的前导语 | `speech` | `filler` | 是 | 否 |
| "正在查"类语音填充、承接句 | `speech` | `filler` | 是 | 否 |
| 模型普通回复 / 工具结果回传后再生成的回复 | `speech` | `answer` | 是 | 是 |
| 本地 TTS 直念工具结果 | `speech` | `answer` | 是 | 是 |
| 委派后台流式文本 → 本地 TTS | `speech` | 首段是承接句为 `filler`，否则 `answer` | 是 | 看 role |
| 秒级写操作"已受理"进度收口 | `speech` | `progress` | 是 | 是，`content_kind=progress` 单独统计 |
| 回合级错误话术（"没听清"） | `speech` | `error` | 是 | 否，`turn_terminal=failed` |
| 事件播报 | `narration` | `answer` | 对事件回合计 | 对事件回合计 |

补充：有内容的计时点是**开始送给用户**的时刻，不是准备好的时刻；正文排在提示语后面时另记 `content_first_ready_at_ms`（正文第一帧到达仲裁器）和 `queue_wait_ms`（送出 − 就绪）。`content_role` 判不准时以 L1 judge 为准（judge 判 `continue` 说明不是内容）。有屏 profile 的屏幕另记 `ui_feedback_first_at_ms` / `ui_content_first_at_ms`，不并入首音。

### 2.4 延迟分段（都在 `latency` 对象下）

| 字段 | 类型 | 定义 | 预算（无屏 / 有屏） |
|---|---|---|---|
| `sound_first_at_ms` | 累计 | T0 → 有声音 | ≤ 900（T_rel）/ ≤ 1000（T_ep） |
| `content_first_at_ms` | 累计 | T0 → 有内容 | 普通 ≤ 1000、逐字念工具 ≤ 1100、组织语言工具 ≤ 1300 / 语音 ≤ 1500、工具回合 ≤ 2000 |
| `content_first_ready_at_ms` | 累计 | T0 → 正文第一帧到达仲裁器 | — |
| `queue_wait_ms` | 段 | 正文就绪 → 正文送出 | — |
| `cue_to_content_gap_ms` | 段 | 第一段 `filler` 播完 → 有内容 | ≤ 1500（无屏） |
| `content_kind` | enum | `answer` / `progress` / `none`（本回合无内容） | — |
| `endpoint_ms` / `ptt_tail_silence_ms` | 段 | 见 2.2 | 判停 300–600，不超过 2500 / 只监控 |
| `stt_final_at_ms` | 累计 | T0 → 识别定稿 | 实践值约 370（T_rel） |
| `seg.stt_first_word_ms` / `seg.vad_ms` / `seg.llm_ms` / `seg.tts_start_ms` | 段 | 首个语音帧 → 首个转写词 / T_ae → 发起回复请求 / 发起请求 → 首个 token 或音频 delta / LLM 首词 → 首个音频包（S2S 省略） | 普通回合 + T_ae 口径下，首音 ≈ vad + llm + tts_start 可作一致性检查 |
| `fc_received_at_ms` | 累计 | T0 → 收到第一个完整 function call | 工具回合 |
| `pre_tool_ms` | 段 | 工具前 = T0 → 第一个工具开始执行 | 工具回合 |
| `tool_done_at_ms` | 累计 | T0 → 产出本回合内容的调用完成（多调用取有内容前最后完成的） | 工具回合 |
| `output_sent_at_ms` | 累计 | T0 → function output 回传上游 | 有回传时 |
| `post_tool_ms` | 段 | 工具后 = `tool_done_at_ms` → 有内容 | 工具回合 |
| `regen_ms` | 段 | 回传 → 模型再生成首个音频（`speak_mode=model`） | — |
| `local_speech_first_audio_at_ms` / `model_speech_first_audio_at_ms` | 累计 | 本地直念 / 模型出声第一帧 | — |
| `sound_first_played_at_ms` / `content_first_played_at_ms` | 累计 | 回执口径 | 校验设备缓冲 + 下行 |

示意（按键、逐字念工具回合、短 cue）：T_ae −180 → T_rel=T0 0 → stt_final 370 → fc_received 800 → tool_done 815 → cue 有声音 830 → 短 cue（约 280 ms）播完 1110 → 有内容 ≈ 1120。用长 cue（约 760 ms）时有内容会推到约 1600，达不到 1100 预算；口径不改，改为让工具层选短 cue（≤ 300 ms）并预合成直念的固定前缀，工具在 cue 开播前已完成则不播 cue。

### 2.5 trace 字段表

每回合一条，按 `(session_id, turn_id)` 唯一；各组件只写自己的字段，网关收集器合并；回合终态后再等最后一个播放回执或上游 usage，最多 5 s（建议值），超时写出、未到字段记 `null`。必填：M = 都必填；M-A / M-B = 仅无屏 / 有屏必填；C = 条件必填；O = 可选。

**标识与版本**

| 字段 | 类型 / 取值 | 必填 | 说明 |
|---|---|---|---|
| `trace_schema_version` | string | M | 如 `"06-v0.1"` |
| `profile` | `A` / `B` | M | A = 无屏设备，B = 有屏 App |
| `session_id` / `user_session_id` | string | M | 连接会话 / 用户会话（可跨连接） |
| `turn_id` | string `c<n>` / `s<n>` | M | 客户端 / 网关生成；合并回合用合并后的值 |
| `generation` | int | M | 仲裁器代际 |
| `turn_source` | `button` / `wake` / `vad` / `text` / `event` | M | |
| `turn_input` | `typed` / `tap` / `test` | C（`text`） | |
| `t0_epoch_ms` | int | M | |
| `device_id` / `user_id_hash` | string | M-A / M-B | |
| `pipeline` / `provider` / `model` | string | M | 如 `s2s:<vendor>`、`cascade:<asr>+<llm>+<tts>` |
| `prompt_version` | string | M | `<pipeline 前缀>/<persona>@<v>+tools@<v>+state@<v>` |
| `gateway_build` | string | M | 代码版本 |
| `route` / `risk_level` | `s2s`/`cascade`/`half_cascade` ; `low`/`high` | M-B | 按回合选路 |

**决策**：`turn_kind`（M，`chat` / `tool_verbatim` / `tool_compose` / `tool_write` / `event` / `ui_input` / `error`，汇总必须按它分组）、`user_text`（M）、`action`（M，无动作写 `none`）、`decision_layer`（M，`rule` / `model` / `correction` / `gate`）、`tool_calls[]`（C）、`slots` / `entity_resolution`（O）、`speak_mode`（C 工具回合，`verbatim` / `model` / `delegated` / `screen` / `none`）、`write_gate`（C，`passed` / `blocked` / `n_a`）、`stage` / `visible_actions_version` / `form_version_before` / `form_version_after`（M-B）。

**锚点**：`anchors.primary`（M）、`anchors.clock_quality`（M）、`anchors.audio_end_at_ms`（M，有音频输入）、`anchors.release_at_ms`（C 按键）、`anchors.endpoint_at_ms`（C 开放麦）。延迟字段见 2.4：`sound_first_at_ms` / `content_first_at_ms` / `content_kind` 为 M，`filler` 相关为 C，工具分段为 C（工具回合），`ui_*` 为 M-B。

**工具调用 `tool_calls[]`（每个调用一项）**

| 字段 | 取值 | 说明 |
|---|---|---|
| `call_id` / `name` / `args` | | `args` 存原始参数，归一化值放 `slots` |
| `presentation` | `verbatim` / `model` / `screen` / `silent` | 结果怎么到达用户 |
| `is_write` / `latency_class` | bool ; `ms` / `seconds` / `background` | |
| `tool_route` | `A0`（模型再生成）/ `A1`（本地直念）/ `A2`（委派后台）/ `A3`（进度收口）/ `silent` | 出声路线 |
| `route_fallback_reason` / `phase` / `gate_result` | | 条件必填 |
| `generation` / `late_result_dropped` | int | 登记时代际；终态后被拒的迟到结果数 |
| `started_at_ms` / `done_at_ms` / `tool_exec_ms` / `settled_at_ms` | int | `settled_at_ms` 之后到达的结果一律拒绝 |
| `reply_required` | bool | 回传后是否再生成；与收口是两件事 |
| `closed_by` | `result` / `interrupted` / `cancelled` / `progress` / `superseded` / `duplicate` | **每个调用必须有值**，缺值即悬挂调用 |
| `interrupt_phase` | `received` / `running` / `done_not_sent` / `sent_speaking` / `progress_closed` / `narration` / `delegating` / `n_a` | C（被打断） |

**会话与缓存**：`session_seq`（M）、`rebuild_reason`（M，`none` / `disconnect` / `upstream_error` / `context_overflow` / `state_change` / `turn_limit` / `scheduled` / `modality` / `app_resume`）、`history_format` / `history_scope` / `history_items`（C 本回合重建）、`handoff_start_at_ms` / `handoff_done_at_ms`（C）、`state_block_version`（M）、`state_update`（M，`none` / `immediate` / `lazy`）、`cached_input_tokens` / `uncached_input_tokens`（上游提供时 M）、`reply_modality`（M，`audio` / `text_only` / `mixed` / `none`）、`turns_in_upstream_session`（M）。

**判停与打断**：`press_ms`（C 按键）、`speech_ms`（M）、`server_vad_segments_in_turn`（M-A）、`compressed_silence_ms`、`dropped_short_press`（M-A）、`smart_turn_prob`、`endpoint_wait_ms`、`supplement_window_hit`、`interrupted`（M）、`interrupt_at_ms` / `interrupt_source`（C，`button` / `vad` / `wake` / `text` / `link` / `critical` / `guardrail`）、`false_interrupt_filtered`（O）。

**仲裁与播放**：`outputs[]`（M，每项 `{request_id, origin, kind, content_role, generation, sentence_id, sent_at_ms, played_ms, status}`，`status` ∈ `played` / `truncated` / `flushed` / `dropped_stale` / `dropped_expired` / `dropped_duplicate` / `dropped_overflow` / `dropped_retry_exhausted`）、`double_voice_count`（M）、`stale_frames_dropped`、`lease_violations`、`queue_truncated`、`drops_by_reason{stale, expired, duplicate, overflow, retry_exhausted}`、`flush_count` / `flush_ms_max` / `flush_timeouts`、`receipt_timeouts{started, ended}`、`consumer_dropped_frames`（应为 0）、`suppressed_frames`、`receipt_mode`（`receipts` / `estimated`）、`truncate_estimated`（C）。

**传输与错误**

| 字段 | 取值 | 必填 |
|---|---|---|
| `uplink_seq_gaps` / `downlink_lead_ms` | int | M-A |
| `uplink_jitter_ms` / `rtt_ms` | int | M |
| `turn_terminal` | `completed` / `interrupted` / `discarded` / `failed` / `cancelled` | M |
| `turn_closed_by` | `completed`：`playback_ended` / `T_drain`；`discarded`：`short_press` / `short_speech` / `false_wake` / `rule_silence`；`interrupted`：打断来源；`failed`：错误码 | M |
| `error_code` | `no_speech` / `asr_timeout` / `upstream_unavailable` / `upstream_timeout` / `upstream_rejected` / `tts_failed` / `audio_gap` / `link_lost` / `protocol_error` / `session_expired` / `internal` | C（`failed`） |
| `error_class` | `fatal` / `transient` / `context_overflow` / `benign` | C（有上游错误） |
| `restart_attempts_in_turn` | int，每回合最多 3 | M |
| `watchdog_fired` | `none` / `T_asr` / `T_filler` / `T_respond` / `T_drain` / `T_intr_receipt` / `T_listen_max` / `tool_timeout` | M |
| `stuck` | bool：兜底后仍无终态，或无声超过 `T_filler` + 500 ms = 2.5 s 且无填充 | M |

**usage**（`usage` 对象，按 response 逐条记）：`usage_semantics`（M，`net` 输入不含缓存 / `gross` 含缓存，汇总时不得把缓存加回 `gross`）、`responses[]{response_id, response_status(completed/cancelled/failed/incomplete), interrupted, input_text_tokens, input_audio_tokens, cached_input_tokens, cached_text_tokens, cached_audio_tokens, output_text_tokens, output_audio_tokens}`、`generated_audio_ms`、`played_audio_ms`、`input_audio_ms`、`local_tts_chars`、`asr_audio_ms`（C）、`upstream_session_duration_ms`（C）。只记量不记金额；被打断回复照样记 usage。

**有屏 profile**：`ui_events[]{type, at_ms, target, sentence_id?}`（`state_change` / `transcript_shown` / `card_shown` / `field_shown` / `option_tap` / `field_edit` / `confirm_tap` / `hangup` / `handoff_tap`）、`ui_audio_skew_ms`、`handoff_to_human`、`guardrail_result`（`pass` / `rewrite` / `block` / `n_a`）。
**无屏 profile**：`device_events[]{event_id, type, at_ms, disposition, acked, deduped}`、`playback_receipts[]{sentence_id, request_id, started_at_ms, ended_at_ms, interrupted, played_ms, reason?}`、`receipt_missing`、`narration_attempts` / `narration_delivered`、`stt_echo_timeout`、`feedback_signal`（`none` / `led` / `tone`）。

**汇总与导出**：轮 → 会话 → 设备 / 用户 → 天；切分维度 `prompt_version`、`pipeline` / `route`、`turn_kind` + `speak_mode`、`anchors.primary` + `clock_quality`、`profile`、人群分层。OTel：每回合 span `voice.turn`，子 span `voice.stt` / `voice.llm`（或 `voice.s2s.response`）/ `voice.tool.<name>` / `voice.tts` / `voice.playback`；usage 用 `gen_ai.usage.*`，其余属性用 `voice.<trace 字段名>`；指标标签只用低基数维度，`session_id` / `turn_id` 只放 span。

### 2.6 评测分层与用例格式

| 层 | 测什么 | 怎么跑 | 门禁 |
|---|---|---|---|
| L0 决策 | 给定文本和状态，动作 / 工具 / 参数 / 终态对不对 | 进程内，mock 工具，毫秒级 | 每次提交 |
| L1 对话 | 多轮脚本：状态变化、指代、打断、重连、不支持的请求、延迟预算 | 模拟客户端 + 模拟后端 + 真上游；文本与音频两种模式 | 发布前 |
| L2 线上只读 | 真实会话上跑同一套逐轮 rubric | 会话结束后离线跑，禁止写操作 | 持续看板 + 告警 |
| 音频层 | ASR、判停、TTS 在真实声学条件下 | 合成 / 录音 + 噪声注入（SNR 20 / 10 / 5 dB）+ 真链路 | 换 ASR / TTS / VAD 时必跑 |

L0 用例（JSONL 一行一个）：

```json
{"id": "status_query_003", "set": "blind-zh-v1", "profile": ["A", "B"], "tags": {"category": "query"},
 "state": {"stage": "in_progress", "task_active": false},
 "history": [{"user": "接下来做什么", "assistant": "下一步是确认地址。"}],
 "turns": [{"user": "那个怎么弄",
            "asr_variants": [{"kind": "homophone", "text": "那个怎么农"}, {"kind": "truncation", "text": "那个怎"}],
            "expected_calls": [{"name": "get_next_step", "args_subset": {"target": "address"}}],
            "forbidden_calls": ["start_task"]}],
 "expected_final_state": {"task_active": false}}
```

`asr_variants[].kind` ∈ `homophone` / `truncation` / `reduplication` / `accent`（可加 `term_error`），盲测集每句 2–3 个；参数用别名表规则比对，不用 LLM 判。指标分开命名：`action_hit`、`slot_hit`、`no_tool_correct`、`write_false_trigger`（分母为所有不该触发写操作的轮）、`final_state_match`；原句与各 ASR 变体分开报。只有"超时且无工具调用也无文字"才重试一次。

L1 脚本（YAML 一文件一脚本）关键构件：`race` / `repeat`（竞态脚本按通过率）、`send_after: {event, delay_ms}`（锚定 `llm_started` / `sound_first` / `content_first` / `fc_received` / `playback_started` 后插话）、`absent: true` + `within_ms`（时限内不应出现）、`sound_first` / `content_first` 的 `within_ms`（按 2.2 的 T0 计）、`function_call` + `args_subset`、`tool_closed.closed_by`、`eval: [rubric_id]`、`persona` / `goal`（模拟用户，至少两个 persona）、`assert_session: {stuck: 0, double_voice_count: 0, hanging_calls: 0}`。失败类型聚合为 `timeout` / `judge_no` / `missing_function_call` / `latency_budget` / `unexpected_event` / `infra`。必备脚本组：插话打断、工具各阶段打断、噪音不回应、不支持的请求、断线重连 / 故障注入、指代与早期信息召回。

L2：judge 输入用"用户实际听到的"文本（按 `played_ms` 截断，打断处标 `[interrupted]`）；任一 rubric 判 no 或硬指标越界（`stuck`、`double_voice_count > 0`、有内容超预算 2 倍）进待归因队列，根因标签 `asr` / `endpoint` / `decision` / `tool_data` / `presentation` / `latency` / `judge_error`，代表性样本补回 L0 / L1。

### 2.7 judge rubric

```yaml
rubric_id: recovery
version: 2
scope: per_turn
inputs: [context, latest_bot_reply, tool_calls, tool_results, heard_text]
criterion: >
  当回复拒绝了或做不到用户这一轮的某个请求时，回复要给出至少一个具体、可行、
  来自工具结果或已知数据的替代（一次最多两个）；没有拒绝任何请求的回复算通过。
options: {yes: …, no: …, not_applicable: 这一轮用户没有提出请求, continue: 只是提示语或承接句}
leniency: [按口语意思判，不因转写错误判 no]
reason_required_for: [no]
pair_with: honesty
```

输出：`{session_id, turn_id, rubric_id, rubric_version, verdict: yes|no|not_applicable|continue, reason(仅 no), judge: {model, prompt_version, temperature: 0}}`。基础标准集：`understanding`、`honesty`（声称已完成时要有支持它的工具结果）、`recovery`（与 honesty 配对）、`efficiency`、`brevity`（规则判：无屏 ≤ 2 句，有屏语音 ≤ 3 句，建议值）、`task_completion`（会话级）、`tone_consistency`。

## 3. 规则（不变量）

1. **报首音必须说是有声音还是有内容，并注明锚点。** 为什么：提示语只解决有声音，不能冒充有内容；锚点不同的数差几百毫秒。
2. **T_rel 与 T_ae 分开记。** 为什么：按键模式下用户说完到松键的静音通常不为零，混成一个点会把用户的时间算成系统延迟或反之。
3. **有内容按开始送给用户的时刻计，不按就绪时刻；不为凑预算改口径。** 为什么：用户感知的是听到，排在提示语后面的等待是真实延迟。
4. **首音裁掉首部静音后计时；测试脚本按实时节奏逐帧发音频，只在确认收到音频帧时停表。** 为什么：静音帧、控制帧、一次性灌入都会让首音虚低。
5. **缺失写 `null`、不适用省略，二者不混。** 为什么：`null` 当 0 会把丢回执的回合算成零延迟。
6. **每个工具调用必须有 `closed_by`（六值之一）和 `settled_at_ms`。** 为什么：没有终态的调用就是悬挂调用，会在后续轮次产生迟到结果和重复播报。
7. **延迟只报 P50 / P90，附 n 和失败率；失败不进延迟分布。** 为什么：平均值会被一次冷连接拉偏，失败混进分布会掩盖可用性问题。
8. **被打断的回复照样记 usage；trace 只记量不记金额。** 为什么：被打断的生成也计费；价格表会变。
9. **竞态用例只按通过率进门禁；`stuck`、双声、写操作重复执行出现一次即失败。** 为什么：竞态单次结果没有意义，但这几项是正确性要求不是概率问题。
10. **judge 逐轮二元判定、能看到工具结果、条件式写法、`not_applicable` 和 `continue` 作为选项；能量化的用规则判。** 为什么：部分分和"never/always"误读会让分数漂移；看不到工具结果就判不出诚实。
11. **冻结盲测集从不用于调参；被针对性调试过的样本移入训练集并发新版本。** 为什么：训练句 100%、盲测 54% 这类差距只有盲测集能暴露。
12. **模拟用户只用来找回归，不报绝对分。** 为什么：LLM 扮的用户说话太完整，且每次对话不同。

## 4. 可调参数

| 参数 | 值 | 性质 |
|---|---|---|
| 首部静音阈值 | 10 ms 窗 RMS > −45 dBFS | 建议值，需在样本集上校准 |
| trace 收尾等待 | 回合终态后最多 5 s | 建议值 |
| `stuck` 无声阈值 | `T_filler`（2.0 s）+ 500 ms = 2.5 s | 建议值 |
| 延迟预算 | 见 2.4 | 目标值 |
| 短 cue 长度 | ≤ 300 ms（示意 280 ms） | 建议值 |
| 样本量下限 | P50 n ≥ 20；P90 n ≥ 50（不足标"样本不足"且不进门禁）；通过率每条 N ≥ 10 | 前者实践值，后者建议值 |
| CI L0 允许降幅 | 每个命名指标 ≤ 2 个百分点；`write_false_trigger` 不许上升 | 建议值 |
| 竞态门槛 | 通过率 ≥ 90%；新脚本连续两个基线版本差 ≤ 10 个百分点才转门禁 | 建议值 |
| 无 P90 目标时 | P90 ≤ P50 目标 × 1.5 | 建议值 |
| judge 逐轮通过率 | 不低于上一版减 3 个百分点；honesty 不许下降 | 建议值 |
| judge 校准 | 人工标注一致率 ≥ 85%；判 yes 抽查 ≥ 5%，每周 | 建议值 |
| L2 抽样 | 每天每个 `prompt_version` ≥ 200 轮，低分全量 | 建议值 |
| `ui_audio_skew_ms` | ≤ 200 | 建议值 |

## 5. profile 差异

| 项 | 无屏设备（A） | 有屏 App（B） |
|---|---|---|
| 主锚点 | 按键 T_rel；开放麦 T_ep | T_ep；按住说话降级 T_rel；点选为点击时刻 |
| 首音 | 只有音频 | 音频同 A；另记 `ui_feedback_first_at_ms` / `ui_content_first_at_ms`，不并入 |
| 延迟预算 | 有声音 ≤ 0.9 s；有内容 1.0 / 1.1 / 1.3 s；首句到正文 ≤ 1.5 s | 判停 0.3–0.6 s；有声音 ≤ 1.0 s；有内容 ≤ 1.5 s；工具回合 ≤ 2 s |
| 独有字段 | `device_events`、播放回执、`receipt_missing`、`narration_*`、`server_vad_segments_in_turn`、短按丢弃、补充窗口、`downlink_lead_ms`、`stt_echo_timeout` | `ui_events`、`ui_audio_skew_ms`、`route` / `risk_level`、`stage`、表单版本、`guardrail_result`、`handoff_to_human` |
| 独有评测 | 目标人群录音集（分年龄段、≥ 1/3 句中停顿 ≥ 400 ms）、设备事件插话、无声 > 2.5 s 即 `stuck`、`brevity` ≤ 2 句 | 任务完成率、结构化抽取准确率（字段级 precision / recall）、产出物质量、UI 同步、高风险回合走文本先于语音的比例 100%、护栏拦截率与误拦率 |

## 6. 验收清单

- [ ] 每个回合恰好一条 trace，能过 JSON Schema 校验，`trace_schema_version` 正确。
- [ ] 能测到的锚点都写了；`anchors.primary`、`clock_quality` 必填；时间字段是相对 T0 的毫秒整数。
- [ ] 有声音 / 有内容按 2.3 判定；`outputs[]` 每段带 `kind` 与 `content_role`；首部静音裁掉后计时；有内容 ≥ 有声音。
- [ ] 工具回合有 `pre_tool_ms` / `post_tool_ms`；每个调用有 `closed_by` 与 `settled_at_ms`。
- [ ] usage 按 response 逐条记、带 `usage_semantics`；被打断的回复有 usage；`played_audio_ms` 与 `generated_audio_ms` 都有。
- [ ] 缺失写 `null`、不适用省略，没有混用。
- [ ] OTel usage 用 `gen_ai.usage.*`，其余 `voice.*`；指标标签无高基数字段。
- [ ] 报表只报 P50 / P90 并附 n 与失败率；n 不够标"样本不足"。
- [ ] L0 / L1 / rubric 格式一致，另一个 profile 的共跑用例能直接跑。
- [ ] 冻结盲测集有版本清单（版本名、冻结日期、样本数、分层、覆盖清单、来源、标注规范版本、校验和），CI 单列盲测结果。
- [ ] 竞态脚本标 `race: true`，只按通过率进门禁；硬指标 `stuck` / 双声 / 悬挂调用 / 写重复执行均为 0。
- [ ] judge 能看到工具结果；判 yes 有抽查复核记录。

## 7. 产出骨架时的注意事项

- 先产出 trace 的 JSON Schema 和对应的类型定义，字段名、枚举、单位逐字照 2.5；schema 与代码同仓，CI 用它校验普通、工具、打断、错误四类回合的样例 trace。
- trace 收集器按 `(session_id, turn_id)` 合并各组件写入的片段；各组件只写自己的字段，不读别人的字段做推断。
- 时间全部用网关单调时钟打点，落 trace 前统一减去 T0；客户端时间点在入口处换算并带 `clock_quality`。
- `kind` / `content_role` 由产生输出的组件在入队仲裁器时填，仲裁器据此算 `sound_first_at_ms` / `content_first_at_ms`；不要在汇总阶段反推。
- 汇总脚本按 `turn_kind`（至少普通 / 工具）× `anchors.primary` × `clock_quality` 分组，输出 n、缺失率、P50、P90 和"样本不足"标记。本 skill 的 scripts/trace_check.py 是一个可直接改造的起点：它校验最小必填集、时间单调、首音口径和终态枚举，并按回合类型汇总两种首音的 P50 / P90。
- 评测 harness 的用例、脚本、rubric 文件格式照 2.6、2.7；judge 调用固定 temperature 0，把 judge 模型和 prompt 版本写进结果。
- 业务专属指标（任务完成率、抽取准确率等）先只报数不设门槛，积累两个版本基线后再定。
