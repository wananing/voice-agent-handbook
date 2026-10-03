# 上游适配层：能力声明、I/O 原语、事件流与 P0 探针

## 目录

1. 解决什么问题
2. 核心模型：能力声明 / I/O 原语 / 标准化事件流 / 错误四分类
3. 规则（不变量）
4. 可调参数
5. profile 与链路差异
6. P0 探针流程
7. 验收清单
8. 产出骨架时的注意事项

---

## 1. 解决什么问题

语音 agent 要接的上游有四类：闭源 S2S、自托管 S2S、级联（STT → 文本 LLM → TTS）、半级联（音频 LLM 只出文本 → TTS）。如果每条链路各写一套"何时请求回复、怎么灌历史、重连几次、何时换会话"，同一个问题会出现几种答案。适配层的做法是：**差异写成数据（能力声明），行为收成九个原语，策略全部上移到链路无关层**。适配层只做协议编解码、字段映射、镜像登记、事件归一、错误归类。

```
链路无关层（策略）：回合状态机 · 仲裁器 · 会话重建器 · 工具层 · 护栏 · 分流 · trace
        │ 原语调用                         ▲ 标准化事件
┌───────▼──────────── 链路适配层 ──────────┴───────────┐
│ 能力声明（静态数据 + 探针证据）                         │
│ 适配器：S2S 厂商 A │ S2S 厂商 B │ … │ 级联 │ 半级联     │
└──────────────────────────────────────────────────────┘
        ▼ 各厂商原生协议 / 自托管推理服务
```

## 2. 核心模型

### 2.1 能力声明格式

每个适配器附一份能力声明，随适配器一起版本化。每个字段是"值 + 证据"：

```yaml
adapter: <adapter_name>
upstream: { vendor: <vendor>, model: "<model-id>", api_version: "<ver>" }
probed_at: null                    # P0 探针完成后填日期
fields:
  turn.server_detection_disable:
    value: yes                     # 取值见 2.2；未知写 unknown
    evidence: code_read            # probed > doc > code_read > unknown
    refs: ["<trace 编号或文档位置>"]
```

`unknown` 一律按该字段的"保守值"运行，直到探针给出结论。

### 2.2 能力字段表

| 字段 | 取值（保守值） | 含义 → 对策略的影响 | 探针 |
|---|---|---|---|
| `turn.server_detection_disable` | `yes\|no\|unknown`（`no`） | 能否完全关掉服务端判停 → `yes` 时按键 / 本地判停直接驱动 `commit_turn` | P0-2 |
| `turn.segment_only` | `yes\|partial\|no\|unknown`（`no`） | 保留服务端切段但不自动回复 → `no` 时只能本地处理音频并扣住松键前的 function call | P0-2 |
| `turn.server_interrupt_optional` | `yes\|no\|unknown`（`no`） | 用户开口时能否不自动取消回复 → `no` 时"打断只认按键"管不到上游生成 | P0-2 |
| `turn.server_silence_ms` | `int\|range\|unknown` | 服务端判停实际静音时长 → 决定停顿压缩阈值 | P0-3 |
| `turn.sensitivity_levels` | `list\|none` | 关不掉时的灵敏度档位 → 至少调到最不敏感一档 | P0-3 |
| `turn.commit_mode` | `commit_create\|activity_window\|whole_utterance\|server_only\|none` | 客户端怎样宣告"输入结束" → 决定 `commit_turn` 的翻译 | P0-4 |
| `turn.commit_min_ms` | `int\|unknown` | 提交所需最短音频 → 短按过滤阈值不得低于它 | P0-4 |
| `turn.empty_commit_behavior` | `error(code)\|ignored\|unknown` | 服务端已自行提交时空 commit 的反应 → 是否进 benign 映射 | P0-4 |
| `turn.input_mute` | `yes\|no\|unknown` | 能否让上游一段时间"不听" → 关不掉判停时的门控杠杆 | P0-11 |
| `history.injection` | 集合 ⊂ `init_only\|packed_item\|per_item\|client_content`（`{init_only}`） | 上游接受历史的方式。集合里有 `per_item` 不代表它有效 | P0-9 |
| `history.mid_session_effective` | `yes\|no\|unknown`（`no`） | 中途插入的 system / user 条目模型是否真的看 → `no` 时状态只能走配置更新、按回复覆盖或重建 | P0-9 |
| `history.limits` | `{max_items?, max_tokens?, max_item_bytes?}` | 建会话时历史上限 → 裁剪在策略层，适配层只校验 | 读文档 |
| `history.constraints` | 规则列表 | 结构要求（以 user 开头 / 结尾、音频前发完）→ 策略层渲染时满足，适配层只校验 | P0-9 |
| `history.text_flips_modality` | `yes\|no\|unknown`（`yes`） | 灌文本历史后是否改用文本回复 → trace 必须记 `reply_modality` | P0-9 |
| `history.tool_calls` | `keep\|as_text\|drop\|unknown` | 工具调用在历史里怎么保留 → `drop` 时关键结果进状态块 | P0-9 |
| `history.item_id_echo` | `server_id\|client_id\|none` | 是否回显条目 id → `none` 时镜像只能按顺序对齐 | 插入带 id 条目 |
| `history.item_ack` | `yes\|no\|unknown` | 插入条目有无确认 → `no` 时 `open` 就绪只能靠超时 | 同上 |
| `config.update.<target>`（`instructions/tools/voice`） | `live_full\|live_sparse\|append_only\|next_session` | 会话中途更新方式 → `next_session` 的项只能攒到换会话 | P0-6 |
| `config.update_invalidates_cache` | `yes\|no\|unknown`（`yes`） | 更新后是否整段重新预填 → `yes` 时少改、晚改 | P0-6 |
| `config.append_channels` | 列表 `instructions\|thinking\|commentary` + `max_tokens` | 不重写前缀的追加通道 → 状态注入旁路 | P0-5 |
| `config.update_ack` | `yes\|no` | 配置更新有无确认 → `update_config` 完成判定 | 读协议 |
| `response.client_initiated` | `yes\|no` | 客户端能否主动发起回复 → `no` 时除本地直念外的收口路径不可用 | 读协议 |
| `response.overrides` | `{instructions: replace\|none, modalities, tools, tool_choice: bool, instructions_fallback: history_item\|none}` | 按回复覆盖 → 可用时状态块优先随回复下发 | P0-5 |
| `response.single_slot` | `yes\|no\|unknown`（`yes`） | 同一时刻只允许一个回复 → `yes` 时上层串行化 | P0-8 |
| `response.say` | `native\|via_local_tts\|none` | 上游能否按给定文本念 → 不逐字就走本地 TTS | P0-13 |
| `tools.calling` | `native\|prompted\|none` | 原生调用 / 靠提示词手写格式 / 无 → `none` 不能承接工具回合 | 跑一次调用 |
| `tools.auto_reply_after_result` | `yes\|no\|unknown`（`yes`） | 回传结果后会不会自己续答 → `yes` 时静默收口默认不可用 | P0-1 |
| `tools.silent_close` | `by_omission\|flag\|none\|unknown` | 静默收口怎么做 → 决定 `send_tool_result(reply_required=false)` 能否执行 | P0-1 |
| `tools.placeholder_accepted` | `yes\|no\|unknown` | 在途工具先回"进行中"假 output 是否被接受 | P0-12 |
| `tools.parallel_calls` | `yes\|no\|unknown` | 一次回复能否多个调用 | 构造双工具请求 |
| `tools.args_streaming` | `complete\|incremental` | 参数一次到齐还是分片 → 适配层拼完整再上报 | 读协议 |
| `truncate.mode` | `audio_ms\|text_rewrite\|local_only\|none` | 打断后怎样让上游知道"只听到前 N ms" → `none` 时只能在下次重灌时纠正 | P0-14 |
| `session.resumption` | `none\|handle` | 能否凭 handle 恢复 → 可先试续接，失败再重灌 | P0-6 |
| `session.max_secs` | `int\|unknown` | 上游硬时长上限（区别于客户端框架自定的回收周期） | 长会话实测 |
| `session.max_turns_retained` | `int\|unknown` | 上游实际保留轮数 → 主动换会话周期 N 小于它 | 40 轮回忆脚本 |
| `session.expiry_notice` | `yes\|no` | 到时限前是否预告 → 发 `session.expiring` | 读协议 |
| `session.idle_timeout_secs` | `int\|unknown` | 空闲会话被回收时间 → 预建会话保活策略 | 建会话不送音频 |
| `session.server_compaction` | `none\|sliding_window` | 服务端自行压缩上下文 → 默认不开 | 读协议 |
| `session.init_ready_signal` | `ack_event\|timeout` | `open` 何时算就绪 | 读协议 |
| `usage.cache_reported` | `none\|cached_tokens\|cached_tokens_by_modality` | 是否上报缓存命中 → `none` 时只能用首音差估算 | P0-7 |
| `usage.semantics` | 描述（`net` / `gross`） | 缓存 token 是否已含在输入里 → trace 按它归一 | 读文档 |
| `errors.code_map` | 原始码或消息子串 → `{class, reason}` | 错误码到四类的映射，随探针更新 | P0-8 |
| `errors.code_stability` | `stable_codes\|message_match` | 错误码稳定还是按消息子串匹配 | 看实际错误 |
| `errors.event_correlation` | `yes\|no` | 错误能否回指上行 `event_id` → 条目级错误只让这一条失败 | 发非法条目 |
| `output.modalities` | 子集 `audio\|text` | 只有 `text` 时必须接本地 TTS | 读协议 |
| `output.modality_switch` | `per_response\|per_session_live\|session_fixed` | 输出模态切换粒度 → 能否按回复选模态 | P0-5 |
| `output.transcript` | `yes\|no` | 音频输出带不带转写 → 没有就拿不到已播放文本 | 读协议 |
| `output.text_lead_ms` | `int\|unknown` | 文本比音频早多少；S2S 只早几十毫秒 → 视为无护栏钩子 | 记 delta 时间戳 |
| `output.audio_format` / `uplink.audio_format` | `{encoding, sample_rate, chunk_ms?}` | 适配层统一转码、分块 | 读协议 |
| `output.voice_prompt` | `yes\|no\|unknown` | 能否指定参考音色 | 主观评测 |
| `uplink.faster_than_realtime` | `tolerated\|degrades\|unknown`（`degrades`） | 音频突发快于实时时识别 / VAD 是否正常 → `degrades` 时上层限速 | P0-10 |
| `uplink.gaps` | `tolerated\|degrades\|unknown`（`degrades`） | 上行时间轴有空洞时的反应 → 能否删静音帧 | P0-10 |
| `uplink.streaming_input` | `streaming\|whole_utterance` | 能否边说边送 → `whole_utterance` 首音要加整段预填 | 读接口 |
| `cascade.user_transcript` | `native\|side_request\|none` | 用户转写来源（级联专用，其他填 `n/a`） | — |
| `cascade.pre_speech_hook` | `sentence\|none` | 出声前护栏钩子粒度 → 高风险回合只能走有钩子的适配器 | P0-15 |
| `cascade.prefix_cache` | `yes\|no\|unknown` | 自托管推理前缀缓存能否命中 | — |

### 2.3 I/O 原语

通用约定：每个原语返回 `Ack{ok, code?, class?, detail?}`；`ok=false` 时 `code ∈ unsupported(<能力字段名>) | invalid_state | slot_busy | timeout | upstream_error`。调用方传 `turn_id`、`response_ref`，适配层把上游 id（`item_id`、`response_id`、`call_id`）与之对应，每个上行事件带适配层生成的 `event_id`。`open` / `close` / `update_config` 同会话内串行，其余可并发但上行顺序与调用顺序一致。

| 原语 | 语义 | 按能力降级（要点） | 幂等 | 超时【建议】 |
|---|---|---|---|---|
| `open(SessionInit)` | 建新上游会话，把人设、`state_block{version,text}`、工具表、`history{format: pairs\|packed\|pairs_tools_as_text, items}`、`resume_handle`、`output_default` 一次带入（重灌）；初始化镜像 | 格式表达不了 → `unsupported(history.tool_calls)`；超限 → `invalid_state(history_over_limit)`；违反结构 → `invalid_state(history_constraint)`；不补条目、不截断 | 不幂等，一个 handle 只 open 一次 | 连接 10 s；条目确认 5 s；无确认等固定时长 |
| `send_audio(frame, meta)` | 送一帧用户音频，`meta{turn_id, seq, sample_count, captured_at_ms, source}`；首次见新 `turn_id` 时按 `commit_mode` 开输入窗口 | `whole_utterance` 本地累积；背压发 `uplink.backpressure`，不丢帧；`turn_id` 未归一或为 0 → `invalid_state(turn_id)` | 按 `(turn_id, seq)` 去重 | 不阻塞 |
| `commit_turn(turn_id, mode)` | `commit` 宣告输入结束（**不**请求回复）/ `discard` 丢掉本轮已送音频 | 短于 `commit_min_ms` → `invalid_state(too_short)`；`server_only` → `ok` + `committed_by_server` | 重复 commit 返回首次结果 | 上游确认 2 s；级联 STT final 1.5 s，超时带 `final_by_timeout` |
| `request_response(turn_id, response_ref, overrides)` | 请求一次回复，返回 `response_id` | 不支持某 override → 整体 `unsupported(response.overrides.<字段>)`，不删字段照发、不写成历史条目；`single_slot` 忙 → `slot_busy` 不排队；服务端已自动发起 → 绑定它，`origin: server` | 按 `response_ref` | started 2 s，首个 delta 3 s |
| `cancel_response(response_id)` | 停止生成；保证之后该 id 的 delta 不再上转，最后发 `response.done{status: cancelled}` | 无取消接口 → 本地丢弃，`ok` + `local_only` | 已结束再取消返回 `ok`；取消竞态归 benign | 确认 1 s，超时发 `confirmed: false` |
| `truncate(item_id, audio_end_ms, played_text?)` | 告诉上游该条目用户只听到前 `audio_end_ms`（条目内位置 = 句偏移 + 句内 `played_ms`，不是单句 `played_ms`） | `audio_ms` 发截断（0 时删条目）；`text_rewrite` 缺 `played_text` → `invalid_state`；`local_only` 改本地；`none` → `unsupported(truncate.mode)` | 只接受单调变小的值；镜像在上游确认后才更新 | 确认 2 s，超时报 benign |
| `send_tool_result(call_id, result, reply_required)` | 回传结果让上游调用有终态；`false` = 静默收口 | 见下表 | 按 `call_id`；重复返回 `duplicate`；不在镜像里 → `invalid_state(unknown_call)` | 条目确认 5 s，超时报 benign |
| `update_config(patch, mode)` | `mode ∈ live\|append\|next_session` 会话中途改配置 | `next_session` 项用 `live` → `unsupported(config.update.<target>)`，**不得**自己重连；追加超长 → `invalid_state(too_long)` | 按 `state_block.version` 或 patch 哈希 | 确认 3 s |
| `close(reason)` | 立即关闭，不等回复说完；进行中的回复以 `cancelled` 收尾，发 `session.closed{reason, final_usage}` | — | 重复关闭返回 `ok` | 优雅关闭 2 s |

`send_tool_result` 降级表：

| 条件 | `reply_required=false` | `reply_required=true` |
|---|---|---|
| `auto_reply_after_result = no` | 只回传（`by_omission`） | 回传 + 发起回复 |
| `= yes` 且 `silent_close = flag` | 回传带静默标志 | 回传 |
| `= yes` 且无静默选项 | `unsupported(tools.silent_close)`，不发送；上层选本地收口 / 照发再取消自动回复 / 接受再生成 | 回传，绑定服务端自动回复 |
| `= unknown` | 按 `yes` 处理 | 同左 |
| 级联 / 半级联 | 追加到本地消息，不调 LLM | 追加后调一次 LLM |

### 2.4 标准化事件流

公共信封：

```yaml
event: { type, session_id, turn_id, response_id, response_ref, item_id,
         seq,            # 本会话单调递增
         t_upstream_ms, t_local_ms, raw_ref }
```

| 事件 | 关键字段 | 说明 |
|---|---|---|
| `input.speech_started` / `input.speech_stopped` | `source: server_vad` | 只作信息，是否打断由策略定 |
| `input.segmented` | `turn_id, segment_index` | 同一回合被服务端切段，供统计 `server_vad_segments_in_turn` |
| `input.committed` | `by: client\|server` | |
| `transcript.user.partial` / `.final` | `text, turn_id, final_by_timeout?, late?` | 迟到的 final 仍带原 `turn_id` |
| `transcript.assistant` | `response_id, item_id, text, is_final` | 上游对自己输出的转写，是"说了什么"不是"听到了什么" |
| `item.echo` | `local_id?, item_id, kind` | 上游确认收到注入条目，用于更新镜像 |
| `response.started` | `response_id, response_ref?, origin: client\|server_vad\|auto_tool` | 区分"我请求的"和"上游自己发起的" |
| `response.text.delta` / `response.audio.delta` | `response_id, item_id, (seq, audio, duration_ms)` | 每个 delta 必带 `response_id` 与 `item_id` |
| `response.sentence` | `response_id, item_id(=sentence_id), text, audio_ms` | 级联 / 半级联在句边界发 |
| `response.done` | `response_id, status: completed\|cancelled\|failed\|incomplete, reply_modality, confirmed?` | 每个 started 恰好一个 done |
| `tool.call` | `call_id, name, args(完整), response_id, turn_id, before_commit` | 参数完整后发一次；`before_commit=true` 供上层扣住 |
| `tool.call_cancelled` | `call_id, reason` | |
| `session.opened` / `session.resumable` / `session.expiring` / `session.closed` | `effective` / `handle` / `remaining_s, source: go_away\|max_secs` / `reason, by, final_usage` | |
| `uplink.backpressure` | `buffered_ms` | |
| `usage` | `response_id, input_text_tokens, input_audio_tokens, cached_input_tokens, cached_text_tokens, cached_audio_tokens, output_text_tokens, output_audio_tokens, generated_audio_ms, usage_semantics` | 每个 done 后一次；不上报缓存时字段为空，不填 0 |
| `error` | 见 2.5 | |

### 2.5 错误四分类

```yaml
error:
  class: fatal | transient | context_overflow | benign
  reason: auth | quota | billing | invalid_config | session_lost | timeout | throttled |
          cancel_race | empty_commit | duplicate | moderation | item_rejected | tool_parse | unknown
  scope: session | response | item | call
  ref: { event_id?, response_id?, item_id?, call_id? }
  raw: { code?, message? }
```

`code_map` 未覆盖时按顺序套默认规则：1）鉴权、授权、配额、计费、账号停用 → `fatal`；2）会话未建成就失败 → `fatal, invalid_config`；3）上下文超限 → `context_overflow`，不重试；4）断连、服务端结束、流超时、限流、模型未就绪 → `transient`；5）取消竞态、已提交导致的空 commit、重复条目 id、条目级错误、审核命中、工具结果解析错误 → `benign`；6）`response.done` 为 failed 且码不在致命表 → `benign, scope: response`；7）其余 → `transient, reason: unknown` 并记"未映射错误码"日志；8）码不稳定的上游按消息子串匹配。

## 3. 规则（不变量）

1. **只翻译不决策。** 有两种以上合理做法的都是策略。为什么：策略散落在各适配器里，同一个问题会各服务处理不一致（例如重连后"已回传"登记表只有部分服务重建）。
2. **做不到就返回 `unsupported(<字段>)`，不偷偷替代。** 为什么：把按回复指令偷偷改写成一条 user 条目会进入历史，影响后续每一轮。
3. **能力未知按保守值。** 为什么：概率性或未验证的"支持"会在线上偶发出错，比明确不支持更危险。
4. **链路无关层不出现厂商名。** 为什么：一旦按厂商分支，新增上游就要改策略代码，能力声明失去意义。
5. **本地是真相，上游是镜像；镜像只在收到上游确认后更新，不作为重灌来源。** 为什么：镜像不跟随截断，用它重放会把被打断的回复当成说完了。
6. **原语不重试、不重连、不定时回收会话。** 为什么：重建预算在会话重建器统一管，适配层再重试会绕过预算，毒回合会无限重建。
7. **cancel 之后该 `response_id` 零 delta 上转，仲裁器再按代际过滤一次。** 为什么：上游迟到的音频是双声和"打断后还在说"的主要来源，双保险。
8. **每个 `response.started` 恰好一个 `response.done`，每个原语超时都以事件报出。** 为什么：上层看门狗依赖它给回合终态，悬挂的回复会让回合卡死。
9. **`commit_turn` 不请求回复，提交和回复分开。** 为什么：这样"只切段不回复""扣住松键前的工具调用"都能组合出来。
10. **`tool.call` 只在参数完整后上报，级联上必须先于该回复的任何音频上报。** 为什么：工具层要能在出声前拦截写操作。
11. **未映射错误默认 `transient`。** 为什么：重建预算兜底不会无限循环；默认 `fatal` 会把可恢复的会话直接放弃。

## 4. 可调参数

| 参数 | 值 | 性质 |
|---|---|---|
| `open` 连接超时 / 条目确认 | 10 s / 5 s | 建议值（取自开源框架默认） |
| `open` 无确认事件时的就绪等待 | 约 500 ms（视上游） | 开源参考值 |
| `commit_turn` 确认 / 级联 STT final | 2 s / 1.5 s | 建议值 |
| `request_response` started / 首个 delta | 2 s / 3 s | 建议值 |
| `cancel_response` 确认 | 1 s | 建议值 |
| `truncate` / `update_config` 确认 | 2 s / 3 s | 建议值 |
| `send_tool_result` 条目确认 | 5 s | 建议值 |
| `close` 优雅关闭 | 2 s | 建议值 |
| 护栏钩子 `budget_ms` | 50 | 建议值 |
| 探针重复次数（有竞态的项） | ≥ 20 次 / 组 | 建议值 |
| 复测周期 | 每季度一次，或版本 / 变更说明 / 未映射错误连续出现时 | 建议值 |
| 重建预算（由重建器使用） | 每回合 3 次，指数退避首次 ≤ 0.5 s、封顶 10 s，稳定 10 s 清零 | 实践值 |

## 5. profile 与链路差异

- 无屏设备 profile：主链路固定 S2S + 本地直念；有屏 App profile：按回合类型和风险等级在 S2S / 级联 / 半级联间选路，选路读能力声明（`cascade.pre_speech_hook`、`output.text_lead_ms`、`output.modality_switch`），不读厂商名。

| 字段 / 原语 | S2S | 级联 | 半级联 |
|---|---|---|---|
| `turn.*` | 看探针 | 自控；本地判停 → STT final | 自控；`whole_utterance` |
| `open` | 建远端会话、重灌 | 构造本地请求上下文；状态块放**消息尾部**保前缀缓存 | 同级联；历史一律存文本，只本轮是音频 |
| `history.injection` / `config.update.*` | 看探针 | 每次请求自带，立即生效 | 同级联 |
| `tools.auto_reply_after_result` | 看探针 | `no` | `no` |
| `truncate.mode` | `audio_ms` / `none` 等 | `local_only`（按 TTS 播放位置） | `local_only`（只出文本时） |
| `cascade.pre_speech_hook` | `none` | `sentence` | `sentence`（只出文本时） |
| TTS | 上游出音频 | 适配器内置，和本地直念**同一声音配置** | 同级联 |

护栏钩子位置：完整句子产出之后、交给 TTS 之前，`input{response_id, turn_id, sentence_index, text, is_last}` → `allow | replace{text} | block{escalate}`。`block` 后适配器取消回复并发 `response.done{status: cancelled, reason: guardrail}`。

## 6. P0 探针流程

**准备**：冻结一套探针音频（句中插 200 / 300 / 400 / 500 / 700 ms 静音的合成语音；目标人群带句中停顿的语音；50 / 100 / 150 ms 短片段；外放回声下的人声和附和语；含数字和专有名词的念读文本）。探针 harness 直接调原语，同时记录原始上游事件和标准化事件，每次运行带模型、API 版本、日期。

**步骤**：1）读文档和开源代码静态填表（`evidence: doc / code_read`）；2）逐项探测，结果写回为 `probed` 并附 trace 编号；3）探测中出现的所有错误码进 `errors.code_map`，再回放历史错误日志重标；4）按判定标准定取值并选上层路线；5）能力声明与适配器代码一起评审、一起版本化；6）上游版本变化、厂商发变更说明、未映射错误连续出现时复测。

**判定标准**：行为类 20/20 → `yes`，0/20 → `no`，其间记 `flaky(k/20)` 并按保守值处理；阈值类取切段率首次超过 50% 的那一档；效果类要求回忆正确率不低于现状、纯文本回复轮数为 0、首轮不把历史当问题回答、不模仿 `[Called function …]` 格式。

**探测项**（P0-1、P0-2、P0-6、P0-7、P0-8、P0-9 覆盖第 7 节的关键字段，上线前必须 `probed`）：

| # | 测什么 | 怎么测 | 通过标准 | 不通过时的策略 |
|---|---|---|---|---|
| P0-1 | 回传工具结果后是否自动续答；有无静默选项 | 触发调用 → 回传结果 → 5 s 内不发任何请求，看是否出现 `response.started`；有静默参数的再试参数；≥ 20 次 | `auto_reply_after_result=no`（20/20 不开口）或 `silent_close=flag` 有效 | 静默收口不可用：`send_tool_result(false)` 返回 `unsupported`；结果由本地直念的路线改为不回传、本地收口，或照发后取消自动回复，或接受再生成 |
| P0-2 | 服务端判停能否关、能否只切段不回复、能否不自动打断 | 依次试关闭参数、最大静音、"切段不建回复"开关、"不自动打断"开关；送 2 s 语音 + 1 s 静音；播放中送噪声 / 附和语 | 关闭后 700 ms 静音不切段不回复；或只切段无回复事件；播放中噪声不取消回复 | 关不掉 → 调到最不敏感一档；不能只切段 → 上层自行门控音频、按 `tool.call.before_commit` 扣住松键前调用；不能关自动打断 → 接受"打断只认按键"只管到本地播放 |
| P0-3 | 判停实际静音阈值 | 静音梯度样本，统计各档切段率 | 得到阈值（切段率首次 > 50% 的档） | 无法稳定测出 → 停顿压缩不启用 |
| P0-4 | 空 commit、过短 commit、清空缓冲 | 分别提交 0 / 50 / 100 / 150 ms；提交前 clear | 得到 `commit_min_ms` 与空提交行为 | 短按过滤阈值设为 ≥ `commit_min_ms`；空提交错误码进 benign |
| P0-5 | 按回复覆盖指令 / 输出模态；追加通道 | 按回复传一条会话里没有的指令，看本轮是否遵循、下一轮是否仍遵循（进没进历史）、`cached_tokens` 变化；同会话一轮音频一轮文本 | 本轮遵循、下一轮不遵循、缓存不掉 | 状态块改走配置更新或重建；不得把指令退化成历史条目 |
| P0-6 | 中途配置更新是否重新预填；续接；时长上限 | 更新前后各一轮比较 `cached_tokens` 和首音；带 handle 断线重连后追问；长会话跑到被断 | 更新后缓存不掉；handle 重连能回忆 | 缓存掉 → 立即生效层改走按回复覆盖 / 追加 / 空档重建，懒生效层攒到重建；无续接 → 一律重灌 |
| P0-7 | 是否上报缓存命中 | 固定前缀连续两轮看 usage | 有 `cached_tokens` | 只能用首音差间接估算缓存效果 |
| P0-8 | 错误码清单与单响应槽位 | 构造超长历史、重复取消、并发回复、非法条目 | 每个码都能归入四类；并发请求行为明确 | 未映射码默认 `transient`；`single_slot=yes` 时上层串行化回复请求 |
| P0-9 | 中途追加上下文是否生效；各灌入方式回忆效果；灌文本后模态是否变文本 | 对 `history.injection` 每种方式灌入含名字、同伴、工具结果的历史，统计回忆正确率和后续 N 轮 `reply_modality`；中途插入一条事实后下一轮追问 | 效果类标准全部满足；中途插入后追问能答对 | 中途插入不生效 → 状态只走配置更新 / 按回复覆盖 / 重建；模态翻转 → 换灌入格式或把前情并入状态块，trace 告警 `text_only` |
| P0-10 | 上行快于实时、上行有空洞 | 3 s 音频 0.5 s 内送完；删掉句中 400 ms 静音后拼接送出；比较识别和切段 | 识别与切段不劣化 | 上层限速；停顿只能"压缩"不能"删帧" |
| P0-11 | 厂商特有的判停杠杆 | 如输入静音开关：按键期间 unmute、其余 mute，看松键前是否还切段 / 回复；外放条件下统计误打断率 | 松键前无切段无回复 | 不可用则回到 P0-2 的策略 |
| P0-12 | 在途工具占位 output 是否被接受 | 回传"仍在进行中"的假 output | 不报错、不引发自动回复 | 长耗时工具不发占位，改用本地进度播报 |
| P0-13 | say / 上游逐字念是否逐字 | 让上游念含数字、专名的文本 20 次，比对转写 | 20/20 逐字 | 直念走本地 TTS，接受音色差异 |
| P0-14 | truncate 是否支持、是否生效 | 截断后追问"你刚才最后一句说了什么" | 回答与截断位置一致 | `truncate.mode=none`：本地照记已播放文本，下次重灌时纠正 |
| P0-15 | 工具调用前是否已有输出（半级联必测） | 触发工具，记录 `tool.call` 之前的 text / audio delta | 调用前无音频 | 护栏钩子对该上游视为无效，高风险回合不走它 |
| P0-16 | 已回传调用被打断后，打断标记的补发通道 | 回传后在正文播放中打断，分两组补 `{"interrupted": true}`：新建条目 / 同一 `call_id` 再回传；各 ≥ 20 次，追问"刚才说到哪了" | 上游接受、不报错、不自动回复，模型知道被打断 | 两种都不行 → 不补标记，只在下次重灌时用带打断标记的已播放文本纠正 |

## 7. 验收清单

- [ ] 每个上游有能力声明，所有字段有值和证据等级；关键字段（`turn.server_detection_disable`、`turn.segment_only`、`history.injection`、`history.text_flips_modality`、`tools.auto_reply_after_result`、`tools.silent_close`、`config.update_invalidates_cache`、`usage.cache_reported`、`errors.code_map`、`response.single_slot`）为 `probed`。
- [ ] 链路无关层代码搜不到厂商名或模型名（配置和能力声明文件除外）。
- [ ] 适配器代码里没有重试循环、定时回收、回合计数、按内容分支。
- [ ] 把所有 `unsupported` 组合跑一遍，确认返回 `unsupported(<字段>)` 而不是悄悄换做法。
- [ ] 一套共用行为测试（mock 各厂商原生协议：上下文投递、工具续答、取消、重连、异步播报排队）对所有适配器跑通。
- [ ] 事件流不变式：delta 带 `response_id` 与 `item_id`；cancel 确认后零 delta；started / done 一一对应；每个 `tool.call` 最终有 `closed_by`；原语超时都有事件。
- [ ] 故障注入（识别中、工具执行中、已回传未出声、出声中断开，各 20 次）：回合都有终态，写操作重复执行 0，重连后重复调用 0。
- [ ] 历史错误日志回放后能统计未映射错误码占比；`fatal` 误判导致的放弃会话为 0。
- [ ] 级联 / 半级联：护栏钩子在每句 TTS 前被调用，`block` 后零音频；工具调用先于该回复任何音频上报。

## 8. 产出骨架时的注意事项

- 先产出能力声明 schema（字段名、取值枚举、保守值常量）和一份全 `unknown` 的模板，再写适配器；适配器构造时加载声明，原语里只按字段分支。
- 原语签名、`Ack`、事件信封、`error` 结构按本文字段名写死成类型；不要给原语加 `retry` / `auto_reconnect` 一类参数。
- 每个原语把"按能力降级"表实现成显式分支，末尾兜底返回 `unsupported`，不要写"尽量做"的分支。
- 镜像登记（条目、上游 id、已回传 `call_id`）单独成类，只在上游确认事件里更新；`open` 时重建。
- 级联适配器内部的 TTS 引用共享 TTS 配置，不要另起声音配置。
- 同时产出 P0 探针 harness 的骨架：每个探测项一个函数，输出"字段名、取值、证据、trace 编号"，可直接写回能力声明。
- 事件字段名对齐 trace 字段（见 trace-evaluation 参考文件），例如 `reply_modality`、`cached_input_tokens`，不要另起名字。
