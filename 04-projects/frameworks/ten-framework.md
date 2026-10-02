# TEN Framework

> 仓库：https://github.com/TEN-framework/ten-framework
> 许可：Apache-2.0 加附加条款：不得部署在终端用户设备上，不得与 Agora 产品竞争（见仓库 LICENSE 文件）
> 分析基于：commit ca00160c（版本号 0.11.73）
> 状态：草稿
> 最后更新：2026-10-01

路径缩写：`rt/` = `core/src/ten_runtime/`；`ext/` = `ai_agents/agents/ten_packages/extension/`；`ex/` = `ai_agents/agents/examples/`。

## 定位

TEN 是一个"扩展图"式的实时多模态 agent 框架，分两层：

- **运行时**：C 写的核心，提供扩展（extension）、图（graph）、消息路由和线程模型，上面有 Python、Go、Node.js、C++ 绑定。
- **AI 层**（`ai_agents/`）：上百个厂商扩展（ASR、LLM、TTS、实时模型、工具），一组示例 app，一个 Go 写的会话服务端，加 playground 前端。

面向的场景：想用配置文件拼装"RTC → ASR → LLM → TTS"管线，按节点替换厂商，并且需要多语言扩展混用的团队。和 [pipecat](pipecat.md)、[livekit-agents](livekit-agents.md) 的主要区别：

- 拓扑写在 `property.json` 的 graph 里，不写在代码里。
- 扩展可以用不同语言写，在同一进程内互通。
- 判停（TEN Turn Detection）和 VAD（ten-vad）是 TEN 团队自己的模型，以扩展形式接入。

和那两家相比，**框架本身不提供输出仲裁、按播放位置截断、实时模型上下文同步这一层**。这些逻辑要写在示例的中心扩展 `main_control` 里，而现有示例写得比较薄。

## 整体架构

**运行时的四个概念**

| 概念 | 是什么 | 位置 |
|---|---|---|
| extension | 最小处理单元。生命周期 `on_init → on_start → on_cmd / on_data / on_audio_frame / on_video_frame → on_stop → on_deinit` | `rt/extension/`；Python 基类 `rt/binding/python/interface/ten_runtime/async_extension.py:AsyncExtension` |
| extension_group | 线程分组，同组扩展共用一个 extension thread | `rt/extension_group/`、`rt/extension_thread/` |
| graph | `property.json` 里 `predefined_graphs[]` 的 `nodes` 加 `connections`，按消息类型和名字路由 | 说明见 `docs/ai/L1/L2/graph_configuration.md` |
| app | 一个进程，装载若干 graph | `rt/app/` |

**消息类型**（仓库根下 `core/include/ten_runtime/msg/msg.h:TEN_MSG_TYPE`）：

- `cmd`：带结果回传（`CmdResult`，可以流式多次返回）。用于控制，比如 `flush`、`tool_call`、`chat_completion`。
- `data`：单向 JSON 属性。用于业务数据，比如 `asr_result`、`tts_text_input`、`text_data`。
- `audio_frame` / `video_frame`：媒体帧。
- 内部命令：`start_graph`、`stop_graph`、`timer` 等。

**路由**：`rt/extension/extension.c:ten_extension_determine_out_msgs` 先看消息自带的目的地。消息带了 dest 就直接投递，不查 graph；没带才走 `ten_extension_determine_out_msg_dest_from_graph`，查不到就报错。同进程内跨扩展投递是跨线程投到目标 extension thread 的 runloop，不经过网络序列化。graph 的连接在生命周期内不变；运行时可以用 `start_graph` / `stop_graph` 增删整张图，但不能改边。

**多语言**：核心是 C（`rt/`），绑定在 `rt/binding/{go,nodejs,python}`，C++ 是头文件封装（仓库根下 `core/include/ten_runtime/binding/cpp`）。示例里有纯 Node.js 版（`ex/voice-assistant-nodejs`）。Python 的 `AsyncExtension` 有单线程和多线程两种模式（`async_extension.py:ThreadMode`），多线程模式下每个扩展自带线程和 asyncio loop。多个 Python 扩展在同一进程里是否受 GIL 制约，仓库里没有讨论，待确认。

**级联示例的 graph**（`ex/voice-assistant/tenapp/property.json`，9 个 graph，只差厂商节点）：

```
agora_rtc ──audio_frame──► streamid_adapter ──audio_frame──► stt
stt ──data:asr_result──► main_control
tts ──audio_frame──► agora_rtc
weatherapi_tool ──cmd:tool_register──► main_control
message_collector ──data──► agora_rtc            （字幕走 RTC 数据通道）

graph 里没有声明、由 main_control 用 Loc("", "", dest) 点名发送的边：
main_control ──cmd:chat_completion──► llm          （流式 CmdResult 即 token 流）
main_control ──data:tts_text_input / tts_flush──► tts
main_control ──cmd:flush──► agora_rtc
main_control ──cmd:tool_call──► 注册该工具的扩展
```

实际拓扑是以 `main_control`（`ex/voice-assistant/tenapp/ten_packages/extension/main_python/`）为中心的星形：graph 只描述媒体流和"汇入中心"的边，编排逻辑全在中心扩展的 Python 代码里。`helper.py:_send_data` 的注释自己承认，这种写法让扩展"只对这张图有意义"。

**音频怎么流**：

1. `agora_rtc`（预编译包，源码不在仓库）把用户音频作为 `pcm_frame` 发出。
2. `streamid_adapter` 给每帧打上 `session_id` 元数据（`ext/streamid_adapter/extension.py:on_audio_frame`）。
3. 帧扇出给 `stt`，接了 VAD 的示例同时扇出给 `vad`（graph 里同一个 source 有两个 dest，运行时逐个 clone）。
4. 下行方向，`tts` 的 `pcm_frame` 直连 `agora_rtc`。
5. S2S 版（`ex/voice-assistant-realtime`）把 `stt/llm/tts` 换成一个 `v2v` 节点（`ext/openai_mllm_python`），它的输出音频也直连 `agora_rtc`。

**部署形态**

- Go 服务端：`/start` 为每个 channel 起一个 worker 进程，命令是 `tman run start -- --property <临时文件>`（`ai_agents/server/internal/worker_linux.go:Worker.start`）；`/stop` 发信号停止；`/ping` 续命，超时后自动停（`ai_agents/server/internal/http_server.go:handlerStart` / `handlerStop` / `handlerPing`）。
- 并发只按数量限流：`WORKERS_MAX` 超了回 429。名字含 "gemini" 的 graph 另有单独上限（`handlerStart`）。
- 没有负载计算，也没有预热进程池，每次 `/start` 都要冷启动进程、加载扩展、连各厂商。
- 开发和部署用 Docker Compose（`docs/ai/L1/L2/deployment.md`），扩展包由 `tman` 管理。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 消息路由 | `rt/extension/extension.c:ten_extension_determine_out_msgs` | 自带 dest 的消息绕过 graph |
| Python 扩展基类 | `rt/binding/python/interface/ten_runtime/async_extension.py:AsyncExtension` | 单线程 / 多线程模式 |
| 级联中心编排 | `ex/voice-assistant/.../main_python/extension.py:MainControlExtension` | `_on_asr_result`、`_on_llm_response`、`_interrupt` |
| LLM 调用与工具回合 | `ex/voice-assistant/.../main_python/agent/llm_exec.py:LLMExec` | `_send_to_llm` 发 `chat_completion`；`_handle_llm_response` 处理 `LLMResponseToolCall` |
| 切句送 TTS | `ex/voice-assistant/.../main_python/helper.py:parse_sentences` | 按标点切句 |
| TTS 冲刷 | `ext/elevenlabs_tts2_python/extension.py:ElevenLabsTTS2Extension.cancel_tts` / `handle_completed_request` | 同一 `request_id` 的迟到文本丢弃；`tts_audio_end` 带原因 |
| 文本语义判停 | `ext/ten_turn_detection/extension.py:TENTurnDetectorExtension`、`turn_detector.py:TurnDetector.eval` | 每个 ASR final 调一次 LLM 判停模型 |
| VAD | `ext/ten_vad_python/extension.py:TENVADPythonExtension` | 帧级概率 → 起止状态机 → `start_of_sentence` / `end_of_sentence` cmd |
| S2S 扩展 | `ext/openai_mllm_python/extension.py:OpenAIRealtime2Extension` | `start_connection`、`_handle_reconnect`、`_resume_context` |
| 会话服务端 | `ai_agents/server/internal/http_server.go:handlerStart`、`worker_linux.go:Worker.start` | 一会话一进程 |
| 扩展测试 | `rt/binding/python/interface/ten_runtime/async_test.py:AsyncExtensionTester` | 扩展级单测框架 |
| 厂商一致性测试 | `ai_agents/agents/integration_tests/asr_guarder`、`tts_guarder` | ASR / TTS 扩展的协议守护测试 |

## 对各机制的回答

### [判停](../../03-mechanisms/turn-detection.md)

**默认示例**：没有独立判停，直接用 STT 的 `final`。`main_control._on_asr_result` 收到 final 就把文本送进 LLM 队列，端点完全由 ASR 厂商决定。

**TEN Turn Detection**（`ext/ten_turn_detection/`，示例 `ex/voice-assistant-with-turn-detection`）

- 接在 ASR 之后，不接 VAD。`main_control._on_asr_result` 把所有 ASR 结果点名发给 `turn_detection`（`_send_to_turn_detection`）。
- 扩展缓存 final 文本，每来一个 final 就调 `TurnDetector.eval`。eval 先去掉标点，再向一个 OpenAI 兼容服务请求 `max_tokens=1`。
- 返回值有三种：`finished` 就提交本轮；`unfinished` 就继续等，同时启动 `force_threshold_ms=5000` 的强制提交计时（`_eval_force_chat`）；`wait` 表示用户在说"等一下"，丢掉这一轮、不回复（`_process_new_turn`）。
- 请求超时 5 s，出错默认按 `unfinished` 处理。
- 模型本身不在仓库里：示例的 `cerebrium/` 目录用 vLLM 起 HF 上的 `TEN_Turn_Detection` 服务。模型规模和中文效果，仓库内待确认。

**ten-vad**（`ext/ten_vad_python/`，依赖从 GitHub 安装 `ten-vad` 包）

- 每 16 ms 一跳算一个语音概率。最近 120 ms 全部高于阈值，判为开始说话；最近 1000 ms 全部低于阈值，判为结束（`TENVADPythonExtension._check_state_transition`，默认值在 `config.py:TENVADConfig`）。
- 这个扩展同时把音频原样透传出去。
- 示例 `ex/voice-assistant-with-ten-vad` 里，VAD 的 `start_of_sentence` 只用来打断（`_on_vad_start_of_sentence`）；`end_of_sentence` 被转成事件，但没有处理器，判停仍然靠 ASR final。

**S2S 版**：用上游的服务端 VAD，判停由上游决定。

### [打断与截断](../../03-mechanisms/interruption.md)

**级联**

- 触发：`_on_asr_result` 里 `event.final or len(event.text) > 2`，也就是部分转写超过 2 个字符就打断。接 VAD 的示例改由 `start_of_sentence` 触发；接判停的示例由判停扩展在新一轮开始时发 `flush` cmd 触发。
- 动作：`MainControlExtension._interrupt` 逐个点名下发三条：
  1. `agent.flush_llm()`：清 LLM 输入队列，取消在途请求；
  2. `tts_flush` 给 TTS；
  3. `flush` cmd 给 `agora_rtc`，清下行播放缓冲。
- TTS 侧的防线：`cancel_tts` 把当前 `request_id` 标记为已完成，之后同一 `request_id` 的迟到文本直接丢弃；`tts_audio_end` 带上 `INTERRUPTED` 原因。相当于在消费端做了一层按回合的代际过滤。
- `agora_rtc` 收到 flush 后的具体行为，因为是预编译包，待确认。
- 上下文截断：不涉及。没有按播放位置截断上下文。`tts_audio_start/end` 送到了 `main_control`，但没有用来驱动任何逻辑。

**S2S**

- `v2v` 收到上游的 `input_audio_buffer.speech_started` 后发 `mllm_server_interrupted`；`main_control` 收到后只给 `agora_rtc` 发一个 flush（realtime 示例 `extension.py:_interrupt`）。
- `conversation.item.truncate` 的代码在 `openai_mllm_python/extension.py:start_connection` 里被注释掉了，被打断的回复只在字幕里追加 `[interrupted]`。上游上下文保留全文。

### [首音优化](../../03-mechanisms/first-audio.md)

- LLM 流式输出，`main_control` 按标点切句（`helper.py:parse_sentences`），每切出一句就发一次 `tts_text_input`。
- 没有抢先生成，没有预热进程或连接（每会话冷启动）。
- 每个 LLM token 块和每句 TTS 文本都走 JSON 属性编解码，跨扩展投递要跨线程；仓库里没有这部分开销的数字，待确认。

### [工具回合](../../03-mechanisms/tool-calls.md)

- 工具本身也是扩展：启动时向 `main_control` 发 `tool_register` cmd 注册（`LLMToolMetadata`）。
- LLM 返回 `LLMResponseToolCall` 后，`LLMExec._handle_llm_response` 按注册来源发 `tool_call` cmd，`await` 结果。结果类型为 `llmresult` 时，把 function call 和 output 写进上下文，再调一次 LLM。整个过程串行。
- 工具执行期间没有填充语或进度播报，也没有异步工具或回帖机制。
- S2S 版：`v2v` 把上游的 function call 转成 `mllm_server_function_call` 交给 `main_control`，工具结果再回给 `v2v`（`send_client_function_call_output`）。

### [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)

- 级联：上下文在 `LLMExec` 里维护，每轮整段发给 `llm` 扩展。会话进程退出，上下文就没了，不涉及跨会话恢复。
- S2S：连接断开后，`OpenAIRealtime2Extension._handle_reconnect` 等 1 s 再调用 `start_connection`，没有退避和次数上限，注释说的"exponential backoff"在代码里没有实现。新会话收到 `session.created` 后调用 `_update_session`，再调 `_resume_context(self.message_context)`，把消息逐条 `conversation.item.create` 发回去。
- `message_context` 在基类 `AsyncMLLMBaseExtension` 里维护。基类来自 `ten_ai_base` 包，这个包不在本仓库里，上下文怎么积累、有没有截断，待确认。
- 没有服务端镜像，也没有 diff，没有致命 / 可恢复的错误分级。

### [音频前处理](../../03-mechanisms/audio-preprocessing.md)

- 服务端扩展里没有降噪或 AEC。前处理依赖 Agora RTC 一侧，是否开启 AEC / ANS 待确认。
- `ten_vad_python` 只做 VAD；ten-vad 模型本身只有预加重和 STFT 前处理（见 [ten-vad](../turn-vad/ten-vad.md)）。
- `streamid_adapter` 只打元数据，不改音频。

### [评测](../../03-mechanisms/evaluation.md)

- 扩展级单测：`AsyncExtensionTester` 能单独驱动一个扩展，收发 cmd / data / 音频帧。`ext/` 下约两百个文件用了它。
- 厂商一致性测试：`integration_tests/asr_guarder`、`tts_guarder` 对 ASR / TTS 扩展跑协议级用例，例如 TTS 的 flush 之后不得再出音频。
- 没有对话质量 judge、判停准确率或端到端延迟基准。TEN Turn Detection 的评测数据不在本仓库，待确认。

## 取舍与局限

- **graph 是布线，不是仲裁**：graph 回答"谁能给谁发什么"，不回答"此刻谁持有话筒"。多个来源连到 `tts` 或 `agora_rtc` 时，只按到达顺序投递，没有优先级，也没有代际。示例里真正的编排集中在 `main_control`，而且绕过 graph 点名发消息，graph 的说明作用被削弱。
- **换厂商成本低**：9 个级联 graph 只差一个节点，改 `addon` 即可，这是图式配置的实际收益。
- **多语言是真需求时才划算**：C 运行时加多绑定、`tman` 打包工具链，学习成本明显高于纯 Python 框架。
- **示例里的打断和截断偏弱**：级联按"转写超过 2 个字符"打断，S2S 版只清 RTC 缓冲，迟到的上游音频帧没有代际过滤（推断），截断代码被注释掉。生产使用需要自己补。
- **判停模型是外部服务**：TEN Turn Detection 每次 final 都要远程调一次 LLM（示例部署在 A10 GPU，见 `cerebrium/cerebrium.toml`），延迟和成本要单独核算。
- **部署粗放**：一会话一进程，按数量限流，没有预热。
- **关键依赖不在仓库**：`agora_rtc`、`ten_ai_base` 是外部包，很多行为只能读调用方推断。

## 相关

- 机制页：[判停](../../03-mechanisms/turn-detection.md)、[打断与截断](../../03-mechanisms/interruption.md)、[首音优化](../../03-mechanisms/first-audio.md)、[工具回合](../../03-mechanisms/tool-calls.md)、[会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)、[音频前处理](../../03-mechanisms/audio-preprocessing.md)、[评测](../../03-mechanisms/evaluation.md)
- 项目页：[ten-vad](../turn-vad/ten-vad.md)、[pipecat](pipecat.md)、[livekit-agents](livekit-agents.md)
- 架构页：[级联](../../02-architectures/cascade.md)、[发言权](../../02-architectures/floor-control.md)
- 对比页：[framework-matrix](../../05-comparison/framework-matrix.md)、[decision-guide](../../05-comparison/decision-guide.md)
