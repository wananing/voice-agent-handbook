# pipecat-flows

> 仓库：https://github.com/pipecat-ai/pipecat-flows
> 许可：BSD-2-Clause（见仓库 LICENSE 文件）
> 分析基于：commit 96223f4（`pipecat-ai-flows` 1.4.0，2026-07-05）
> 状态：草稿
> 最后更新：2026-10-01

下文路径默认相对 `src/pipecat_flows/`，示例相对 `examples/`。标"推断"的是读代码推出来、没有跑过的结论。

**版本说明**：1.4.0 是这个独立包的最后一版。README 写明从 `pipecat-ai` 1.5.0 起 Flows 并入 pipecat 本体的 `pipecat.flows`，独立包冻结，导入时发 `DeprecationWarning`；`pyproject.toml` 把依赖锁在 `pipecat-ai>=1.4.0,<1.5.0`。pipecat 422ad13 里的 `src/pipecat/flows/` 比本仓库多了 `flow.py`、`config.py`（YAML / JSON 声明式流程）、`{{ key }}` 状态占位符和 `NO_RESPONSE` 等，本页只分析独立仓库，差异处单独标出。

## 定位

Pipecat Flows 是叠在 pipecat 上的结构化对话层。它把一段对话写成"节点图"：每个节点有自己的任务说明和可用函数，函数的返回值决定下一个节点。面向的是流程固定、需要按步骤收信息的场景，例如订餐、预约、病人问诊、保险报价、转人工（见 `examples/`）。

和同类做法的区别：

- **它不自己管音频和回合**。判停、打断、TTS、工具执行全部用 pipecat 的，Flows 只在节点切换时往 pipeline 里塞 `LLMMessagesAppendFrame` / `LLMMessagesUpdateFrame`、`LLMSetToolsFrame`、`LLMUpdateSettingsFrame`、`LLMRunFrame`。
- **控制手段是"这一步模型能看到哪些函数"**，不是让模型背流程图。每次切节点整体替换工具集。
- **分支由函数代码决定**，不由模型决定：handler 拿到后端结果后选下一个节点。
- 对照 [openai-realtime-agents](openai-realtime-agents.md) 的 handoff：那边每个 agent 是一套 prompt 加工具，转交靠模型调 `transfer_to_<name>`；Flows 的节点切换写在 handler 里，转不转、转到哪由代码判断。

## 整体架构

```
FlowManager（持有 llm、context_aggregator、worker）
  │ initialize(initial_node) / set_node_from_config(node)
  ▼
_set_node
  1. pre_actions（tts_say / end_conversation / function / 自定义）
  2. 组装函数：global_functions + node.functions → FunctionSchema(handler=transition_func)
  3. _update_llm_context：
       role_message → LLMUpdateSettingsFrame(system_instruction)
       task_messages → APPEND: LLMMessagesAppendFrame / RESET: LLMMessagesUpdateFrame
       functions    → LLMSetToolsFrame（整体替换）
  4. respond_immediately → LLMRunFrame
  5. post_actions（respond_immediately=False 时推迟到 bot 下一次说完）
  ▼
pipecat pipeline：user_agg → LLM → TTS → output → assistant_agg
  │ LLM 调用函数 → LLMService.run_function_calls → transition_func
  ▼
transition_func：调用 handler，得到 (result, next_node)
  ├─ next_node 为空（节点函数）：result_callback(result, run_llm=True) → 正常再推理
  └─ next_node 非空（边函数）：  result_callback(result, run_llm=False,
                                   on_context_updated=_check_and_execute_transition)
                                 → 本轮所有调用结束后 _set_node(next_node)
```

**核心抽象**

| 抽象 | 位置 | 作用 |
|---|---|---|
| `FlowManager` | `manager.py:FlowManager` | 状态机本体：当前节点、`state` 字典、已注册函数、待执行转移 |
| `NodeConfig` | `types.py:NodeConfig` | 节点：`task_messages`（必填）、`role_message`、`functions`、`pre_actions` / `post_actions`、`context_strategy`、`respond_immediately` |
| `FlowsFunctionSchema` | `types.py:FlowsFunctionSchema` | 带 handler 的函数定义，含 `cancel_on_interruption`（默认 False）、`timeout_secs` |
| 直接函数 | `types.py:FlowsDirectFunctionWrapper` | 第一个参数是 `flow_manager` 的 async 函数，schema 从签名和 docstring 提取，必须返回 `(result, next_node)` |
| `ConsolidatedFunctionResult` | `types.py` | `tuple[Any, NodeConfig \| None]`，"结果 + 下一节点"合并返回 |
| `ContextStrategy` | `types.py:ContextStrategy` | `APPEND`（默认）/ `RESET` / `RESET_WITH_SUMMARY`（已废弃） |
| `ActionManager` | `actions.py:ActionManager` | 执行 pre / post action，内置 `tts_say`、`end_conversation`、`function` |
| `LLMAdapter` | `adapters.py:LLMAdapter` | 只剩摘要相关：`generate_summary` 调 `llm.run_inference` |

**状态**：`flow_manager.state` 是跨节点的普通 dict，不进模型；handler 自己读写。`current_node` 记录当前节点名。

**节点是动态构造的**：示例里节点都是函数返回的 `NodeConfig`，可以把上一步的数据拼进下一个节点的 prompt。例如 `examples/restaurant_reservation.py:check_availability` 查到不可用时，带着 `alternative_times` 调 `create_no_availability_node(alternative_times)`，新节点的任务说明里直接写"建议这些替代时间"。

### 一个带节点切换的回合

以订餐厅为例，用户说"周五晚上七点，四个人"：

1. user 聚合器判停，推 `LLMContextFrame`，LLM 第一次推理，产出 `check_availability(party_size=4, time=...)` 调用。这次推理一般没有正文。
2. pipecat 的 `LLMService.run_function_calls` 开 task 执行 `transition_func`，它调用户写的 handler。handler 查后端，返回 `(TimeResult, next_node)`。
3. `transition_func` 发现 `next_node` 非空，把转移信息存进 `_pending_transition`，用 `run_llm=False` 回传结果。结果照常写进 context 的 tool 消息，但不触发推理。
4. 结果写入 context 后，pipecat 在独立 task 里执行 `on_context_updated`，即 `_check_and_execute_transition`。如果同批还有调用没结束，什么也不做，等最后一个调用的回调再检查。
5. 全部结束后 `_set_node(next_node)`：执行 pre_actions，推 `LLMUpdateSettingsFrame`（如果新节点设了 `role_message`）、`LLMMessagesAppendFrame`（新任务说明）、`LLMSetToolsFrame`（新工具集），最后 `LLMRunFrame`。
6. LLM 第二次推理，在新节点的指令和工具下说出"有位 / 没位，可以改成……"。

节点函数（返回 `(result, None)`）则在第 3 步直接 `run_llm=True`，和普通 pipecat 工具回合一样只多一次推理，不切节点。

### 动作（actions）

| 类型 | 何时生效 | 说明 |
|---|---|---|
| `tts_say` | `TTSSpeakFrame` 走到 TTS 时 | 推理之前先说一句固定的话；`append_text_to_context` 控制是否进 context（1.3.0 加入，默认 True） |
| `end_conversation` | `EndFrame` 走到 pipeline 末端 | 可带 `text`，先说再结束；之后的动作不再执行 |
| `function` | `FunctionActionFrame` 到达 pipeline 末端时 | 在 pipeline 里排队执行，所以会排在之前的 `tts_say` 之后 |
| 自定义 | 立即 | `FlowManager.register_action` 或在节点里写 `handler`；执行前会等之前的动作完成 |

动作的顺序靠 `ActionManager` 维护一个"进行中动作计数"，并监听 `ActionFinishedFrame` / `FunctionActionFrame` / `BotStoppedSpeakingFrame` 到达下游（`actions.py:ActionManager.__init__` 里注册的 `on_frame_reached_downstream`）。`examples/warm_transfer.py` 用自定义动作做"静音客户、播等待音乐、拉人工坐席进房间"，是 Flows 管通话流程而不只是管对话的例子。

### 与 pipecat 本体内 `pipecat.flows` 的差异

| 项 | 独立包 96223f4 | pipecat 422ad13 `src/pipecat/flows/` |
|---|---|---|
| 流程定义 | 只能用 Python 构造 `NodeConfig` | 另有 `FlowConfig`（YAML / JSON / dict）+ `Flow` 绑定 handler，工具可返回 `TRANSITION_IN_YAML` 让配置决定下一节点 |
| prompt 中引用状态 | 无 | `{{ key }}` 占位符，进节点时用 `state` 填充 |
| "不回复"结果 | 无 | `NO_RESPONSE` |
| 依赖 | `pipecat-ai>=1.4.0,<1.5.0` | 跟随 pipecat 版本 |

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 初始化、进入首节点 | `manager.py:FlowManager.initialize` | 可不给首节点，之后用 `set_node_from_config` |
| 外部强制切节点 | `manager.py:FlowManager.set_node_from_config` | 不经函数调用直接进节点，例如事件回调里切到"人工已接入" |
| 注册自定义动作 | `manager.py:FlowManager.register_action`、`_register_action_from_config` | 节点配置里的 `handler` 字段也会被注册 |
| 节点切换主流程 | `manager.py:FlowManager._set_node` | pre_actions → 函数 → context → `LLMRunFrame` → post_actions |
| 函数包装与转移 | `manager.py:FlowManager._create_transition_func` | 区分节点函数和边函数，设置 `run_llm` 和 `on_context_updated` |
| 等并行调用结束再切换 | `manager.py:FlowManager._check_and_execute_transition` | 查 `assistant_aggregator.has_function_calls_in_progress` |
| 函数 schema 与调用选项 | `manager.py:FlowManager._create_function_schema` | 用 pipecat 的 `tool_options` 把 `cancel_on_interruption`、`timeout_secs` 挂到 handler 上 |
| context 策略 | `manager.py:FlowManager._update_llm_context` | APPEND / RESET 选帧类型；RESET_WITH_SUMMARY 带 5 s 超时，失败退回 APPEND |
| `tts_say` 动作 | `actions.py:ActionManager._handle_tts_action` | 推 `TTSSpeakFrame`，`append_text_to_context` 默认 True |
| 动作串行化 | `actions.py:ActionManager._maybe_wait_for_ongoing_actions_to_finish` | 自定义动作前等 `tts_say` 的帧走到 pipeline 末端 |
| 推迟的 post_actions | `actions.py:ActionManager.schedule_deferred_post_actions` | `respond_immediately=False` 时等 `BotStoppedSpeakingFrame` |
| 摘要 | `adapters.py:LLMAdapter.generate_summary` | 走 `llm.run_inference`，S2S 服务没有实现它（见 [pipecat](pipecat.md)） |
| 全局函数 | `manager.py:FlowManager.__init__`（`global_functions`） | 每个节点都可用，示例 `examples/food_ordering.py` 的 `get_delivery_estimate` |
| 评测场景 | `evals/manifest.yaml`、`evals/scenarios/*.yaml` | 基于 `pipecat eval`，默认文本模态 |
| 单元测试 | `tests/test_manager.py`、`tests/test_context_strategies.py`、`tests/test_actions.py` | |

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：不涉及，完全沿用 pipecat 的 user 聚合器和回合策略；Flows 只通过 `respond_immediately=False` 决定"进节点后等用户先说"（`examples/restaurant_reservation.py:create_initial_node`）。
- [打断与截断](../../03-mechanisms/interruption.md)：打断机制本身沿用 pipecat；Flows 特有的一点是 `FlowsFunctionSchema` 和直接函数的 `cancel_on_interruption` 默认 False（`types.py:FlowsFunctionSchema`、`FlowsDirectFunctionWrapper._initialize_metadata`），在 pipecat 1.0.0 之后的语义下这等于"异步工具"，用户插话不会取消函数，边函数仍会完成节点切换。结果在用户插话后才回来时，按 pipecat 的规则以 developer 消息补进 context（推断，独立包锁定的 pipecat 1.4.x 上的确切行为待确认）。
- [首音优化](../../03-mechanisms/first-audio.md)：节点的 `pre_actions: tts_say` 可以在推理之前先说一句固定的话（`actions.py:ActionManager._handle_tts_action`）；但每次经边函数切节点都要两次推理（第一次只产出函数调用，进新节点后 `LLMRunFrame` 再推理出正文，见 `manager.py:FlowManager._create_transition_func` 与 `_set_node`），首音比单次推理多一轮。
- [工具回合](../../03-mechanisms/tool-calls.md)：这是 Flows 的核心：节点函数 `run_llm=True` 照常再推理，边函数 `run_llm=False` 加 `on_context_updated=_check_and_execute_transition`，等本轮并行调用全部结束才切节点（`manager.py:FlowManager._create_transition_func`、`_check_and_execute_transition`）。handler 抛异常时回传 `{"status": "error", ...}` 收口；返回 `(None, next_node)` 的"仅转移"函数回传 `{"status": "acknowledged"}`。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：切节点时 `APPEND` 追加任务说明、`RESET` 用任务说明替换全部历史（人设走 system instruction 不受影响），`RESET_WITH_SUMMARY` 已废弃，建议在 pre_action 里推 pipecat 的 `LLMSummarizeContextFrame`（`manager.py:FlowManager._update_llm_context`）。断线重连不涉及；`state` 只在内存里，没有持久化。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：不涉及。
- [评测](../../03-mechanisms/evaluation.md)：`evals/` 用 `pipecat eval` 对 8 个示例跑行为评测，断言函数是否触发、参数、回复内容，默认文本模态（RTVI `send-text`），可切音频模态（Kokoro 合成、Moonshine 转写），judge 用本地 Ollama（`evals/README.md`）；README 定位为发布前手动跑。

## 取舍与局限

**与自由对话的边界**

- Flows 适合"每一步收一类信息、下一步取决于上一步结果"的流程。节点 prompt 常写"只做这件事，其余礼貌拒绝"或"必须用函数推进对话"。
- 用户随时换话题的场景，只能把通用能力挂成 `global_functions`，图就退化成"全局工具 + 少数阶段工具"，这时 Flows 带来的约束很少。
- 自由对话和结构化流程可以组合：`examples/multi_worker_handoff.py` 让一个自由对话的 `LLMWorker` 路由器通过 pipecat 的 multi-worker bus 把用户交给 Flows worker，流程跑完再交回，两边共享同一份 context（CHANGELOG 1.3.0）。

**代价**

- **切节点多一次推理**：边函数回合先推理出调用，进节点再推理出正文。对延迟敏感的场景，每个阶段切换都要付这一次。
- **RESET 会丢指代**：用户说"它""刚才那个"要靠最近对话解析；只有 APPEND 保留。摘要式重置带 5 s 超时，不适合放在回合关键路径上。
- **函数默认不随打断取消**：适合"用户插话也要把预约提交完"的语义，但 handler 有副作用时，用户中途改主意也拦不住，需要 handler 自己判断（推断）。
- **S2S 上没有示例，行为待确认**：Flows 依赖 `LLMMessagesAppendFrame` 送任务说明、`LLMSetToolsFrame` 换工具、`LLMRunFrame` 触发推理。pipecat 的 OpenAI Realtime 服务对 `LLMMessagesAppendFrame` 只打一行 "NEED TO IMPLEMENT" 的错误日志，Gemini Live 收到 `LLMSetToolsFrame` 什么都不做（见 [pipecat](pipecat.md)）。所以在这两类 S2S 服务上，节点切换后新任务说明和新工具集能否生效，待确认；两个仓库里都没有 Flows + S2S 的示例。
- **没有规则修正层**：最接近的是 handler 内部校验后返回错误结果或别的 `next_node`。模型如果在不该调的时候调了当前节点可见的函数，Flows 不会事后纠正。
- **流程定义在代码里**：独立包里节点只能用 Python 构造。pipecat 本体里的新版增加了 YAML / JSON 声明式配置（`pipecat/src/pipecat/flows/config.py`），独立包没有。
- **独立包已冻结**：新特性和修复只进 `pipecat.flows`，新项目应直接用 pipecat 本体。

## 相关

- 机制页：[判停](../../03-mechanisms/turn-detection.md)、[打断与截断](../../03-mechanisms/interruption.md)、[首音优化](../../03-mechanisms/first-audio.md)、[工具回合](../../03-mechanisms/tool-calls.md)、[会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)、[音频前处理](../../03-mechanisms/audio-preprocessing.md)、[评测](../../03-mechanisms/evaluation.md)
- 项目页：[pipecat](pipecat.md)、[openai-realtime-agents](openai-realtime-agents.md)、[livekit-agents](livekit-agents.md)
- 对比页：[framework-matrix](../../05-comparison/framework-matrix.md)、[decision-guide](../../05-comparison/decision-guide.md)
