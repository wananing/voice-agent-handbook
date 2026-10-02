# FunASR

> 仓库：https://github.com/modelscope/FunASR
> 许可：代码 MIT；模型权重在 ModelScope 另有许可，待确认（见仓库 LICENSE 文件）
> 分析基于：commit 12e417f
> 状态：草稿
> 最后更新：2026-10-01

## 定位

阿里通义实验室的语音识别工具箱：一套 Python 训练 / 推理框架（`AutoModel`）加上一组工业级中文模型（Paraformer、SenseVoiceSmall、Fun-ASR-Nano、FSMN-VAD、ct-punc 等），再配上 C++ ONNX runtime 的 websocket 服务。

在 voice agent 里它的价值是"自托管中文流式 ASR 服务端"：`funasr-wss-server-2pass` 一个进程就把流式 partial、VAD 断句、句尾离线纠错、标点、ITN、热词、字级时间戳串好了。和 sherpa-onnx 比，它更偏服务端、功能更全，端侧覆盖弱；和 SenseVoice 仓库比，SenseVoice 只是它的一个模型。

README 自己强调"FunASR 是工具箱，需要分别选择任务、checkpoint 和运行时"，一个模型支持的能力不代表所有服务后端都支持（`README_zh.md:122-123`）。下面按 voice agent 实际会走的路径拆。

## 整体架构

```
客户端 ──PCM(int16, 8k/16k)──▶ funasr-wss-server-2pass（C++，websocketpp + asio 线程池）
                                   │ 每连接一个 online handle
                                   ▼
                     FunTpassInferBuffer（funasrruntime.cpp）
                     ├─ Audio::Split + FSMN-VAD 在线切段
                     ├─ 在线 Paraformer：600 ms 一块 ─▶ "2pass-online" partial（无标点、无热词）
                     └─ VAD 段结束 / is_speaking:false
                          ─▶ 离线模型（Paraformer-large 或 SenseVoiceSmall）+ 热词
                          ─▶ ct-punc ─▶ FST ITN ─▶ 时间戳平滑 ─▶ "2pass-offline" final
```

核心抽象是"两遍"（2pass）：在线 Paraformer 按块出低延迟 partial，FSMN-VAD 判定一段话结束后，用离线大模型对整段重识别作为定稿。客户端协议只有三种消息：首包 JSON 配置、二进制 PCM、结束标志 `{"is_speaking": false}`（`runtime/docs/websocket_protocol_zh.md:63-89`）。

进程模型：websocket 层是 asio io 线程 + decoder 线程池，`--decoder-thread-num` 决定并发路数，`--model-thread-num` 是每路 ONNX 内部线程（`runtime/websocket/bin/funasr-wss-server-2pass.cpp:106-110`）。每个连接持有独立的在线模型状态，离线模型共享。CPU 版没有跨连接 batch（从代码推断）；GPU 走 Triton（`runtime/triton_gpu/`，有 paraformer online / offline 和 sense_voice_small 三个 model repo）。

另外两条 Python 路径：

- `AutoModel` 逐块流式：`model.generate(input=chunk, cache=cache, is_final=..., chunk_size=[0,10,5])`，每块 600 ms，调用方自己维护 `cache`（`README_zh.md:234-248`）。适合嵌进 Python 编排框架。
- Fun-ASR-Nano 实时 websocket（`funasr/bin/realtime_ws.py`）：FSMN-VAD 切段 + vLLM 解码 LLM-ASR，partial 是每 `--decode-interval`（默认 0.48 s）对最近 `--partial-window-sec`（默认 8 s）窗口重解码；支持服务端 VAD 或客户端 COMMIT 两种端点模式（`realtime_ws.py:1-10`、`:1448-1460`）。这是"伪流式"，需要 GPU。

### voice agent 视角的能力清单

| 维度 | 2pass C++ 服务（Paraformer） | 说明 |
|---|---|---|
| 流式 | 是 | 在线 Paraformer，`chunk_size:[5,10,5]` = 当前块 600 ms、回看 300 ms、前瞻 300 ms（`websocket_protocol_zh.md:74`）；块长计算见 `runtime/onnxruntime/src/paraformer-online.cpp:112` |
| 首字延迟 | 约一块 + 前瞻 | 非 final 块会清掉前瞻区的 CIF 权重，前瞻那 300 ms 的字要等下一块才出（`funasr/models/paraformer/cif_predictor.py:343-346`，`forward_chunk`）。实测值待确认 |
| 定稿延迟 | VAD 尾部静音 + 离线重识别 | VAD 默认尾部静音 800 ms（`runtime/onnxruntime/src/e2e-vad.h:83`），之后跑离线模型 + 标点 + ITN。离线 Paraformer-large int8 单路 RTF 0.028（`runtime/docs/benchmark_onnx_cpp.md`，32 线程口径） |
| partial / final | 两种消息 | `mode:"2pass-online"` 是 partial，`"2pass-offline"` 是定稿；`slice_type` 0/1/2 标一段开始 / 进行中 / 结束（`websocket-server-2pass.cpp:84-110`） |
| 内置端点 | FSMN-VAD | 尾部静音 800 ms、最长段 15000 ms（C++ 默认，`e2e-vad.h:83-94`），实际值以模型 `config.yaml` 为准（`fsmn-vad.cpp:34-38`） |
| 热词 | 有，仅离线那一遍 | 首包 `hotwords` JSON 或服务端 `--hotword` 文件；在线 `Forward` 不带热词，离线 `Forward` 才带 `hw_emb`（`funasrruntime.cpp:531` 对比 `:573`）；"热词权重仅在 fst 热词服务下生效"（`websocket_protocol_zh.md:81`） |
| 时间戳 | 有，仅离线那一遍 | 用时间戳模型时返回字级 `[[start_ms,end_ms],...]` 和 `stamp_sents`（`websocket_protocol_zh.md:94-103`），起点按 VAD 段 `global_start` 偏移（`funasrruntime.cpp:584-590`） |
| 标点 | 有 | partial 不加标点；定稿过 ct-punc 在线标点模型，`input_finished` 时补"。"（`funasrruntime.cpp:598-602`） |
| ITN | 有 | FST ITN，首包 `itn` 开关，默认 True；ITN 后重新对齐时间戳（`funasrruntime.cpp:605-617`） |
| 输入 | int16 PCM | 8k / 16k，非 16k 时内部重采样（`runtime/onnxruntime/src/audio.cpp:300` `Audio::WavResample`）。不收 Opus |

离线那一遍可以换成 SenseVoiceSmall（`svs_lang` / `svs_itn` 参数，`websocket_protocol_zh.md:78-79`）。换成 SenseVoice 时 C++ 侧不再走 ct-punc 和 FST ITN，而是直接用模型自己的输出（`funasrruntime.cpp:618-620` 的 else 分支），时间戳是否可用待确认。

### 中文效果定位

| 模型 | 参数量 | 仓库给的数字 | 出处 |
|---|---|---|---|
| Paraformer-zh（离线，带时间戳） | 220M | AISHELL-1 CER 1.95%（int8 后不变） | `README_zh.md:199`；`runtime/docs/benchmark_onnx_cpp.md` |
| Paraformer-zh-streaming | 220M | 无单独 CER | `README_zh.md:200` |
| SenseVoiceSmall | 234M | 历史报告 GPU 行 CER 7.81%（测试集见原文） | `README_zh.md:197`；`docs/benchmark/historical_asr_zh.md:32` |
| Fun-ASR-Nano | 800M | README 正文无 CER | `README_zh.md:195` |
| fsmn-vad | 0.4M | — | `README_zh.md:206` |
| ct-punc | 290M | — | `README_zh.md:205` |

README 的"性能评测"一节只链接历史报告并强调不同测量不能合并成通用速度排名（`README_zh.md:144-151`）。训练数据：Paraformer-large 系列标注 60000 h 阿里内部数据（`model_zoo/modelscope_models.md:27-34`）。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 2pass websocket 服务 | `runtime/websocket/bin/funasr-wss-server-2pass.cpp` | 命令行参数、模型路径、线程数、热词文件 |
| 连接与消息分发 | `runtime/websocket/bin/websocket-server-2pass.cpp:WebSocketServer::on_message` | 解析首包 JSON、收 PCM、`is_speaking:false` 触发 final |
| 解码与结果打包 | `websocket-server-2pass.cpp:WebSocketServer::do_decoder` | 调 `FunTpassInferBuffer`，组装 `2pass-online` / `2pass-offline` JSON |
| 两遍推理主流程 | `runtime/onnxruntime/src/funasrruntime.cpp:FunTpassInferBuffer` | VAD 切段 → 在线 Forward → `FetchTpass` 离线 Forward → 标点 → ITN → 时间戳 |
| 在线 VAD 切段 | `runtime/onnxruntime/src/audio.cpp:Audio::Split(VadModel*, int, bool, ASR_TYPE)` | 按 VAD 结果把音频分成在线块和离线段 |
| VAD 参数默认值 | `runtime/onnxruntime/src/e2e-vad.h` | 尾部静音 800 ms、最长段 15 s |
| 在线 Paraformer | `runtime/onnxruntime/src/paraformer-online.cpp` | 块长 600 ms，CIF 增量出字 |
| 热词编译 | `funasrruntime.cpp:CompileHotwordEmbedding` | nn 热词；fst 热词走 `FunASRWfstDecoderInit` |
| Python 流式 ASR | `funasr/models/paraformer_streaming/model.py:ParaformerStreaming.generate_chunk` | `AutoModel` 逐块推理的实现 |
| Python 流式 VAD | `funasr/models/fsmn_vad_streaming/model.py:FsmnVADStreaming` | 流式输出 `[[beg,-1]]` 开始、`[[-1,end]]` 结束（`model.py:374`）；默认尾部静音 800 ms、最长段 60000 ms |
| 动态尾部静音 | `funasr/models/fsmn_vad_streaming/dynamic_vad.py` | 已说时长越短静音阈值越长；C++ 服务端未使用（从代码推断） |
| Fun-ASR-Nano 实时服务 | `funasr/bin/realtime_ws.py:handle_client` / `RealtimeASRSession` | 文本命令 START / HOTWORDS / LANGUAGE / COMMIT，vLLM 批量解码 |
| OpenAI 兼容 HTTP | `examples/openai_api/server.py` | `/v1/audio/transcriptions`，文件级转写，非流式 |

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：内置声学 VAD 端点，不是语义判停。C++ 服务用 FSMN-VAD，默认尾部静音 800 ms（`runtime/onnxruntime/src/e2e-vad.h`），段结束即触发离线定稿；Python 侧另有按已说时长调阈值的 `fsmn_vad_streaming/dynamic_vad.py`。注意 2pass 下一个用户回合可能被切成多个 `2pass-offline` 段，上层要自己拼。
- [打断与截断](../../03-mechanisms/interruption.md)：不涉及。VAD 的段开始（`slice_type:0`）可以被上层拿来当 barge-in 信号，但仓库没有这层逻辑。
- [首音优化](../../03-mechanisms/first-audio.md)：不涉及（属于 TTS 侧）。可间接相关的是 partial 能让上层提前预取 LLM，仓库不提供。
- [工具回合](../../03-mechanisms/tool-calls.md)：不涉及。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：不涉及。在线状态挂在 websocket 连接上（`websocket-server-2pass.cpp:on_open` 创建、`on_close` 释放），断线重连不续接。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：只做重采样和特征提取（`audio.cpp:Audio::WavResample`、fbank + LFR + CMVN），没有 AEC、降噪、AGC；VAD 前不做增强。
- [评测](../../03-mechanisms/evaluation.md)：有离线 RTF / CER 报告（`runtime/docs/benchmark_onnx_cpp.md`、`docs/benchmark/historical_asr_zh.md`）和计时口径说明（`docs/benchmark/rtf_reproducibility.md`）；`docs/benchmark/realtime_ws_benchmark.md` 专门测 Fun-ASR-Nano 实时服务的首次更新延迟、STOP 后定稿延迟和多客户端行为，这是 voice agent 最关心的口径。2pass C++ 服务没有对应的延迟基准（待确认）。

## 取舍与局限

- **partial 是"便宜那一遍"**：没有标点、没有热词，专有名词通常要等定稿才纠正。拿 partial 做抢先生成要接受它和 final 不一致。
- **VAD 切段和用户回合不是一回事**：句中停顿超过尾部静音阈值就会出多条 `2pass-offline`。按键说话场景可以用 `mode:"offline"`，整段在 `is_speaking:false` 后一次识别，代价是没有 partial。
- **定稿延迟 = 尾部静音 + 离线重识别**：800 ms 静音本身就是判停延迟的大头，调小会更容易切碎句子。
- **服务端偏重**：CPU 多路靠 decoder 线程池，没有跨路 batch；GPU 要上 Triton。端侧有 `runtime/android`、`runtime/ios` 目录和 llama.cpp GGUF 路径（`README_zh.md:133`，只覆盖 SenseVoiceSmall / Fun-ASR-Nano 这类非流式模型），端侧真流式不如 sherpa-onnx 成熟。
- **模型选择多、能力不对齐**：SenseVoice 做二遍时 C++ 侧不走 ct-punc / FST ITN，时间戳可用性待确认；Fun-ASR-Nano 准确率更高但需要 GPU + vLLM，且是重解码式伪流式。
- 不接收 Opus 等编码音频，网关要先解码成 PCM。

## 相关

- 机制页：[turn-detection](../../03-mechanisms/turn-detection.md)、[audio-preprocessing](../../03-mechanisms/audio-preprocessing.md)、[evaluation](../../03-mechanisms/evaluation.md)
- 对比页：[model-matrix](../../05-comparison/model-matrix.md)
- 同类项目：[sensevoice](sensevoice.md)、[sherpa-onnx](sherpa-onnx.md)、[fireredasr](fireredasr.md)
