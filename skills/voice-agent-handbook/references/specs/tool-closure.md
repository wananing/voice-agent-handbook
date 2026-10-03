# 设计模板：工具注册表与调用收口

## 目录

1. 解决什么问题
2. 核心模型（2.1 注册表字段 / 2.2 注册表示例 / 2.3 结果公共字段 / 2.4 路线 A0–A3 与决策表 / 2.5 调用生命周期 / 2.6 六个终态 / 2.7 打断矩阵 / 2.8 委派协议 / 2.9 回传 output / 2.10 决策分层与门槛链）
3. 规则
4. 可调参数
5. profile 差异
6. 验收清单
7. 产出骨架时的注意事项

## 1. 解决什么问题

工具回合慢，慢在"回传结果后让模型再生成一轮"（S2S 上约 1.2 s），不在工具执行本身。省掉这一段的办法是把**收口**（调用在上游和本地都有终态）和**再生成**（回传后请模型再说）分开决定：按工具的呈现方式、执行时长、是否写操作选出声路线。同时每个调用都必须有终态，悬着的调用会让模型在后续几轮反复调同一个工具（实测闲聊约 21%，打断后更高）。执行权在工具层：模型只提议，写操作的门槛、阶段可见性、取消与去重由工具层执行。出声排队由仲裁器负责，见 output-arbiter.md。

## 2. 核心模型

### 2.1 注册表字段

注册表在会话开始时整体下发给模型；按阶段、按状态的限制在工具层执行，不改上游工具表（S2S 改工具表要重连）。

| 字段 | 类型 / 取值 | 必填 | 说明 |
|---|---|---|---|
| `name` | snake_case 字符串 | 是 | 全局唯一 |
| `description` | 字符串 | 是 | 给模型的用途说明，不写事实 |
| `params` | JSON Schema | 是 | 实体类参数标 `entity: true`，由工具层解析，与用户原话冲突时以原话为准 |
| `presentation` | `verbatim` / `model` / `screen` / `silent` | 是 | 直念模板 / 模型组织语言 / 上屏 + 语音要点 / 只记不出声 |
| `voice_template` | 模板字符串或 id | `verbatim`、`screen` 必填 | 无屏设备一次一个问题、最多两个选项 |
| `latency_class` | `ms` / `seconds` / `background` | 是 | `ms`：P90 ≤ 200 ms；`seconds`：P90 ≤ 15 s；`background`：可能 > 15 s 或多步 |
| `cue` | `long` / `short` / `none` | 否 | 默认 A1 为 `short`（≤ 300 ms），A2 / A3 为 `long`（整句约 0.7–0.8 s）；工具在 cue 授权前已完成则撤回 cue |
| `write` | 布尔 | 是 | 有副作用；`true` 时 `gates`、`idempotency_key` 必填 |
| `idempotency_key` | 参数名列表 | `write=true` 必填 | `duplicate` 判定和后端去重 |
| `gates` | 有序列表 | `write=true` 必填 | `intent` → `utterance` → `backend_state`（2.10） |
| `phases` | `global` 或阶段名列表 | 是 | 哪些阶段对模型可见 |
| `visible_after` | 工具名列表 | 否 | 前置调用成功后才可见 |
| `interrupt_policy` | `cancel` / `continue` / `finish_then_notify` | 是 | `write=true` 只能取后两者 |
| `cancellable_by_user` | 布尔 | 否 | `background` 默认 `true`，用户可说"算了"取消 |
| `on_duplicate` | `allow` / `reject` / `replace` / `confirm` | 是 | `write=true` 默认 `reject` |
| `duplicate_scope` | `turn` / `in_flight` / `session` | 否 | 默认 `turn` |
| `timeout_ms` | 整数 | 是 | 超时 `closed_by=cancelled, reason=timeout` 并本地直念兜底 |
| `progress` | 对象 | `seconds` / `background` 必填 | `ack_template`（进度收口的承接句）、`throttle` |
| `result_schema` | JSON Schema | 是 | 必须含 2.3 的公共字段 |
| `profile_overrides` | `{<profile>: {...}}` | 否 | 只能覆盖 `presentation`、`voice_template`、`phases`、`timeout_ms`、`progress`，不能覆盖 `write`、`gates` |

注册时拒绝：`write=true` 无 `gates`；`screen` 无 `voice_template`；`write=true` 且 `interrupt_policy=cancel`；`latency_class≠ms` 无 `progress`；无屏 profile 出现 `screen`；`silent` 且 `write=true`。

### 2.2 注册表示例

```yaml
registry_version: "2026-09-29.1"
tools:
  - name: get_status                 # 只读、毫秒级、直念 → A1
    description: 查询当前任务的进度和剩余项。
    params: { type: object, properties: {}, additionalProperties: false }
    presentation: verbatim
    voice_template: status.v3        # "已经完成 {done} 个，还剩 {left} 个。"
    latency_class: ms
    write: false
    phases: global
    interrupt_policy: cancel
    on_duplicate: replace
    timeout_ms: 1000
    result_schema: { $ref: "#/common/result" }
  - name: submit_request             # 写操作、秒级 → A3 + 门槛
    description: 提交用户已确认的请求。
    params: { type: object, properties: { option_id: { type: string, entity: true } }, required: [option_id] }
    presentation: verbatim
    voice_template: submit.done.v2
    latency_class: seconds
    write: true
    idempotency_key: [option_id]
    gates:
      - { kind: intent, allowed_actions: [confirm_submit] }
      - { kind: utterance, must_match: [提交, 确认], must_not_match: [怎么提交, 提交什么] }
      - { kind: backend_state, check: no_active_request_and_option_selected }
    phases: [confirm]
    visible_after: [show_options]
    interrupt_policy: finish_then_notify
    on_duplicate: reject
    duplicate_scope: turn
    timeout_ms: 15000
    progress:
      ack_template: submit.ack.v1    # "好，已经提交，正在处理。"
      throttle: { first_after_s: 60, min_interval_s: 60, replace_older: true }
    result_schema: { $ref: "#/common/result" }
```

### 2.3 结果公共字段

```json
{ "ok": false, "reason_code": "not_supported",
  "summary": "给模型看的一句话摘要，不含 ID、URL",
  "data": {}, "display": { "card_id": "c-17" },
  "alternatives": [ { "label": "换成方案二", "action": "select_option", "params": { "option_id": "o2" } } ],
  "escalation": { "label": "转人工", "action": "transfer_to_human" } }
```

`ok=false` 时 `reason_code` 必填；`alternatives` 最多两项，由工具从数据算出；`escalation` 在有屏 profile 的高风险阶段必填；`display` 只在 `screen` 时有。

### 2.4 路线 A0–A3 与决策表

| 路线 | 做法 | 再生成 |
|---|---|---|
| **A1 收口直念** | 执行 → 按 `voice_template` 渲染 → 本地 TTS / 预合成，排在短 cue 之后播（speech，受代际约束）→ 同时回传 output | 否 |
| **A2 委派后台流式** | 前台只调委派工具 → 后台文本 LLM 带只读工具流式出正文 → 首句交本地 TTS → 回传实际播出文本；识别定稿即投机起跑 | 否 |
| **A3 进度即收口** | 工具一启动就回传进度并收口（`closed_by=progress`），说一句承接；最终结果作 narration 等插话窗口 | 承接句本地直念时否；由模型生成时是，只一轮 |
| **A0 回传再生成** | 回传结果并请求模型再生成；兜底路线 | 是 |

| # | presentation | latency_class | write | 路线 |
|---|---|---|---|---|
| 1 | verbatim | ms | 否 | A1 |
| 2 | verbatim | ms | 是 | A1 + 门槛，执行前再查代际 |
| 3 | verbatim | seconds | 否 | A3 |
| 4 | verbatim | seconds | 是 | A3 + 门槛 |
| 5 | 任意（silent 除外） | background | 任意 | A3，进度按 `throttle` 节流 |
| 6 | model | ms / seconds | 否 | A2 |
| 7 | model | 任意 | 是 | 拆分：写操作按 #2 / #4 执行收口，讲解另起 A2；写操作不委派 |
| 8 | screen | ms | 否 | A1-屏：上屏 + 直念 `voice_template` |
| 9 | screen | seconds | 任意 | A3-屏：先显示"正在查 + 已知信息"，承接句直念，结果上屏后念要点 |
| 10 | silent | 任意 | 否 | 静默收口：回传，不再生成、不出声 |
| 11 | silent | 任意 | 是 | 禁止 |

路线在注册时静态算出、进 trace（`tool_route`）。运行时只有两种情况改路线：上游能力声明不支持静默回传（`tools.silent_close` 为 `none` / `unknown`，或收到 output 后会自动开口且无静默选项）时 A1 / A2 退到 A0 或改走半级联，写 `route_fallback_reason`；有屏 profile 的高风险回合禁用 A0，`model` 正文必须文本先于语音、进 TTS 前过护栏。上游是否支持静默回传需按上游实测，结果写进能力声明，执行器按能力声明分支，不按厂商名写分支。

### 2.5 调用生命周期

| 状态 | 事件 | 转移 | 动作 / 登记 |
|---|---|---|---|
| `received` | 收到 function call 或委派请求 | — | 登记 `call_id, tool, turn_id, generation, route, received_at`；看门狗开始计 `timeout_ms` |
| `received` | 阶段可见性、`on_duplicate`、门槛链全通过 | → `running` | `started_at` |
| `received` | 阶段不可见 / 门槛拒绝 | → `closed{result}` | `ok=false`，本地直念拒绝或引导话术，不再生成 |
| `received` | 重复命中 | → `closed{duplicate}` | |
| `running` | 首条进度 | → `closed{progress}` | 之后的进度和最终结果改作 narration 和上下文条目，不再挂原 `call_id` |
| `running` | 完成 | → `closed{result}` | 回传前查代际 |
| `running` | 超时 / 取消 / 打断 | → `closed{cancelled / interrupted}` | 见 2.7 |
| 任一 | 会话重建 | → `closed{superseded}` | 本地收口，不向新会话回传 |
| `closed` | — | — | 置 `settled=true`，记 `closed_by, closed_at, reply_required, output_sent` |

`settled` 置位后拒绝一切迟到的结果、进度、取消，记 `late_result_dropped`。

### 2.6 六个终态（`closed_by`，不得增删）

| `closed_by` | 触发 | 回传 output | 再生成 |
|---|---|---|---|
| `result` | 正常完成（含 `ok=false`、门槛拒绝、阶段不可见），回传时代际仍有效 | 2.9 结果体 | 仅 A0 |
| `interrupted` | 打断时调用已完成 | 已回传：补 `{"interrupted": true}`；未回传：见 2.7 行 3 | 否 |
| `cancelled` | 在跑时被取消：打断且 `cancel`、用户"算了"、超时、委派取消 | `{"cancelled": true, "reason": "interrupt" / "user" / "timeout"}` | 否，超时本地直念兜底 |
| `progress` | `seconds` / `background` 工具的首条进度 | `{"status": "in_progress", "summary": "已提交，正在处理", "spoken_to_user": "<承接句>"}` + "任务仍在进行，不要编造结果、不要再次调用" | 承接句本地直念时否 |
| `superseded` | 会话重建；被 `on_duplicate=replace` 的新调用替换。不用于打断 | 重建：不回传；替换：`{"superseded": true}` | 否 |
| `duplicate` | `reject` / `confirm` 命中；同一用户回合第二个写操作；同一回合第二个委派 | `reject`：`{"duplicate": true, "message": "同一请求已在处理或已完成，不要再次调用"}`；`confirm`：加 `"needs_confirmation": true` | `reject` 否；`confirm` 是或本地直念确认问题 |

在途占位（下一轮推理前向上下文注入"仍在进行中"的假 output、推理后剥掉）只在级联路径启用；S2S 上是否被接受需按上游实测，通过前靠 A3 的进度即收口达到同样效果。

### 2.7 打断矩阵（打断时仲裁器代际已 +1）

| # | 阶段 `interrupt_phase` | 只读工具 | 写操作 | `closed_by` |
|---|---|---|---|---|
| 1 | `received`：门槛检查中、未执行 | 放弃执行 | 放弃执行，不写后端 | `cancelled` |
| 2 | `running`：同步执行中 | `cancel` 则取消，不等 | 已发后端请求不撤回，等完成；`finish_then_notify` 下次空闲补一句 | 只读 `cancelled`；写操作完成后 `interrupted` |
| 3 | `done_not_sent`：已完成未回传 | 上游支持静默收口：回传真实结果 + `"not_spoken": true`，`reply_required=false`；否则不回传，本地置 `interrupted` | 同左，另补一句 | `interrupted` |
| 4 | `sent_speaking`：已回传，模型在生成或本地在念 | 取消生成 / 停播；S2S 按播放位置 truncate；补 `{"interrupted": true}` | 同左，结果已在 output 里，不需补一句 | `interrupted` |
| 5 | `progress_closed`：已进度收口，后台在跑 | `continue`：继续，完成后作 narration | 同左 | 保持 `progress` |
| 6 | `narration`：正在播最终结果 | 视为已送达，不重播 | 同左 | — |
| 7 | `delegating`：委派进行中 | 取消后台任务，丢弃未播出文本，output 只回已播部分 | 不适用 | 未播出：`cancelled`；已播部分：`interrupted` |

误打断（附和、咳嗽）由回合层过滤，不进本矩阵。

### 2.8 委派协议

前台 → 后台请求字段：`delegation_id`、`call_id`、`turn_id`、`generation`；`intent_hint`；`last_utterance_gist`（前台复述的最后一句要点，对冲转写竞态）；`transcript`（自有 ASR 定稿，有则优先）；`state_block`（带版本号）；`recent_turns`（最近 K 轮，assistant 部分用用户实际听到的文本）；`allowed_tools`（只读工具子集）；`constraints`（一两句、先结论、禁 markdown 与列表、数字逐位念；有屏另给可上屏结构化字段）；`deadline_ms`（后台首句截止）。

后台 → 前台三个通道：

| 通道 | 内容 | 去向 |
|---|---|---|
| `speak` | 流式正文，按句切出 | 仲裁器 speech → 本地 TTS；带 `generation`，过期即丢 |
| `context` | 中间事实、工具细节、结构化字段 | 本地上下文和状态块，不出声 |
| `terminal` | `done` / `failed` / `timeout` / `empty` | 工具层据此收口前台调用 |

投机起跑：ASR 定稿（约 0.37 s）即用"转写 + 状态块"启动后台；产出先扣住，直到前台真的发出委派调用且 `turn_id` 一致才放行；前台直答或回合结束仍未委派则按代际作废、丢弃产出、不进上下文；投机只能调 `write=false` 工具，一旦需要写操作立即撤销；命中率进 trace，低于门槛关闭投机。同一委派跨"承接句 → 正文"保持同一条流式 TTS 连接。委派不跨会话重建存活，重建时收口为 `superseded`，已播文本进新会话历史。

### 2.9 回传 output

| 字段 | 说明 |
|---|---|
| `status` | `ok` / `failed` / `in_progress` / `interrupted` / `cancelled` / `superseded` / `duplicate` |
| `summary` | 一句话摘要，不含 ID、URL、路径 |
| `spoken_to_user` | 实际播出的原文，按播放回执换算的位置截断；未播出为空串；立即回传时暂填计划播出文本 |
| `shown_to_user` | 有屏：上屏内容摘要或卡片 id |
| `alternatives` / `escalation` | 失败时随结果下发 |
| `note` | 给模型的约束，如"已经告诉用户了，不要重复" |
| `delivery` | `in_progress`：立即回传时正文还在念；其余省略。与 `status=in_progress`（进度收口）不是一回事 |

回传时机是**立即回传**，不等前面的音频播完；被打断时按 2.7 行 4 补 `{"interrupted": true}`。是否附 `played_text`、补发走新条目还是同一 `call_id` 再回传，需按上游实测。

不发再生成的条件（满足任一）：路线是 A1、A2、静默收口或本地直念承接的 A3；`closed_by` 是 `interrupted` / `cancelled` / `superseded` / `duplicate(reject)`；调用代际已过期；用户正在说或回合未结束；门槛拒绝或阶段不可见。只有 A0、`duplicate(confirm)`、A3 的模型承接句会再生成，每个调用最多一轮。

业务失败的话术固定为"不能 + 最多两个可行选项 + 升级出口"，替代项来自数据，模型不得自行补充。

### 2.10 决策分层与门槛链

顺序：① 规则短路（命中直接执行，不进模型）→ ② 阶段过滤（可见动作集 = 阶段动作 ∪ 全局动作 ∪ `visible_after` 已满足的动作）→ ③ 模型提议（`ToolSelector` 可替换：规则 / 小文本 LLM / S2S 原生 FC）→ ④ 修正层（按状态纠正提议）→ ⑤ 实体解析（原话优先）→ ⑥ 门槛链（仅写操作）→ 执行 → 按 2.4 分流。S2S 上不改上游工具表，② 在 ③ 的输出校验上生效。

| 顺序 | `kind` | 检查 | 失败 `reason_code` |
|---|---|---|---|
| 1 | `intent` | 最终动作属于 `allowed_actions` | `intent_mismatch` |
| 2 | `utterance` | 用户原话命中 `must_match`、不命中 `must_not_match` | `utterance_not_explicit`；念一句确认问题，有屏同时给确认按钮 |
| 3 | `backend_state` | 重新读后端：前置状态成立、无进行中的同类操作 | `backend_state`，带 `alternatives` |

门槛拒绝以 `ok=false` 收口。阶段外调用：不执行，`closed_by=result`、`reason_code=not_available_in_phase`，本地直念引导话术。全局动作（闲聊、追问、纠正、重复、取消、停止播报；有屏另加转人工、紧急出口、挂断）在任何阶段保留。有屏点选"确认"视为同时满足第 1、2 道，第 3 道仍要查。

## 3. 规则

1. 每个 `received` 的调用在 `timeout_ms` + 看门狗余量内必然进入 `closed`，因为悬着的调用会导致模型反复调同一工具。
2. 收口和再生成分开决定，因为省掉再生成是工具回合提速的主要来源，而收口是防重复调用的必要条件。
3. `settled` 置位后拒绝一切迟到结果、进度、取消，因为迟到结果被播出或回传会和已发生的对话矛盾。
4. 结果、音频、流式文本到达时先查代际，过期即丢，因为打断后旧回合的结果不该出声。
5. 写操作在门槛通过到真正调后端之间再查一次代际和门槛，变化则放弃、记 `cancelled`，因为不能依赖上游的"禁止打断"开关。
6. 同一用户回合只认第一个写操作调用和第一个委派，后续回 `duplicate`，因为模型会对同一意图重复发起。
7. 写操作执行完才被打断的，下次空闲用 `voice_template` 补一句告知（narration，被打断不重播），因为副作用已发生，用户必须知道。
8. 后端按 `idempotency_key` 去重，作最后一道保险但不替代规则 6，因为后端去重只防副作用，不防重复播报和重复收口。
9. 写操作不委派给后台模型执行，投机任务不得有副作用，因为执行权必须留在有门槛的工具层。
10. 回传的 `spoken_to_user` 是用户实际听到的文本，因为模型要据此理解后续指代（"第二个"）。
11. 打断后补 `{"interrupted": true}`，因为不补时模型会以为已经说完，补后出声率实测 20/20 对 15/20。
12. 会话重建后在途调用本地收口为 `superseded`、不向新会话回传，因为旧上游会话已放弃。
13. 委派失败、超时、无输出时前台一定回一句本地兜底话术，不交给模型再生成，因为再生成还要付约 1.2 s，而沉默即故障。
14. 替代方案由工具从数据算出、随结果下发，因为模型临场编的选项可能不可执行。
15. `superseded` 不用于打断，打断一律按矩阵记 `interrupted` / `cancelled`，因为终态语义要能直接从 trace 里区分原因。

## 4. 可调参数

| 参数 | 值 | 性质 |
|---|---|---|
| `latency_class` 阈值 | `ms` P90 ≤ 200 ms；`seconds` P90 ≤ 15 s | 建议值 |
| `timeout_ms` 初值 | `ms` 1000；`seconds` 15000；`background` 最终结果 600000（设备侧工具可用 30 s） | 建议值 |
| 看门狗余量 | 2 s | 建议值 |
| `cue: short` 时长 | ≤ 300 ms | 建议值 |
| `cue: long` 时长 | 约 0.7–0.8 s 整句 | 实践值 |
| 进度节流 | 首条 ≥ 60 s、全局间隔 ≥ 60 s、新覆盖旧 | 建议值（开源默认） |
| 委派 `recent_turns` K | 4 | 建议值 |
| output 长度上限 | 500 字符 | 建议值 |
| 替代项 | 最多 2 个 | 规则 |
| 基线耗时 | ASR 定稿约 0.37 s；function call 约 0.8 s；状态类工具 1–50 ms；再生成约 1.2 s；A0 有内容首音约 2.1 s | 实践值（S2S 参考基线） |
| A1 目标 | 有内容首音 P50 ≤ 1.1 s | 建议值 |
| A2 目标 | P50 ≤ 1.3 s、P90 ≤ 2.0 s；投机命中 ≥ 70% | 建议值 |
| 有屏工具回合 | ≤ 2 s 有内容（屏幕或语音） | 建议值 |

## 5. profile 差异

| 维度 | 按键硬件 / 开放麦（无屏） | 浏览器 / App（有屏） |
|---|---|---|
| 呈现方式 | `verbatim` 多；`screen` 注册时拒绝，需 `profile_overrides` 给直念版本 | `screen` 多；`verbatim` 用于要点和承接；`model` 正文文本先于语音 |
| 主要路线 | A1（状态类）、A2（讲解类）、A3（写操作与设备任务） | A1-屏 / A3-屏为主，A2 讲解；高风险回合禁用 A0 |
| 结果体 | `voice_template` 必填，选项最多两个 | `data` + `display`，产出物字段会话结束时汇总 |
| 阶段化 | 弱，只用于少数子流程 | 强，`phases` 普遍使用 |
| 特有工具 | 设备事件工具（定时、传感器、设备控制）；事件先按 handle / context / respond / interrupt 定性再交仲裁器 | 转人工工具：`global`，由规则执行，带会话摘要 |
| 等待期出声 | 约 2 s 无声必须有 cue 填充 | 屏幕状态 + 一句语音填充 |
| 进度 | 语音节流 | 屏幕可实时，语音仍节流 |
| 替代方案 | "不能 + 两个选项"，升级出口可省 | "不能 + 两个选项 + 升级出口"，选项上屏可点、点选等同说出 |
| 护栏 / 确认 | 只查事实边界；语音复述确认 | `speak` 通道逐句过护栏；语音复述 + 确认按钮 |

按键与开放麦对工具层没有差异，差别只在打断来源（见 turn-protocol.md）。注册表字段、决策表、状态机、终态、打断矩阵、委派协议、回传格式、门槛链两类 profile 共享。

## 6. 验收清单

有竞态的项（打断、异步、委派）用重复运行出通过率，单次通过不算。

- [ ] 悬挂调用（超过 `timeout_ms` + 余量仍无 `closed_by`）= 0。
- [ ] 后续 3–5 轮重复调用率 ≤ 2%（远低于 21% 基线）。
- [ ] 写操作重复副作用 = 0；阶段外工具被执行 = 0。
- [ ] 打断收口每格 ≥ 20 次：出声 20/20、卡死 0、重复调用 0、误称已告知 0。
- [ ] 迟到结果被播出或被回传 = 0，`late_result_dropped` 可观测。
- [ ] 双声 = 0。
- [ ] A1 有内容首音 P50 ≤ 1.1 s；A2 P50 ≤ 1.3 s、P90 ≤ 2.0 s，委派召回 ≥ 81%，闲聊误委派 ≤ 5%，投机命中 ≥ 70%。
- [ ] A3 首音下降，重复播报 < 5%；有屏工具回合 ≤ 2 s 有内容。
- [ ] 委派失败 / 超时 / 无输出后无声 = 0。
- [ ] 不支持的请求同时判诚实（没做成不说做成）与恢复（给了具体替代）。
- [ ] 每个调用 trace 都有 `call_id, name, tool_route, closed_by, reply_required, generation, gate_result`：100%。

## 7. 产出骨架时的注意事项

- 注册表加载时跑 2.1 的校验规则并在启动期失败，不要等运行时才发现写操作没门槛。
- 路线由 `(presentation, latency_class, write)` 查 2.4 决策表静态算出，再按能力声明降级；不要在执行器里按工具名写 if。
- 调用对象上要有 `settled` 位和 `generation`；所有回调入口（结果、进度、取消、超时）第一行先查 `settled`、再查代际。
- 每个调用都起一个看门狗定时器，到期统一走 `cancelled{timeout}` + 本地兜底话术。
- A3 的首条进度即收口：后续进度和最终结果不能再挂原 `call_id`，要以新的 narration（`task_progress` / `task_result`）入仲裁器。
- 打断处理按 2.7 的七个阶段写成显式分支，`interrupt_phase` 写进 trace；行 3 要按能力声明 `tools.silent_close` 分两路。
- 写操作执行前的"再查一次代际和门槛"放在真正调用后端的那一行之前，而不是门槛链结束处。
- 委派的 `speak` 通道每句带代际进仲裁器；投机产出用闸门扣住，闸门同时比对 `turn_id`。
- 回传 output 走能力原语 `send_tool_result(reply_required=false)`；只有 A0、`duplicate(confirm)`、模型承接句传 `true`。
- cue 由工具层在收到 function call 时入队；结果在 cue 授权前已就绪时，用定向冲刷撤回 cue，不要让仲裁器改规则。
- 阶段外调用和门槛拒绝都要回传 `ok=false` 收口，不能静默丢弃。
