# IndexTTS

> 仓库：https://github.com/index-tts/index-tts
> 分析基于：commit ee40fa7
> 状态：草稿
> 最后更新：2026-10-01

## 定位

B 站 Index 团队的零样本 TTS。当前版本是 **IndexTTS-2.5**（0.8B），仓库同时保留 IndexTTS-2（1.5B）和更早的版本。README 自称 "Industrial-Level Controllable and Efficient Zero-Shot TTS"，主打可控性：**音色和情感分离**（情感可以来自另一段参考音频、8 维情感向量或文本推断），`duration_factor` 调语速，拼音 / CMU 音素 / 日文假名纠音（`README.md:17-45`）。2.5 支持中、英、日、西、阿 5 种语言。

和同类项目的区别：情感表现力和发音可控性是长处。实时性是短板：官方 PyTorch 路径没有句内流式；真正的分块流式只在从第三方移植来的 TensorRT 后端里，而且只支持 2.0 权重。许可是 bilibili Model Use License，不是 Apache。

在 voice agent 里，它适合对表现力要求高、能接受短语级首包的场景，或者用来离线生成带情感的提示语。

## 整体架构

```
text ──► TextNormalizer(WeTextProcessing / wetext) ─► TextTokenizer ─► 按 120 token 切段
spk_audio_prompt(≤15 s) ─► w2v-BERT 特征 + 说话人条件（按路径缓存）
emo: emo_audio_prompt | emo_vector[8] | emo_text(QwenEmotion)
              │
              ▼  每段文本
   GPT 自回归 ─► semantic codes ─► semantic_codec.decode
              ─► length_regulator(× duration_factor) ─► CFM flow matching(25 步) ─► mel
              ─► BigVGAN ─► 22.05 kHz wav ──（stream_return 时按段 yield）
```

**模型结构**：GPT 自回归生成语义 code，再由 s2mel 模块（length regulator + CFM flow matching，默认 `diffusion_steps=25`、`inference_cfg_rate=0.7`）生成 mel，最后用 BigVGAN 出波形（`indextts/infer_v2_5.py:826-866`）。输出 22.05 kHz（`infer_v2_5.py:741`）。属于 "LLM + flow matching" 一类。

**进程 / 线程模型**：本地是单进程、单请求的同步 generator，没有后台线程。说话人条件按参考音频路径缓存在实例上（`infer_v2_5.py:620-630`，参考变了才重算），所以同一实例不适合并发服务多个音色。

**流式能力**：

- 音频流式输出（PyTorch 路径）：`stream_return=True` 只是**按文本段整段出**。每段要完整跑完 GPT（默认 `num_beams=3`、`do_sample=True`）、25 步 CFM 和 BigVGAN 才 yield 一次（`infer_v2_5.py:732-864`）。2.5 只在文本超过 `max_text_tokens_per_segment=120` 时才切段（`split_text_by_tokens`，`infer_v2_5.py:427`），所以短语级调用时首包就是整句合成时间。IndexTTS-2 有 `quick_streaming_tokens` 让首段单独成段（`indextts/infer_v2.py:400,523`），2.5 的切段不看这个参数。
- 音频流式输出（TRT 后端）：`backends/trt/` 支持真正的分块流式。GPT 每生成 `chunk_size=100` 个 code 解码一块，块间重叠 5 个 code 并做交叉淡化（`backends/trt/pipeline/streaming.py:57-68`，服务默认值 `backends/trt/serving/triton_server.py:324-325`）。2.0 的 code 率约 50 个/秒，所以首块要先生成约 2 s 音频的 code（推断）。该后端 README 只写 "low time-to-first-audio"，**没有给首包数字**；只验证过 RTX 4090 + `MAX_BATCH_SIZE=1`，2.5 "has no TensorRT engines yet"（`backends/trt/README.md:22,49-79`）。
- 文本流式输入：不支持。
- 首包量级：仓库没有首包数字，待确认。只能按 RTF 粗算：RTX 4090 上 2.5 bf16 的 RTF，7 字为 0.29，16 字为 0.22（`README.md:474-483`）；7 字约 1.5 s 音频时，首包约 0.4 s（推断）。TRT fp16 的 RTF 约 0.14（`backends/trt/README.md:69-75`）。

**音色**：

- 零样本克隆：单段参考音频，截断到 15 s（`_load_and_cut_audio(spk_audio_prompt, 15)`，`infer_v2_5.py:627`），不需要参考文本。
- 情感：①情感参考音频 + `emo_alpha`；②8 维情感向量 `[happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]`；③`use_emo_text`，用 QwenEmotion 从文本推情感，2.5 要在构造时开 `use_qwen_emo=True`（`README.md:254-336`，`infer_v2_5.py:79-137`）。README 提醒 `use_random=True` 会降低克隆相似度。
- 预置音色：没有；`examples/` 里只有示例参考音频。
- 跨语言：README 有 CV3-Eval 跨语言表，中文参考可以合成英 / 西 / 日 / 阿语（`README.md` Table 2）。
- 时长：IndexTTS-2 论文里的精确时长控制在本版本**没有开放**（`README.md:43`）；2.5 只有 `duration_factor`（0.5–2.0），整体缩放 length regulator 的目标长度（`infer_v2_5.py:832`）。

**中文**：

- 有文本前端，默认开启：Linux 用 WeTextProcessing，其他平台用 wetext（`indextts/utils/front.py:TextNormalizer.load`，`pyproject.toml:65-67`）。
- 多音字：2.5 支持 `<行|XING2>` 形式的拼音标注（`README.md:356-370`），2.0 支持字和拼音混写（`README.md:372-380`）。前端在 TN 时会保护这些标注（`front.py:_protect_pronunciation_annotations`）。
- 术语 / 人名：`TextNormalizer` 有 glossary、人名、技术术语的保护和恢复逻辑（`front.py:save_tech_terms`、`load_glossary_from_yaml` 等），避免 TN 把它们改坏。
- 中英混读：2.5 的 `infer` 有 `lang` 参数；混读效果待确认。

**推理部署**：

- 本地：`webui.py`（Gradio）、`indextts/cli_v2.py`、Python API `indextts/infer_v2_5.py:IndexTTS2.infer`（2.5 的类也叫 `IndexTTS2`）。可选 bf16 / fp16、DeepSpeed、编译的 CUDA kernel、`use_accel`（`indextts/accel/`，自带 KV cache 管理的 GPT 加速）、`use_torch_compile`。
- vLLM：README 指向外部 vLLM recipe（`README.md:201-203`），代码不在本仓库，待确认。
- TensorRT / Triton：`backends/trt/`，TensorRT + TensorRT-LLM + PyTriton，支持非流式和 decoupled 流式两种模式（`backends/trt/serving/triton_server.py`）。只支持 2.0 权重，需要单独的 venv 和 OpenMPI。
- 显存：README 没有给数字；显存小于 10 GB 时自动进入 low-VRAM 模式，非流式的长文本按 40 字切（`infer_v2_5.py:124-131,510`）。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 2.5 推理入口 | `indextts/infer_v2_5.py:IndexTTS2.infer` | 低显存分支、流式 / 非流式分发 |
| 主生成流程 | `indextts/infer_v2_5.py:IndexTTS2.infer_generator` | 参考缓存 → 切段 → GPT → CFM → BigVGAN |
| 文本切段 | `indextts/infer_v2_5.py:IndexTTS2.split_text_by_tokens` | 超过 120 token 才按标点切 |
| 文本前端 | `indextts/utils/front.py:TextNormalizer.normalize` | WeTextProcessing / wetext，保护拼音标注、术语 |
| 情感文本推断 | `indextts/infer_v2_5.py:QwenEmotion` | 文本 → 8 维情感向量 |
| 2.0 首段快速切分 | `indextts/infer_v2.py:infer`（`quick_streaming_tokens`） | 只在 2.0 生效 |
| TRT 分块流式 | `backends/trt/pipeline/streaming.py` | 100 code 一块，重叠交叉淡化 |
| TRT 服务 | `backends/trt/serving/triton_server.py` | PyTriton，`indextts2_stream` 为 decoupled 模型 |

## 对各机制的回答

- 判停：不涉及。
- 打断与截断：PyTorch 路径是同步 generator，调用方停止迭代就不会再往下合成下一段（推断，`indextts/infer_v2_5.py:IndexTTS2.infer_generator`）；但一段内部不可中断，粒度是整段。TRT 流式路径能否取消待确认。没有时间戳。
- 首音优化：PyTorch 路径没有句内流式，只能靠上游把首句切短；TRT 后端有 100 code 分块流式，但只支持 2.0，没有首包数字（`backends/trt/pipeline/streaming.py`）。
- 工具回合：不涉及。
- 会话恢复与上下文同步：不涉及。跨请求只缓存说话人条件（按参考路径）。
- 音频前处理：不涉及（参考音频只做截断和重采样）。
- 评测：README 给出 CV3-Eval 多语种和跨语言的 WER / SS 表，以及 RTX 4090 上的 RTF 表（`README.md:410-483`）；`tests/regression_test.py` 是回归测试，不是质量评测。

## 取舍与局限

- **可控性最强，延迟最弱**：情感 / 音色分离、拼音纠音、语速控制在同类里最完整；但官方路径首包等于整句合成时间，`num_beams=3` 和 25 步 CFM 都会拉长延迟。改 `num_beams=1` 能快多少、质量掉多少，待确认。
- **流式路径割裂**：分块流式只在第三方移植的 TRT 后端，只支持 2.0 权重，只验证过 batch 1。
- **许可**：bilibili Model Use License 覆盖权重和代码（`LICENSE:9`）。上一月月活超过 1 亿或上一年营收超过 10 亿元人民币的主体需另行申请（`LICENSE:18`）；不得用它改进其他 AI 模型（`LICENSE:28`）。协议文本里的模型名是 "bilibili indextts2"，是否覆盖 2.5 待确认。
- **适合的链路**：级联链路里对表现力要求高、能接受数百毫秒首包的场景，采用短语级调用；也适合离线生成带情感的提示语库。对首包要求严格的实时对话不是首选。
- **接入建议**（推断，未实测）：
  1. 常驻实例、固定一个参考音频，让说话人条件缓存命中；
  2. 上游按短语切分，首句压到 6–8 字左右；
  3. 评估 `num_beams=1` 对首包和质量的影响；
  4. 需要句内流式时，单独评估 2.0 + TRT 后端，并实测首块延迟。

## 相关

- 机制页：[首音优化](../../03-mechanisms/first-audio.md)、[打断与截断](../../03-mechanisms/interruption.md)、[评测](../../03-mechanisms/evaluation.md)
- 对比页：[模型对比矩阵](../../05-comparison/model-matrix.md)
