# FireRedASR

> 仓库：https://github.com/FireRedTeam/FireRedASR
> 许可：代码 Apache-2.0；权重待确认（见仓库 LICENSE 文件）
> 分析基于：commit 834635e
> 状态：草稿
> 最后更新：2026-10-01

## 定位

FireRedTeam 开源的工业级中文 ASR 模型族，主打公开普通话 benchmark 上的准确率（`README.md:15-17`）。两个变体：

- **FireRedASR-AED**（1.1B）：Conformer 编码器 + Transformer 解码器（attention-based encoder-decoder），兼顾精度和效率；
- **FireRedASR-LLM**（8.3B）：Encoder-Adapter-LLM 结构，LLM 是 Qwen2-7B-Instruct（`README.md:30-31`、`:63`）。

在 voice agent 里它的定位是**离线高精度识别 / 准确率参照**，不是实时链路组件：整句输入、非流式，没有 VAD、热词、时间戳、标点、ITN，推理接口按文件路径批量处理。仓库首页已指向后继项目 FireRedASR2S（ASR + VAD + LID + Punc 一体，`README.md:13-15`），它不在本次分析范围内，能力和数字待确认。

和同类比：FunASR / sherpa-onnx 是"带服务端或运行时的工具链"，SenseVoice 是"小而快的整句模型"，FireRedASR 是"大而准的整句模型"，仓库只给模型和最小推理脚本。

## 整体架构

```
wav 文件路径 ─▶ kaldiio 读 16k PCM ─▶ 80 维 fbank（25 ms / 10 ms）+ CMVN
           ─▶ Conformer encoder（Conv2d 4 倍下采样，全上下文自注意力）
           ├─ AED：Transformer decoder beam search ─▶ 1-best token ─▶ detokenize
           └─ LLM：Adapter（拼帧 + 2 层 Linear）─▶ Qwen2-7B-Instruct.generate
                   prompt 固定为 "<speech>请转写音频为文字"
```

统一入口是 `FireRedAsr.from_pretrained(asr_type, model_dir)` + `transcribe(batch_uttid, batch_wav_path, args)`（`fireredasr/models/fireredasr.py:13-107`）。特征提取 `ASRFeatExtractor.__call__` 直接用 `kaldiio.load_mat(wav_path)` 读文件（`fireredasr/data/asr_feat.py:16-30`），所以接入实时链路时要先把 VAD 切好的音频段落盘，或者自己改成接收内存数组。

编码器自注意力只用 padding mask，没有 chunk mask（`fireredasr/models/module/conformer_encoder.py:24-43`），这是"非流式"的结构性原因，不是缺一个接口。

部署形态：

| 形态 | 入口 | 说明 |
|---|---|---|
| Python CLI | `fireredasr/speech2text.py`：`--wav_path` / `--wav_paths` / `--wav_dir` / `--wav_scp`，`--batch_size` | 批量离线转写 |
| Python API | `fireredasr/models/fireredasr.py:FireRedAsr` | 见 README Python Usage |
| Triton + TensorRT（仅 AED） | `runtime/triton_tensorrt/`：docker compose，HTTP `/v2/models/fireredasr/infer`；`config.pbtxt` 开 `dynamic_batching`、`max_batch_size: 64` | 离线吞吐服务，非流式 |
| ONNX / 端侧 | 本仓库没有；sherpa-onnx 有 `offline-fire-red-asr`（AED encoder / decoder ONNX）和 `offline-fire-red-asr-ctc` 两套离线支持 | 对应哪个 checkpoint 版本待确认 |

### voice agent 视角的能力清单

| 维度 | 情况 | 出处 |
|---|---|---|
| 流式 | 不支持。整句输入，无 chunk 接口 | `conformer_encoder.py:24-43` |
| partial / final | 只有 final（整句 1-best） | `fireredasr.py:68-75`、`:99-105` |
| 内置 VAD / 端点 | 无，需外接 | — |
| 热词 | 无；LLM prompt 固定，无 context 输入 | `fireredasr/tokenizer/llm_tokenizer.py:50` |
| 时间戳 | 无，只返回 `uttid / text / wav / rtf` | `fireredasr.py:68-75` |
| 标点 | 无。AED 训练文本把 `，。？！` 换成空格，LLM 训练文本清洗时去掉标点 | `aed_tokenizer.py:38-39`、`llm_tokenizer.py:clean_text` |
| ITN | 无。评测脚本可选 `cn2an` 做简单数字归一（`--do_tn`） | `fireredasr/utils/wer.py:12`、`:50-52` |
| 语言 | 普通话、中文方言、英语；分词是"中文单字 + 英文 SPM"，结构上支持中英混说，混说数字仓库没有 | `README.md:17`；`aed_tokenizer.py` |
| 输入长度 | AED ≤ 60 s（超过可能幻觉，超过 200 s 位置编码报错）；LLM ≤ 30 s | `README.md:147-149` |
| 输入格式 | 16 kHz 16-bit PCM wav，README 要求先 ffmpeg 转换 | `README.md:81-84` |
| 解码参数 | AED：beam 3、softmax_smoothing 1.25、length_penalty 0.6；LLM：beam 3、repetition_penalty 3.0 | `README.md` Python Usage |

### 中文效果定位

README 的公开普通话 benchmark（CER %，`README.md:40-49`）：

| 模型 | 参数量 | aishell1 | aishell2 | ws_net | ws_meeting | Average-4 |
|---|---|---|---|---|---|---|
| FireRedASR-LLM | 8.3B | 0.76 | 2.15 | 4.60 | 4.67 | 3.05 |
| FireRedASR-AED | 1.1B | 0.55 | 2.52 | 4.88 | 4.76 | 3.18 |
| SenseVoice-L | 1.6B | 2.09 | 3.04 | 6.01 | 6.73 | 4.47 |
| Paraformer-Large | 0.2B | 1.68 | 2.85 | 6.74 | 6.97 | 4.56 |

方言 / 英文（`README.md:53-57`）：KeSpeech LLM 3.56 / AED 4.48；LibriSpeech test-clean 1.73 / 1.93，test-other 3.67 / 4.44。

注意：这是 FireRed 自己跑的对比表；SenseVoice-L 不是开源的 SenseVoiceSmall。速度方面，README 正文没有 RTF；`runtime/triton_tensorrt/README.md` 给出单卡 H20 解 AISHELL-1 测试集（10 小时）PyTorch 760 s、TensorRT 60 s（仅 AED，批量离线口径）。LLM 版的速度和显存仓库没给，待确认。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 统一推理入口 | `fireredasr/models/fireredasr.py:FireRedAsr.transcribe` | 特征提取 → AED 或 LLM 解码 → 1-best 文本 + rtf |
| 模型加载 | `fireredasr.py:FireRedAsr.from_pretrained`、`load_fireredasr_aed_model`、`load_firered_llm_model_and_tokenizer` | LLM 需另下 Qwen2-7B-Instruct 并软链 |
| 特征提取 | `fireredasr/data/asr_feat.py:ASRFeatExtractor` | 按文件路径读取，fbank + CMVN |
| AED 模型 | `fireredasr/models/fireredasr_aed.py:FireRedAsrAed.transcribe` | Conformer encoder + Transformer decoder beam search |
| LLM 模型 | `fireredasr/models/fireredasr_llm.py:FireRedAsrLlm.transcribe` | Adapter 后接 HF `generate` |
| 编码器 | `fireredasr/models/module/conformer_encoder.py:ConformerEncoder` | 全上下文，无 chunk mask |
| CLI | `fireredasr/speech2text.py:main` | 按 `--batch_size` 分批 |
| 评测 | `fireredasr/utils/wer.py` | CER / WER，可选 `cn2an` 数字归一 |
| Triton 服务 | `runtime/triton_tensorrt/model_repo_fireredasr_aed/fireredasr/1/model.py` | TensorRT encoder + 解码，动态 batch |

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：不涉及。没有 VAD 或端点，需要上游先切好整句（FireRedASR2S 带 FireRedVAD，未分析，待确认）。
- [打断与截断](../../03-mechanisms/interruption.md)：不涉及。
- [首音优化](../../03-mechanisms/first-audio.md)：不涉及。
- [工具回合](../../03-mechanisms/tool-calls.md)：不涉及。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：不涉及。LLM 版的 prompt 固定，不接收对话上下文。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：只做 fbank + CMVN（`asr_feat.py:ASRFeatExtractor`），不做重采样（README 要求先用 ffmpeg 转 16 kHz），没有降噪 / AEC。
- [评测](../../03-mechanisms/evaluation.md)：`fireredasr/utils/wer.py` 是可复用的 CER / WER 打分脚本（支持 `--do_tn` 数字归一、`--rm_special`）；`transcribe` 每批输出 rtf；README 给出公开集 CER 表。适合当 ASR 选型时的"准确率上限"尺子。

## 取舍与局限

- **非流式、无端点**：只能放在"VAD 切段之后"的位置，定稿延迟 = 判停延迟 + 整句解码；LLM 版是自回归 7B 解码，延迟和显存成本都高。
- **没有热词、时间戳、标点、ITN**：接进对话链路要自己补标点 / ITN，也无法做字级对齐；文本输出大概率是汉字数字（待确认，比较时要统一 TN）。
- **接口是文件路径**：实时链路要改造 `ASRFeatExtractor` 或落盘。
- **服务化只有 AED 的 Triton**，LLM 版无官方服务端。
- 优势是公开普通话集上 CER 最低一档，适合离线标注、准确率对照、二遍重识别的候选。

## 相关

- 机制页：[evaluation](../../03-mechanisms/evaluation.md)、[turn-detection](../../03-mechanisms/turn-detection.md)
- 对比页：[model-matrix](../../05-comparison/model-matrix.md)
- 同类项目：[funasr](funasr.md)、[sensevoice](sensevoice.md)、[sherpa-onnx](sherpa-onnx.md)
