# fish-speech

> 仓库：https://github.com/fishaudio/fish-speech
> 分析基于：commit 214da3c
> 状态：草稿
> 最后更新：2026-10-01

## 定位

Fish Audio 开源的多语种 TTS，当前模型是 **Fish Audio S2 Pro**（4B Slow AR + 400M Fast AR）。主打表现力：用 `[whisper]`、`[excited]` 这类自由文本标签在句内控制情感和韵律，原生支持多说话人、多轮对话生成，覆盖 80+ 种语言，中文属于 Tier 1（`README.md:76-145`）。

和同类项目的区别：音质和表现力定位最高，README 自述 Seed-TTS Eval 中文 WER 0.54%（`README.md:96`）。但**本仓库没有句内流式**，低延迟数字来自外部的 SGLang-Omni。许可是 Fish Audio Research License，**商用必须另签授权**（`LICENSE` 第 III 节）。

在 voice agent 里，它更适合做音质参照、离线提示语生成或研究用途。要上实时链路，得走 SGLang-Omni / vLLM-Omni 并另行评估。

## 整体架构

```
ServeTTSRequest(text, references / reference_id, streaming)
      │
      ▼
TTSInferenceEngine.inference ──► 参考音频 → DAC codec 编码（可内存缓存）
      │  input_queue
      ▼
[LLM worker 线程，max_batch_size=1，串行]
  generate_long: 按 <|speaker:N|> 分 turn → 按字节数分 batch
  每个 batch: Slow AR(4B, 时间轴, 主码本) + Fast AR(400M, 其余 9 个码本) 一次生成完
      │  response_queue（每个 batch 一个 GenerateResponse）
      ▼
VQManager.decode_vq_tokens ──► modded DAC 解码 ──► 44.1 kHz PCM / WAV
```

**模型结构**：Dual-AR 解码器 + RVQ 音频 codec（10 个码本，约 21 Hz）。Slow AR 沿时间轴预测主语义码本，Fast AR 在每一步补齐剩余 9 个残差码本（`README.md:118-123`）。codec 是 modded DAC，采样率 44.1 kHz（`fish_speech/configs/modded_dac_vq.yaml:3`）。整体是 "LLM + codec"，没有 flow matching。

**进程 / 线程模型**：`launch_thread_safe_queue`（`fish_speech/models/text2semantic/inference.py:770`）起一个 worker 线程，加载模型时用 `setup_caches(max_batch_size=1)`，从队列里逐个取请求串行处理。HTTP 层的 `--workers N` 会让每个 uvicorn 进程各加载一份完整模型（`tools/api_server.py` 注释）。按 4B 的体量，一张卡基本只能跑一个进程（推断）。

**流式能力**：

- 音频流式输出：**名义支持，实际按批次整段出**。`streaming=True` 时服务端先发 WAV 头，然后每收到一个 batch 的结果，就解码并发出一段（`fish_speech/inference_engine/__init__.py:73-116`）。batch 由 `generate_long` 划分：文本里有 `<|speaker:N|>` 标签时按 turn 和 `chunk_length`（默认 200 字节）分组；**没有标签时整段文本就是一个 batch**（`inference.py:622-628`）。每个 batch 在 `generate()` 里一次生成完，codec 也一次解码完。所以在本仓库里，首包时间等于整段合成时间。
- 文本流式输入：不支持。
- 首包量级：README 写 H200 上 TTFA 约 100 ms、RTF 0.195（`README.md:132-139`），但这是 **SGLang-Omni** 的数字，推理代码不在本仓库。本仓库路径的首包没有给数字，待确认。
- 流式只支持 WAV 格式（`tools/server/views.py:165-170`）。

**音色**：

- 零样本克隆：参考音频 "typically 10-30 seconds"（`README.md:161`），需要参考文本（请求里的 `references[].text`，或 `reference_id` 目录下的 `.lab` 文件）。
- 预置音色：没有内置音色；可以通过 `/v1/references/add` 把参考音频注册成 `reference_id`（`tools/server/views.py:208`），并用 `use_memory_cache="on"` 缓存编码结果（`fish_speech/inference_engine/reference_loader.py:load_by_id` / `load_by_hash`）。
- 跨语言：80+ 种语言，不需要音素或语言特定的预处理（`README.md:143-145`）。
- 多说话人：一段参考里可以包含多个说话人，用 `<|speaker:i|>` 区分（`README.md:153`）。
- 表现力控制：15000+ 种自由文本标签，可以插在句中任意位置（`README.md:107-116`）。

**中文**：

- **推理路径上没有文本前端**。`ServeTTSRequest.normalize` 字段注释写"为数字增加稳定性"（`fish_speech/utils/schema.py:96-97`），但在推理和服务代码里没有任何引用；`clean_text` 只在训练数据集里用（`fish_speech/datasets/semantic.py:263`）。数字、单位、中英混读全靠模型自己读。
- 多音字：仓库里没有拼音纠音机制，待确认。

**推理部署**：

- 本仓库：`tools/api_server.py`（Kui ASGI + uvicorn），`POST /v1/tts`，另有 `/v1/references/*` 管理参考音色；`tools/run_webui.py` 是 WebUI。启动时会预热（`tools/server/model_manager.py:warm_up`），可开 `--compile`。
- 加速：本仓库没有 vLLM / TensorRT 路径。README 指向外部的 SGLang-Omni 和 vLLM-Omni（`README.md:64-67`），代码不在本仓库，能力待确认。
- 显存：文档建议至少 24 GB（`docs/zh/inference.md:3`）。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| HTTP 服务 | `tools/api_server.py`、`tools/server/views.py` 的 `/v1/tts` | 流式只支持 WAV |
| 请求定义 | `fish_speech/utils/schema.py:ServeTTSRequest` | `chunk_length`、`references`、`reference_id`、`seed`、`streaming`；`normalize` 未生效 |
| 推理编排 | `fish_speech/inference_engine/__init__.py:TTSInferenceEngine.inference` | 发请求到 LLM 队列，按 batch 取结果、解码、yield |
| LLM worker | `fish_speech/models/text2semantic/inference.py:launch_thread_safe_queue` | 单线程，`max_batch_size=1` |
| 分批与生成 | `fish_speech/models/text2semantic/inference.py:generate_long` | 按 speaker 标签和字节数分 batch，上一 batch 的码作为下一 batch 的上下文 |
| 参考音色 | `fish_speech/inference_engine/reference_loader.py:ReferenceLoader` | `reference_id` 目录、哈希缓存 |
| codec 解码 | `fish_speech/inference_engine/vq_manager.py:VQManager.decode_vq_tokens` | 整段解码 |
| 模型加载 / 预热 | `tools/server/model_manager.py:ModelManager` | 可选 `torch.compile` |

## 对各机制的回答

- 判停：不涉及。
- 打断与截断：仓库没有取消接口。请求进入 LLM worker 队列后会一直生成到结束，客户端断开不会中止 worker 里正在跑的 `generate_long`（推断，`fish_speech/models/text2semantic/inference.py:launch_thread_safe_queue`）。没有时间戳。
- 首音优化：本仓库只有按 batch 出音频，无标签时首包等于整段合成时间；低首包要靠外部 SGLang-Omni（README 自述 TTFA ~100 ms，H200），待确认。
- 工具回合：不涉及。
- 会话恢复与上下文同步：同一请求内，上一 batch 生成的码会作为上下文带进下一 batch，韵律连贯（`generate_long`）；跨请求不保留状态。多轮对话生成是模型能力，但本仓库服务端没有跨请求的会话接口。
- 音频前处理：不涉及。
- 评测：README 给出 Seed-TTS Eval、Audio Turing Test、EmergentTTS-Eval、多语种 MiniMax 测试集等结果（`README.md:90-105`）；仓库内没有评测脚本，待确认。

## 取舍与局限

- **质量高，延迟和部署成本也高**：4B + 400M、≥ 24 GB 显存、本仓库单 worker 串行，没有句内流式。实时 voice agent 链路里不能直接用本仓库服务。
- **可控性强**：句内自由文本情感标签、多说话人、多轮上下文，适合需要强表现力的角色。LoRA 微调文档提醒，默认配置下 LoRA 基本只学到发音方式，学不到音色（`docs/zh/finetune.md`）。
- **中文前端缺位**：没有 TN，数字、单位、缩写的读法要在上游自己处理。
- **许可**：Fish Audio Research License 只允许研究和非商业用途，商用（包括通过托管服务或 API 提供）需要另签书面授权。
- **适合的链路**：作为级联链路里的音质上限参照或离线合成；实时用途需要另评估 SGLang-Omni / vLLM-Omni 和商用授权。
- **如果仍要用本仓库做实时**（推断，未实测）：
  1. 上游按短语切分，每个短语一次请求，让"整段合成时间"足够短；
  2. 预先注册 `reference_id` 并开 `use_memory_cache`，省掉每次的参考编码；
  3. 在上游做 TN；
  4. 接受单卡串行带来的并发上限。

## 相关

- 机制页：[首音优化](../../03-mechanisms/first-audio.md)、[打断与截断](../../03-mechanisms/interruption.md)、[会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)、[评测](../../03-mechanisms/evaluation.md)
- 对比页：[模型对比矩阵](../../05-comparison/model-matrix.md)
