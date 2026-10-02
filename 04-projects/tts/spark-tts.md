# Spark-TTS

> 仓库：https://github.com/SparkAudio/Spark-TTS
> 分析基于：commit 2f1ea90
> 状态：草稿
> 最后更新：2026-10-01

## 定位

HKUST、出门问问等机构联合发布的 0.5B 中英双语 TTS（Spark-TTS-0.5B）。卖点是"极简"：整个生成模型就是一个 Qwen2.5 LLM，直接预测 BiCodec 的离散 token，**没有 flow matching**，codec 直接从 token 重建波形（`README.md:42`）。BiCodec 把语音拆成两类 token：表示音色的定长 global token 和 50 Hz 的 semantic token。

和同类项目的区别：结构最简单，参数量小；除了零样本克隆，还有不需要参考音频的"音色创建"模式，可以用性别、音高、语速参数造新音色（`README.md:45`）。代价是输出只有 16 kHz；本地推理没有流式；项目在本 commit 之后没有更新，README 里的训练代码和数据集至今没有放出（`README.md:308-312`）。

在 voice agent 里，它更适合用来研究 "纯 LLM + codec" 路线，或作为 Triton 流式部署的参考实现；当产品 TTS 要先过许可和音质两关。

## 整体架构

```
prompt_wav ─► BiCodecTokenizer.tokenize ─► global tokens（取参考中 ref_segment_duration 秒，不够就循环补齐）
                                          + semantic tokens（只在给了 prompt_text 时放进 prompt）
text（无 TN）─┐
              ▼
   "<|task_tts|><|start_content|>[prompt_text]text<|end_content|><|start_global_token|>…"
              ▼
   Qwen2.5-0.5B（HF generate, max_new_tokens=3000）─► "bicodec_semantic_N" 文本 token
              ▼  正则解析成 id
   BiCodec.detokenize(global, semantic) ─► 16 kHz wav（整段）
```

**模型结构**：单个 Qwen2.5 LLM 自回归预测 BiCodec semantic token，BiCodec 解码器直接重建 16 kHz 波形（`cli/SparkTTS.py:SparkTTS.inference`，`sparktts/models/bicodec.py:BiCodec.detokenize`）。输出 16 kHz（`cli/inference.py:105`）。属于 "LLM + codec" 一类，没有 flow matching。

**进程 / 线程模型**：本地 CLI / WebUI 是单进程同步调用：一次 `model.generate` 生成全部 token，再整段 detokenize，不 yield 中间结果（`cli/SparkTTS.py:194-234`）。Triton 部署时拆成 `audio_tokenizer`、`tensorrt_llm`、`vocoder` 和编排它们的 `spark_tts`（BLS）四个模型（`runtime/triton_trtllm/model_repo/`）。

**流式能力**：

- 音频流式输出（本地）：不支持，整段出。
- 音频流式输出（Triton + TensorRT-LLM）：支持 decoupled 流式。`spark_tts` BLS 模型按 50 Hz semantic token 分块：首块 `audio_chunk_duration=1.0` s（50 个 token），之后按 `audio_chunk_size_scale_factor=8` 放大，上限 30 s，块间重叠 0.1 s（`runtime/triton_trtllm/run.sh:52-56`，`model_repo/spark_tts/1/model.py:345-380`，帧率 `model_repo/spark_tts/config.pbtxt:46-47`）。代码里断言首块至少 0.5 s（`model.py:121-123`）。
- 文本流式输入：不支持，整句文本在 prompt 里一次给全。
- 首包量级：Triton 流式，L20、26 条样本，首块 P50 并发 1 为 210 ms，并发 2 为 226 ms，并发 4 为 1018 ms（`runtime/triton_trtllm/README.md:92-94`）。口径是客户端发请求到收到第一块（`runtime/triton_trtllm/client_grpc.py`）。注意表中流式那几行的 "Code Commit" 链接指向第三方 fork。本地路径没有首包数字，首包等于整句合成时间。

**音色**：

- 零样本克隆：参考音频 + 可选的参考文本（`cli/SparkTTS.py:process_prompt`）。音色由参考中截出的固定长度片段生成 global token（`sparktts/models/audio_tokenizer.py:get_ref_clip`）；时长由模型配置 `ref_segment_duration` 决定，具体取值在权重配置里，仓库代码里没有写死，待确认。给了参考文本时，参考的 semantic token 也会放进 prompt，变成续写式克隆。
- 音色创建：`gender`（female / male）、`pitch`、`speed`（各 5 档）只在**不克隆**的模式下可用（`cli/SparkTTS.py:process_prompt_control`，`inference` 里的 `if gender is not None` 分支）。克隆模式下不能调语速。
- token 表里有年龄 token（含 `Child`），但推理没有接上（`sparktts/utils/token_parser.py:35`）。
- 跨语言 / 中英混读：README 称支持跨语言和 code-switching 的零样本克隆（`README.md:43-44`），效果待确认。
- 预置音色：没有。

**中文**：

- 没有文本前端：文本直接拼进 prompt 送 tokenizer（`cli/SparkTTS.py:process_prompt`），没有 TN、多音字处理或拼音标注。数字、单位、符号全靠模型自己读，上游必须自己做 TN。

**推理部署**：

- 本地：`cli/inference.py`（CLI）、`webui.py`（Gradio）。
- 加速：`runtime/triton_trtllm/`，Triton + TensorRT-LLM，`TRITON_MAX_BATCH_SIZE=16`、`BLS_INSTANCE_NUM=4`（`run.sh:50-51`），gRPC / HTTP 客户端都有（`client_grpc.py`、`client_http.py`）。没有 vLLM 路径。
- 显存：README 没有给数字，待确认。
- 采样：`do_sample=True`，默认 temperature 0.8、top_k 50、top_p 0.95（`cli/SparkTTS.py:SparkTTS.inference`），每次合成都有随机性。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 本地推理 | `cli/SparkTTS.py:SparkTTS.inference` | 一次 `generate`，整段 detokenize |
| 克隆 prompt 构造 | `cli/SparkTTS.py:SparkTTS.process_prompt` | global token + 可选参考文本 / semantic token |
| 音色创建 prompt | `cli/SparkTTS.py:SparkTTS.process_prompt_control` | 性别 / 音高 / 语速，仅非克隆模式 |
| 参考音频编码 | `sparktts/models/audio_tokenizer.py:BiCodecTokenizer.tokenize` | 截取固定长度片段，提 wav2vec2 特征 |
| 波形重建 | `sparktts/models/bicodec.py:BiCodec.detokenize` | global + semantic → 16 kHz |
| Triton 编排 / 流式 | `runtime/triton_trtllm/model_repo/spark_tts/1/model.py` | BLS，decoupled 时按块发送 |
| 部署脚本 | `runtime/triton_trtllm/run.sh` | 构建 TRT-LLM 引擎、填模板、起服务、跑 benchmark |
| 压测客户端 | `runtime/triton_trtllm/client_grpc.py` | 记录首块延迟和 RTF |

## 对各机制的回答

- 判停：不涉及。
- 打断与截断：本地路径是一次性 `generate`，生成过程中无法中止（`cli/SparkTTS.py:SparkTTS.inference`）。Triton decoupled 流式能否在客户端取消时停止生成，待确认。没有时间戳。
- 首音优化：本地不涉及（整段出）；Triton 流式首块 1.0 s 音频、之后指数放大块长，并发 1 时自述首块 P50 210 ms（`runtime/triton_trtllm/model_repo/spark_tts/1/model.py`，`runtime/triton_trtllm/README.md:92`）。
- 工具回合：不涉及。
- 会话恢复与上下文同步：不涉及。
- 音频前处理：不涉及（参考音频只做截取和特征提取）。
- 评测：README 和仓库里没有质量评测表；`runtime/triton_trtllm/client_grpc.py` 可以在 Seed-TTS 数据集上测首块延迟和 RTF。第三方评测见 CosyVoice / VoxCPM 的 README（Seed-TTS test-zh CER 1.2 / SS 66.0）。

## 取舍与局限

- **简单 vs 质量**：没有 flow matching，结构简单、易于用 TRT-LLM 加速；但 16 kHz 输出，第三方评测里中文说话人相似度明显低于同代模型（CosyVoice README：SS 66.0，CosyVoice3 为 78.0）。
- **可控性分裂**：音色创建模式可控（性别 / 音高 / 语速），克隆模式不可控。
- **流式只在 Triton 路径**：本地无流式；Triton 首块固定 1 s 音频，并发 4 时首块 P50 跳到约 1 s。
- **短语级调用的风险**：每次调用都重新从参考生成 global token，并独立采样。分段调用时，段与段之间音色是否一致待确认。
- **维护状态和许可**：本 commit 之后没有更新；代码是 Apache-2.0（`LICENSE`），权重许可仓库里没写，待确认（此前调研笔记记录的 HF 元数据为 `cc-by-nc-sa-4.0`，即非商用，以 HF 仓库的 LICENSE 文件为准）。
- **适合的链路**：级联链路的 TTS 节点，配合 Triton 流式部署；更适合研究和原型，不建议直接做中文产品的主 TTS。
- **如果要接入**（推断，未实测）：
  1. 走 Triton 流式部署，按并发 1–2 规划容量；
  2. 参考音频固定，并在服务侧缓存 global token，避免每次重算；
  3. 上游负责 TN 和短语切分；
  4. 下游按 16 kHz 处理，或者自己上采样。

## 相关

- 机制页：[首音优化](../../03-mechanisms/first-audio.md)、[打断与截断](../../03-mechanisms/interruption.md)、[评测](../../03-mechanisms/evaluation.md)
- 对比页：[模型对比矩阵](../../05-comparison/model-matrix.md)
