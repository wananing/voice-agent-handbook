# xiaozhi-esp32-server

> 仓库：https://github.com/xinnan-tech/xiaozhi-esp32-server
> 许可：MIT（见仓库 LICENSE 文件）
> 分析基于：commit `87c6df77`（2026-09-29）
> 状态：草稿
> 最后更新：2026-10-02

下文 `srv/` 指 `main/xiaozhi-server/`。

## 定位

xiaozhi-esp32-server 是 [xiaozhi-esp32](xiaozhi-esp32.md) 固件的**开源第三方服务端**，按小智通信协议实现（`README.md:8`）。它是一条完整的**级联语音链路**：Silero VAD 判停 → ASR → （意图识别）→ LLM（含 function call）→ 分句 TTS → 限速下发 Opus。每一环都是可替换的 provider，集成了大量国内云厂商和本地模型。

面向：想自建小智后端、换用自己选的 ASR / LLM / TTS、或者接入 Home Assistant、MCP、知识库的开发者和硬件厂商。

和同类的区别：

- 为一个固定设备协议写的**应用服务**，结构直接、配置驱动。没有 [pipecat](../frameworks/pipecat.md) 那种帧管道抽象，也没打算做通用编排框架。
- provider 覆盖面以国内服务为主：讯飞、豆包 / 火山、阿里 / 百炼、腾讯、百度、智谱、FunASR、SenseVoice 等。
- 两种部署：**最简化**（只跑 Python 服务，配置写在 `config.yaml`）和**全模块**（加 Java `manager-api`、Vue `manager-web` 智控台、数据库，多用户多智能体）（`README.md:167-173`）。

## 整体架构

```
设备 WebSocket（:8000）── websocket_server.py：每个连接一个 ConnectionHandler
  │
  │ 文本帧 → textMessageProcessor → 各 handler（hello / listen / abort / mcp / iot / ping / server）
  │ 二进制帧 → _route_message：入口解码 Opus → PCM
  ▼
asr_audio_queue ─► ASR 线程（asr_text_priority_thread）
                     │ VAD.is_vad（Silero，manual 模式跳过）
                     │ ASR.receive_audio：攒音频 / 流式转发
                     ▼ 判停
                  handle_voice_stop：ASR 与声纹识别 asyncio.gather 并行
                     ▼
                  startToChat：输出字数限额 → 必要时打断 → 意图（退出词 / 唤醒词 / intent_llm）
                     │ 下发 stt；executor.submit(conn.chat)
                     ▼
chat()（线程池，同步迭代 LLM 流）── function call ─► 工具（插件 / 服务端 MCP / 设备 MCP / IoT / MCP 接入点）
                     │ 文本增量
                     ▼
tts_text_queue ─► TTS 文本线程：按 sentence_id 过滤、标点切句（或双流式直转）、合成
                     ▼
tts_audio_queue ─► TTS 音频线程 ─► sendAudioMessage：前 5 包直发，之后按 60 ms/包限速
                     ▼
                  设备（tts start / sentence_start / Opus / stop）
```

- **进程 / 线程模型**：asyncio 主循环处理 WebSocket；每个连接有一个线程池执行 `chat()`，ASR、TTS 文本、TTS 音频各一条常驻线程，用 `queue.Queue` 串起来，线程之间通过 `asyncio.run_coroutine_threadsafe` 回到事件循环发送（`srv/core/providers/asr/base.py:36-58`、`srv/core/providers/tts/base.py:371-470`）。
- **配置**：`srv/config.yaml` 的 `selected_module` 选每一类用哪个配置项，配置项的 `type` 决定加载哪个 provider 文件（`srv/config.yaml:233-251`）。全模块部署时按设备从 manager-api 拉取差异化配置。
- **另一个端口**：`http_port: 8003` 提供 OTA、视觉分析等 HTTP 接口（`srv/config.yaml:12-14`）。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 连接入口 | `srv/core/websocket_server.py:_handle_connection` | 每连接 new 一个 `ConnectionHandler` |
| 连接主体 | `srv/core/connection.py:ConnectionHandler` | 状态、队列、组件初始化、`chat()`、`clear_queues()`、`close()` |
| 消息路由 | `srv/core/connection.py:_route_message` | 文本 → handler；二进制 → Opus 解码后进 `asr_audio_queue`；MQTT 网关来的包解析 16 字节头 |
| 收音 | `srv/core/handle/receiveAudioHandle.py:handleAudioMessage` | VAD、唤醒后 2 s 忽略 VAD、服务端 AEC 下的即时打断、120 s 无人声结束 |
| listen 消息 | `srv/core/handle/textHandler/listenMessageHandler.py` | `start` 重置状态；`stop` 触发识别；`detect` 处理唤醒词或文本输入 |
| 打断 | `srv/core/handle/abortHandle.py:handleAbortMessage` | 置 `client_abort`、清队列、回 `tts stop` |
| VAD | `srv/core/providers/vad/silero.py:VADProvider.is_vad` | 唯一实现，Silero ONNX |
| ASR 基类 | `srv/core/providers/asr/base.py:ASRProviderBase` | `receive_audio` / `handle_voice_stop` / `speech_to_text` |
| 开始对话 | `srv/core/handle/receiveAudioHandle.py:startToChat` | 打断判断、意图、下发 stt、提交 `chat` |
| 意图 | `srv/core/handle/intentHandler.py:handle_user_intent` | 退出词 → 唤醒词 → `intent_llm`（`function_call` 模式跳过） |
| LLM 循环 | `srv/core/connection.py:ConnectionHandler.chat` | `sentence_id`、`direct_answer`、工具并行执行、递归最多 5 层 |
| 工具结果 | `srv/core/connection.py:_handle_function_result`、`srv/plugins_func/register.py:Action` | `RESPONSE` / `REQLLM` / `RECORD` / `NONE` / `ERROR` |
| 工具管理 | `srv/core/providers/tools/unified_tool_manager.py`、`base/tool_types.py:ToolType` | 五类来源：服务端插件、服务端 MCP、设备 IoT、设备 MCP、MCP 接入点 |
| TTS 基类 | `srv/core/providers/tts/base.py:TTSProviderBase` | 文本线程切句、音频线程下发、`sentence_id` 过滤 |
| 下发与限速 | `srv/core/handle/sendAudioHandle.py:sendAudioMessage`、`srv/core/utils/audioRateController.py` | 预缓冲 5 包，之后按帧时长限速；估算播放结束后发 `tts stop` |
| 对话历史 | `srv/core/utils/dialogue.py:Dialogue` | 一个 list，无长度上限 |
| 性能测试 | `srv/performance_tester.py` + `srv/performance_tester/` | ASR / 流式 ASR / LLM / TTS / 流式 TTS / VLLM 耗时 |

## 插件化结构

每类模块一个工厂函数，按配置里的 `type` 去 `srv/core/providers/<类>/` 下找同名文件并实例化其中固定名字的类，例如 ASR 是 `ASRProvider`、TTS 是 `TTSProvider`、LLM 是 `LLMProvider`（`srv/core/utils/asr.py:create_instance`、`tts.py:create_instance`、`llm.py:create_instance`）。新增一家厂商就是加一个文件、在 `config.yaml` 里加一个配置项。

| 类 | 基类与接口 | 已有实现（`srv/core/providers/` 下） | 默认 |
|---|---|---|---|
| VAD | `is_vad(conn, pcm)` | `silero` | SileroVAD |
| ASR | `ASRProviderBase`；`interface_type` 分 `STREAM` / `NON_STREAM` / `LOCAL` | 流式：`doubao_stream`、`aliyun_stream`、`aliyunbl_stream`、`xunfei_stream`；非流式：`doubao`、`aliyun`、`tencent`、`baidu`、`fun_server`、`qwen3_asr_flash`、`openai`；本地：`fun_local`（SenseVoiceSmall）、`sherpa_onnx_local`、`vosk` | FunASR 本地 |
| LLM | `response()` / `response_with_functions()`；基类的 function call 默认实现就是不带工具的普通流（`llm/base.py:24-33`） | `openai`（OpenAI 兼容，覆盖 DeepSeek、通义、豆包、智谱等）、`AliBL`、`coze`、`dify`、`fastgpt`、`gemini`、`ollama`、`xinference`、`homeassistant` | ChatGLMLLM（glm-4-flash） |
| TTS | `TTSProviderBase`；`interface_type` 分 `DUAL_STREAM` / `SINGLE_STREAM` / `NON_STREAM` | 双流式：`huoshan_double_stream`、`aliyun_stream`、`alibl_stream`、`xunfei_stream`；单流式：`index_stream`；其余为非流式：`edge`、`doubao`、`aliyun`、`tencent`、`siliconflow`、`cozecn`、`fishspeech`、`gpt_sovits_v2/v3`、`minimax_httpstream`、`paddle_speech`、`openai`、`custom` | EdgeTTS |
| Intent | — | `function_call`、`intent_llm`、`nointent` | function_call |
| Memory | — | `nomem`、`mem_local_short`、`mem0ai`、`powermem`、`mem_report_only` | nomem |
| VLLM | — | `openai` 兼容 | ChatGLMVLLM |

### 各家模型的适配做法

- **ASR 输出统一成文本**：FunASR / SenseVoice 返回带语言、情绪的 dict，服务端把它和声纹识别出的说话人一起序列化成 JSON 字符串交给 LLM（`asr/base.py:128-162`）。
- **流式 ASR 按说话开始建连**：以豆包为例，VAD 首次判到有声时才建 WebSocket（`asr/doubao_stream.py:69-100`），auto 模式下以服务端返回的 `definite` 分句为准触发识别完成，manual 模式下累积分句直到收到 `listen stop`（`doubao_stream.py:175-230`）。其他流式 ASR 是否同样以厂商端点为准，待确认。
- **LLM 关思考**：OpenAI 兼容适配器按 base_url 域名自动注入关闭思考的参数，覆盖阿里云、DeepSeek、智谱、Moonshot、火山（`llm/openai/openai.py:14-18`、`_apply_thinking_disabled`）。
- **文本形式的工具调用**：有些模型把工具调用写在 content 里（`<tool_call>{json}`），`chat()` 检测到这个前缀就按工具调用解析（`srv/core/connection.py:1167`）。
- **双流式 TTS**：LLM 的文本增量直接送给厂商服务端，由对方切句合成；打断时调用 `cancel_session`（`srv/core/providers/tts/huoshan_double_stream.py:274-290`、`:454`）。非流式 TTS 走本地标点切句后逐句合成，每句最多重试 5 次（`srv/core/providers/tts/base.py:126-166`）。
- 选型参考：仓库 README 推荐"入门全免费"（FunASR + glm-4-flash + EdgeTTS）和"流式配置"（讯飞流式 ASR + qwen-flash + 火山双流式 TTS）两套；对应的延迟数字在外部报告仓库 `xiaozhi-performance-research`，这里不引用。

## 判停方式

| 模式（由设备 `listen start` 的 `mode` 决定） | 判停 | 依据 |
|---|---|---|
| manual | 不跑 VAD（`is_vad` 直接返回 True，全部缓存），等设备松键发 `listen stop` | `vad/silero.py:55-58`、`listenMessageHandler.py:38-54` |
| auto / realtime + 非流式 ASR | Silero VAD：双阈值 0.5 / 0.3（中间值沿用上一帧），最近 5 帧中 ≥3 帧有声算"有声"；有声→无声且距最后一次有声 ≥`min_silence_duration_ms` 判停；**至少 15 帧（约 0.9 s）音频才送识别** | `vad/silero.py:88-112`、`config.yaml:606-611`（默认 200 ms）、`asr/base.py:75-82` |
| auto / realtime + 流式 ASR | VAD 只负责"开始说话时建连"，结束由 ASR 服务端的分句结果决定（豆包为 `definite`） | `asr/doubao_stream.py:205-228` |

细节：

- VAD 按 512 样本（32 ms）一块跑，每个连接独立的 Silero 状态（`vad/silero.py:39-44`、`:67-86`）。
- 静音时长用的是墙钟（`time.time()` 和上次有声时间之差，`vad/silero.py:106-112`），不是音频时间；上行卡顿后突发到达时，判停时刻会偏移（推断）。
- 唤醒后 2 s 内忽略 VAD，防止把唤醒词尾音当成提问（`srv/core/handle/receiveAudioHandle.py:20-40`）。
- 没有语义判停模型；"说完没有"只看静音。配置注释建议说话停顿长的用户把 `min_silence_duration_ms` 调大（`config.yaml:611`）。
- ASR 结果为空时什么都不发给设备（`asr/base.py:161-170` 只在 `text_len > 0` 时继续）。

## 打断处理

1. **设备主动打断**：收到 `abort` → `handleAbortMessage`：`client_abort = True`，清空 TTS 文本队列、音频队列、上报队列并重置限速器，立刻回 `{"type":"tts","state":"stop"}`，清掉 `client_is_speaking`（`abortHandle.py:9-20`、`srv/core/connection.py:clear_queues`）。
2. **语音打断（auto / realtime）**：在 `startToChat` 里，如果设备正在播放且不是 manual 模式，先打断再处理新问题（`srv/core/handle/receiveAudioHandle.py:86-88`）。也就是说**打断发生在判停 + ASR 完成之后**，不是用户一开口就打断。
3. **服务端 AEC 下的即时打断**：设备声明了 `features.aec` 时，VAD 一判到有声就打断（`srv/core/handle/receiveAudioHandle.py:27-30`）。服务端 AEC 只在 MQTT 网关路径生效（`srv/core/connection.py:_process_mqtt_audio_message`、`_apply_aec`）。
4. **manual 模式不做语音打断**，只响应设备的 `abort`。
5. **各线程如何停**：LLM 流式循环每个 chunk 检查 `client_abort` 并 `break`（`srv/core/connection.py:1157`）；TTS 文本线程和音频线程都检查 `client_abort`（`srv/core/providers/tts/base.py:375`、`:433`）；`sentence_id` 不匹配的旧消息在 TTS 文本线程和 `sendAudioMessage` 两处被丢弃（`srv/core/providers/tts/base.py:378-381`、`srv/core/handle/sendAudioHandle.py:21-24`）。`client_abort` 在下一次 `startToChat` 时复位（`srv/core/handle/receiveAudioHandle.py:101`）。
6. **上下文不截断**：被打断时，LLM 已经生成的全部文本仍写入历史（`srv/core/connection.py:1374-1378`），不按设备实际播放位置截断，也没有"被打断"标记。设备也不回传播放位置。

## 意图识别与 function call

**三种意图模式**（`srv/config.yaml:244-251` 注释）：

- `function_call`（默认）：不做前置分类，工具表直接给 LLM。
- `intent_llm`：进 LLM 前先用一次 LLM 做意图分类，串行，配置注释自己承认"会增加处理时间"。
- `nointent`：不用工具。

无论哪种模式，`startToChat` 都先过两道零成本规则：精确匹配退出词（`exit_commands`，`config.yaml:84-86`）、唤醒词（命中时播缓存好的回复音频，`helloHandle.py:checkWakeupWords`）。

**function call 的做法**（`srv/core/connection.py:ConnectionHandler.chat`）：

- 顶层调用时在工具表里加一个虚拟工具 `direct_answer`，说明是"不匹配其他工具时，把回复写在 `response` 参数里"。注释写明目的：把"调不调工具"的二选一变成"调哪个"的多选，防止小模型误触发真实工具（`srv/core/connection.py:58-76`）。流式阶段增量解析 `response` 参数送 TTS，保住首音（`:1175-1197`）。递归调用时不再注入，避免循环。
- 会话开始时注入几条 few-shot 示例，演示 `direct_answer` 和真实工具的用法（`srv/core/connection.py:_inject_tool_call_fewshot`）。
- 多个工具调用**并行**提交到事件循环，逐个等待，单个超时 `tool_call_timeout`（默认 30 s），超时统一返回"哎呀，网络遇到点问题，请稍后再试下！"（`srv/core/connection.py:1328-1371`）。
- 工具返回值声明后续动作（`plugins_func/register.py:25-31`）：`RESPONSE` 直接把结果念出来不再过 LLM；`REQLLM` 把结果写回历史、递归调 `chat(depth+1)`；`RECORD` 只记历史；`NONE` 什么都不做。
- 递归最多 5 层，到顶后注入"请直接回答"的系统提示并禁用工具（`srv/core/connection.py:1079-1100`）。
- 设备 MCP 工具：服务端作为 JSON-RPC client 发 `tools/call`，自增 id + Future 等结果，30 s 超时（`srv/core/providers/tools/device_mcp/mcp_handler.py`）。
- 工具执行期间没有专门的"正在查询"提示机制，只有 LLM 在 tool_call 之前流出的文本会先播（`srv/core/connection.py:1318-1324`）。

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：纯静音判停。manual 由设备松键决定；auto / realtime 用 Silero 双阈值 + 5 帧滑窗 + 默认 200 ms 静音（`vad/silero.py:VADProvider.is_vad`），非流式 ASR 还要求至少约 0.9 s 音频；流式 ASR 以厂商端点为准（`asr/doubao_stream.py`）。没有语义判停。
- [打断与截断](../../03-mechanisms/interruption.md)：`abortHandle.py:handleAbortMessage` 置 `client_abort`、清队列、回 `tts stop`；`sentence_id` 作为回合代际过滤残留音频；语音打断在 ASR 完成后才发生（`srv/core/handle/receiveAudioHandle.py:startToChat`），服务端 AEC 时 VAD 有声即打断。被打断的回复全文写入历史，不按播放位置截断。
- [首音优化](../../03-mechanisms/first-audio.md)：首句遇逗号、顿号即切（`srv/core/providers/tts/base.py:95-111` 的 `first_sentence_punctuations`），后续只在句末标点切；双流式 TTS 直接转发 LLM 增量；`direct_answer` 参数流式送 TTS；唤醒词回复走缓存音频；ASR 与声纹并行；下行前 5 包直发作预缓冲（`srv/core/handle/sendAudioHandle.py:18`）。
- [工具回合](../../03-mechanisms/tool-calls.md)：`ConnectionHandler.chat` 内的 function call 循环，`direct_answer` 虚拟工具、并行执行 + 30 s 超时、`RESPONSE / REQLLM / RECORD` 结果分类、最多 5 层递归；工具来源五类（`tools/base/tool_types.py:ToolType`）。工具执行期间无填充语。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：一个 WebSocket 连接就是一个会话，`Dialogue` 无长度上限、无摘要（`utils/dialogue.py`）；断线不恢复，跨会话靠记忆模块（如 `mem_local_short` 在关闭时用 LLM 压缩整段对话，下次注入 system prompt）。120 s 无人声时念结束语后断开（`srv/core/handle/receiveAudioHandle.py:no_voice_close_connect`）。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：服务端不做降噪和 AGC；只有 MQTT 网关路径上的实验性服务端 AEC（互相关延迟估计 + 维纳滤波 + 谱减，`srv/core/connection.py:_apply_aec`），设备端需编译 `USE_SERVER_AEC`。这是服务端近似方案，按设备在上行 v2 帧里回报的播放时间戳对齐参考信号，效果依赖时间戳精度，不等同于端侧 AEC。
- [评测](../../03-mechanisms/evaluation.md)：`performance_tester.py` 测各 provider 的耗时（ASR 整段 / 首字、LLM 首 token、TTS 首包），只算平均值，测试音频是系统提示音，不测识别准确率；`tests/` 下是 provider 契约测试（构造、接口存在、返回非空）。没有端到端对话质量评测。

## 取舍与局限

- **级联 + 纯静音判停**：结构简单、每环可换，但判停只看静音，句中停顿容易被切；打断要等 ASR 完成，延迟 = 判停 + 识别。
- **协议层弱保证**：回合 id（`sentence_id`）不下发、ASR 为空不回任何消息、TTS 失败只写日志，设备端无法区分"没听见"和"还在想"。
- **上下文与实际播放不一致**：打断后历史里是模型生成的全文，用户其实只听到一部分。
- **历史无上限**：长会话的 token 成本和延迟会持续增长，只靠 120 s 无声断连兜底。
- **线程 + 队列 + 共享标志**的并发模型：`client_abort`、`sentence_id` 是连接级共享属性，竞态问题需要逐个排查（未系统验证）。
- **provider 质量参差**：IndexStream（`SINGLE_STREAM`）、MiniMax 等实现文件里没有 `client_abort` 检查，打断只能依赖基类线程；双流式 TTS（如火山）会主动 `cancel_session`。各家行为不一致，选型时要逐个看代码。
- `intent_llm` 模式多一次串行 LLM 调用；`function_call` 模式要求 LLM 支持工具调用（基类兜底时会退化成不带工具的普通对话）。
- 性能测试工具的口径偏粗，数字只能用于粗排。

## 相关

- 机制页：[判停](../../03-mechanisms/turn-detection.md)、[打断与截断](../../03-mechanisms/interruption.md)、[首音优化](../../03-mechanisms/first-audio.md)、[工具回合](../../03-mechanisms/tool-calls.md)、[会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)、[评测](../../03-mechanisms/evaluation.md)
- 项目页：[xiaozhi-esp32](xiaozhi-esp32.md)、[funasr](../asr/funasr.md)、[sensevoice](../asr/sensevoice.md)、[pipecat](../frameworks/pipecat.md)
- 对比页：待补
