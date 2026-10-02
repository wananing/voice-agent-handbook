# smart-turn

> 仓库：https://github.com/pipecat-ai/smart-turn
> 分析基于：commit `4786657`（2026-01-29，README 标 v3.2）；Pipecat 接入部分基于 pipecat `422ad13`
> 状态：草稿
> 最后更新：2026-10-01

## 定位

smart-turn 是一个**只看音频的语义判停模型**：输入用户这一轮的音频，输出"这一轮说完了"的概率。它要解决的问题是：纯 VAD 只能判断"有没有声音"，判停只能靠"静音多久"，用户在句中停顿、拖音或说"嗯……"时，VAD 判停要么太早（抢话），要么为了保险把静音阈值拉长（反应慢）。smart-turn 让 agent 在短静音（Pipecat 里是 200 ms）后就能根据韵律和句尾用词来判断要不要接话。

面向：开放麦克风（免按键）的级联或 S2S 语音 agent。最初是 Pipecat 生态的组件，现在是 Pipecat 默认的判停策略。

和同类的区别：

- 和文本判停模型（TEN Turn Detection、LiveKit 旧 `turn_detector`）相比，它不依赖 ASR 结果，所以不受识别延迟和错字影响，还能利用语调、拖音等韵律线索；代价是看不到对话上下文（只看当前这一轮的音频）。
- 模型小：int8 版 8 MB，CPU 可跑；权重、训练脚本、数据集都开放，BSD 2-clause 许可（`README.md:11`、`:25-26`）。

## 整体架构

```
16 kHz mono PCM（本轮音频，最长 8 s）
   │  不足 8 s 在前面补零，超过 8 s 只保留最后 8 s
   ▼
Whisper log-mel 特征（80 mel × 800 帧）
   ▼
Whisper-tiny 编码器（max_source_positions=400）
   ▼
注意力池化（Linear-Tanh-Linear → softmax 加权求和）
   ▼
MLP 分类头（384→256→64→1）→ sigmoid → P(complete)
   ▼
P > 0.5 → 完成；否则 → 未完成
```

- **Backbone**：`openai/whisper-tiny` 的编码器部分，解码器不用（`train.py:31`、`:63-66`、`:103-120`）。约 8M 参数（`README.md:118`）。
- **分类头**：README 写"linear classifier layer"（`README.md:118`），但 `train.py` 里实际是注意力池化加三层 MLP（`train.py:72-86`、`:111-120`）。以代码为准。
- **训练**：BCE loss，按 batch 内正负样本比例算 `pos_weight`（`train.py:122-128`，`pos_weight` 在 `:124`）；4 epoch，lr 5e-5，batch 384（`train.py:31-48`）。
- **导出**：先导出 fp32 ONNX（opset 18），再用 1024 条校准样本做静态 int8 量化，只量化 Conv / MatMul / Gemm（`train.py:194`、`:266-313`）。fp32 版 32 MB 给 GPU，int8 版 8 MB 给 CPU，README 称 fp32 版准确率高约 1%（`README.md:20-23`）。
- **推理**：纯 ONNX Runtime，单线程顺序执行（`inference.py:9-14`）。仓库自带的 `record_and_predict.py` 演示了"Silero VAD 切段 → 整段送 smart-turn"的用法。

它本身没有进程或线程模型，是一个无状态的函数：每次调用都要把整轮音频重新送进去。README 明确要求：VAD 判出静音后对整轮录音跑一次；如果模型还没跑完用户又说话了，要把新音频拼上**整轮重跑**，而不是只送新片段（`README.md:90-96`）。上一轮的音频不需要带（`README.md:98`）。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 模型定义 | `train.py:SmartTurnV3Model` | Whisper 编码器 + 注意力池化 + MLP；仓库里没有 README 提到的独立 `model.py` |
| 推理（参考实现） | `inference.py:predict_endpoint` | 截取 / 补零到 8 s → `WhisperFeatureExtractor(chunk_length=8)` → ONNX → 阈值 0.5。注意 `ONNX_MODEL_PATH` 仍指向 `smart-turn-v3.1.onnx`（`inference.py:7`） |
| 补零与截断 | `audio_utils.py:truncate_audio_to_last_n_seconds` | 保留末尾，补零在前 |
| 麦克风演示 | `record_and_predict.py:record_and_predict` | Silero VAD 512 样本一块，阈值 0.5，前补 200 ms，**静音 1000 ms 才切段**，最长 8 s |
| 训练与量化 | `train.py:do_training_run` / `do_quantization_run` | 数据集从 HF 拉取，量化用 `quantize_static` |
| 分语言 / 分语气词指标 | `train.py` 中 `on_evaluate` 的 `_process_category_metrics` | 按 `language`、`midfiller` 分组算指标（`train.py:470-506`） |
| 离线评测 | `benchmark.py:benchmark` | 准确率、混淆矩阵、按类别指标、特征提取与端到端延迟（含 P50 / P90） |
| 数据标注规范 | `docs/data_generation_contribution_guide.md` | complete / incomplete 50:50；incomplete 以语气词、连接词或"还在想"的韵律结尾；样本末尾约 200 ms 静音 |
| Pipecat 接入：分析器基类 | `pipecat/src/pipecat/audio/turn/smart_turn/base_smart_turn.py:BaseSmartTurn` | 缓存音频、静音计时、在线程池里跑模型 |
| Pipecat 接入：本地 ONNX | `pipecat/.../smart_turn/local_smart_turn_v3.py:LocalSmartTurnAnalyzerV3` | 内置 `smart-turn-v3.2-cpu.onnx`（8,679,182 字节）；非 16 kHz 时 soxr HQ 重采样；特征计算是 vendored 的 numpy 实现 `_whisper_features.py` |
| Pipecat 接入：停止策略 | `pipecat/src/pipecat/turns/user_stop/turn_analyzer_user_turn_stop_strategy.py:TurnAnalyzerUserTurnStopStrategy` | VAD 停止时调分析器，再等转写或 STT 超时 |
| Pipecat 默认策略 | `pipecat/src/pipecat/turns/user_turn_strategies.py:default_user_turn_stop_strategies` | 默认就是 `TurnAnalyzerUserTurnStopStrategy(LocalSmartTurnAnalyzerV3())` |

## 输入、输出与性能

| 项 | 值 | 来源 |
|---|---|---|
| 输入 | 16 kHz 单声道 PCM，最长 8 s，建议送整轮音频 | `README.md:90-94` |
| 特征 | Whisper log-mel，80 × 800（8 s） | `benchmark.py:18-22` |
| 输出 | 一个 sigmoid 概率 P(complete)；`prediction = 1 if P > 0.5` | `inference.py:51-57` |
| 模型大小 | CPU 版 int8 8 MB；GPU 版 fp32 32 MB | `README.md:20-23` |
| 推理延迟 | README：部分 CPU 上低至 10 ms，多数云主机 <100 ms；Pipecat Cloud 标准 1x 实例约 65 ms | `README.md:17-19`、`:80`（仓库自述，未复现） |
| 语言 | 23 种，含中文 | `README.md:15-16` |
| 训练 / 测试数据 | HF `pipecat-ai/smart-turn-data-v3.2-train` / `-test` | `README.md:144-145`、`train.py:33-34` |

训练数据来源：众包的"turn training games"、人工分类平台、以及 Liva AI、Midcentury、MundoAI 三家机构贡献的数据集（`README.md:148-180`）。各语言的样本量和分语言准确率仓库里没有给出，**中文效果待确认**。`docs/static/` 下只有一张未标注版本和语言的总体混淆矩阵。

## 在 Pipecat 里怎么接

1. 输入 transport 把每帧音频送进 `TurnAnalyzerUserTurnStopStrategy._handle_input_audio`，它调 `BaseSmartTurn.append_audio(audio, is_speech)` 累积音频（`turn_analyzer_user_turn_stop_strategy.py:203-216`）。
2. VAD（默认 `stop_secs=0.2`，`pipecat/src/pipecat/audio/vad/vad_analyzer.py:25-28`）发出 `VADUserStoppedSpeakingFrame` 时，调 `analyze_end_of_turn()`，在单线程 `ThreadPoolExecutor` 里跑模型（`base_smart_turn.py:154-168`、`turn_analyzer_user_turn_stop_strategy.py:225-243`）。
3. 送进模型的音频从"开口时刻 − (`pre_speech_ms`=500 ms + VAD `start_secs`)"开始，到当前为止，最长 8 s（`base_smart_turn.py:27-29`、`:207-236`）。
4. 判"完成"后，默认还要等**定稿转写**（`TranscriptionFrame.finalized`）或 STT P99 超时才真正结束用户回合（`wait_for_transcript=True`）。接 realtime / S2S 服务时 `LLMContextAggregatorPair` 会把它改成 `False`，判完成就立即结束，不等转写（`turn_analyzer_user_turn_stop_strategy.py:50-77`）。
5. 判"未完成"后：用户再开口，VAD 发 `VADUserStartedSpeakingFrame`，挂起的结论作废（`:218-223`）；一直不开口，`append_audio` 里的静音计时满 `stop_secs=3 s` 时强制判完成（`base_smart_turn.py:128-138`）。所以"未完成"的最长等待是 3 s。
6. 其他实现：`HttpSmartTurnAnalyzer`（远程推理）、`LocalSmartTurnAnalyzerV2`（wav2vec2，已废弃）、`LocalCoreMLSmartTurnAnalyzer`（已废弃）。

## 和纯 VAD 判停的差别

| | 纯 VAD 判停 | VAD + smart-turn |
|---|---|---|
| 判停依据 | 静音时长超过阈值 | 短静音触发，模型看韵律和句尾词决定 |
| Pipecat 对应策略 | `SpeechTimeoutUserTurnStopStrategy`，VAD 停止后再等 `user_speech_timeout=0.6 s`（`speech_timeout_user_turn_stop_strategy.py:49-53`） | `TurnAnalyzerUserTurnStopStrategy`，VAD 停止（0.2 s）后立即推理 |
| 句中停顿 / "嗯……" | 停顿超过阈值就被切 | 判"未完成"，最多再等 3 s |
| 说完后的反应速度 | 至少等满静音阈值 | 0.2 s 静音 + 推理耗时（几十 ms 量级） |
| 额外成本 | 无 | 每次 VAD 停止跑一次 8 s 窗口的推理；CPU 上要一个线程 |
| 失败模式 | 抢话或反应慢，二选一 | 模型误判"未完成"时最多多等 3 s；误判"完成"时和 VAD 一样抢话 |

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：核心功能。只看音频的二分类模型，VAD 短静音后对整轮音频推理一次，P>0.5 判完成。Pipecat 里由 `TurnAnalyzerUserTurnStopStrategy` + `LocalSmartTurnAnalyzerV3` 驱动，"未完成"靠 `BaseSmartTurn.append_audio` 的 3 s 静音兜底。
- [打断与截断](../../03-mechanisms/interruption.md)：不涉及。模型只判用户回合结束，不判用户何时开始插话；Pipecat 的打断由 user turn start 策略负责。
- [首音优化](../../03-mechanisms/first-audio.md)：间接相关。它让判停能在 200 ms 静音后完成，而不是等更长的静音阈值；代价是推理耗时（README 称 10–100 ms）进入关键路径。接 S2S 时 Pipecat 用 `wait_for_transcript=False` 把转写移出关键路径（`turn_analyzer_user_turn_stop_strategy.py:61-72`）。
- [工具回合](../../03-mechanisms/tool-calls.md)：不涉及。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：不涉及。模型无状态，每次推理只看当前一轮。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：不涉及前处理本身。数据规范要求"尽量少背景噪音"（`docs/data_generation_contribution_guide.md:43`），`train.py` 里也没有找到加噪或增强逻辑，在嘈杂环境下的鲁棒性待确认。
- [评测](../../03-mechanisms/evaluation.md)：有离线评测。`benchmark.py` 在 HF 测试集上算准确率、混淆矩阵、分语言和分语气词指标，以及特征提取和端到端延迟的 P50 / P90；训练时 `on_evaluate` 也按语言分组记录。没有端到端对话级评测。

## 取舍与局限

- **只看当前一轮音频**：看不到 agent 上一句问了什么，"是 / 不是"这类短回答只能靠韵律判断。README 的中期目标里提到要加"文本条件"来支持报卡号、电话号码等模式（`README.md:114`、`:163`），目前没有。
- **阈值写死 0.5**：`inference.py` 和 Pipecat 的 `LocalSmartTurnAnalyzerV3._predict_endpoint` 都硬编码 0.5，想调阈值要自己包一层。
- **每次都要整轮重算**：用户在一轮里多次停顿就会多次推理，每次都是 8 s 窗口；不支持流式增量。
- **"未完成"的兜底是静音时长**：Pipecat 默认 3 s，这个值决定了误判"未完成"时的最坏延迟。
- **文档与代码不一致**：README 说分类头是线性层、让用户取 `model.py`，代码里是注意力池化 + MLP、仓库里没有 `model.py`；`inference.py` 默认加载 v3.1 的文件名，Pipecat 内置的是 v3.2。
- **中文、儿童、噪声场景的效果仓库里没有数据**，要自己用 `benchmark.py` 在目标数据上测。训练脚本和数据格式都开放，可以微调。
- 演示脚本 `record_and_predict.py` 用的是 1000 ms 静音才切段，和"200 ms 后就跑"的推荐用法不同，不要直接照搬它的参数。

## 相关

- 机制页：[判停](../../03-mechanisms/turn-detection.md)、[首音优化](../../03-mechanisms/first-audio.md)、[评测](../../03-mechanisms/evaluation.md)
- 项目页：[pipecat](../frameworks/pipecat.md)、[ten-vad](ten-vad.md)、[livekit-agents](../frameworks/livekit-agents.md)
- 对比页：待补
