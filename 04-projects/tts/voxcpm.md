# VoxCPM

> 仓库：https://github.com/OpenBMB/VoxCPM
> 分析基于：commit f772e49
> 状态：草稿
> 最后更新：2026-10-01

## 定位

OpenBMB（面壁智能）的 tokenizer-free TTS。它不把音频离散化，而是用**扩散自回归**在 AudioVAE 的连续隐空间里直接生成语音表征。当前版本 **VoxCPM2**：2B 参数，基于 MiniCPM-4，支持 30 种语言和 9 种中文方言，原生输出 48 kHz（`README_zh.md:43-54`）。仓库也支持 VoxCPM1.5（0.6B，44.1 kHz）和 VoxCPM-0.5B（16 kHz）。

和同类项目的区别：
- 音色玩法最多：只给参考音频、不需要转写就能克隆；可以在文本开头用括号写自然语言风格指令；也可以完全凭描述"设计"新音色。
- 句内流式粒度细：每个自回归步出一个 patch 就解码一块。
- 许可干净：代码和权重都是 Apache-2.0，README 明写可以免费商用（`README_zh.md:54`）。

在 voice agent 里，它可以做级联链路的 TTS 节点：上游按短语切分，它负责音频流式输出。高并发场景走外部的 Nano-vLLM / vLLM-Omni。

## 整体架构

```
text ─►（可选）TextNormalizer(wetext，默认关) ─► 中文强制拆成单字的 tokenizer
reference_wav / prompt_wav(+prompt_text) ─►（可选 ZipEnhancer 降噪）─► AudioVAE 编码 ─► prompt_cache
                         ▼
序列：[参考] 文本 <audio_start> [续写音频 patch …]
                         ▼   每一步（LM 6.25 Hz）
   TSLM / RALM（MiniCPM-4 骨干）─► LocDiT（flow matching，默认 10 步，CFG 2.0）─► 1 个 patch 的连续 latent
                         ▼   streaming=True 时立即
   AudioVAE.streaming_decode().decode_chunk ─► 48 kHz 音频块（VoxCPM2）
   stop_head 判断结束
```

**模型结构**：LocEnc → TSLM → RALM → LocDiT 四段，在 AudioVAE 的连续隐空间中自回归生成，每步由 LocDiT 用 flow matching 生成一个 patch（`README_zh.md:386`，`src/voxcpm/model/voxcpm2.py:VoxCPM2Model._inference`）。所以它是"LM + 局部扩散 + VAE"，不是"LM + 离散 codec"。VoxCPM2 输出 48 kHz（参考音频按 16 kHz 输入），1.5 是 44.1 kHz（`README_zh.md:372`）。

**进程 / 线程模型**：单进程同步 generator，没有后台线程。base_lm / residual_lm 的 KV cache 按 batch 1 建（`voxcpm2.py:184,199`），所以本仓库进程内不能并发。`optimize()` 用 `torch.compile` 编译单步前向和 DiT（`voxcpm2.py:279`），构造时会跑一次预热（`core.py:99-104`）。

**流式能力**：

- 音频流式输出：支持。主循环每做一步自回归，就把最新 patch 的 latent yield 出去（`voxcpm2.py:1095-1110`）；调用侧用有状态的 `audio_vae.streaming_decode()` 逐块解码（`voxcpm2.py:956-962`）。LM 6.25 Hz 意味着每块约 160 ms 音频（推断）。首块只需要文本和参考的 prefill，加一步 LocDiT（默认 `inference_timesteps=10`），再加一次 VAE 解码（推断）。
- 文本流式输入：不支持。序列是"[参考] 文本 `<audio_start>` [音频]"，文本必须一次给全（`voxcpm2.py:845-858`）。
- 首包量级：仓库没有给首包数字，待确认。只有 RTF：RTX 4090 上 VoxCPM2 约 0.30，1.5 约 0.15；用 Nano-vLLM 时分别约 0.13 和 0.08（`README_zh.md:379-380`）。
- 流式下关闭了坏例重试：非流式默认在"音频 / 文本长度比 ≥ 6"时最多重试 3 次，流式直接关掉（`voxcpm2.py:486-488,835-837`）。漏读、拖长这类坏例在流式下会直接播出去。
- 每次调用都重建 prompt cache：`core.VoxCPM._generate` 每次都调 `build_prompt_cache`，重新 VAE 编码参考音频（`core.py:266-277`）。模型层有 `build_prompt_cache` / `generate_with_prompt_cache_streaming`（`voxcpm2.py:695,791`），常驻服务时应该建一次、复用。

**音色**：

- 零样本克隆（VoxCPM2）有三种用法（`README_zh.md:161-194`）：只给 `reference_wav_path`，**不需要转写**；给 `prompt_wav_path + prompt_text` 做续写式克隆；两者同时给，相似度最高。1.5 和 0.5B 只支持续写式克隆。
- 风格控制：在文本开头写括号指令，如 `(稍快一点，欢快的语气)`（`README_zh.md:172-178`）。
- 音色设计：不需要参考音频，用自然语言描述性别、年龄、音色、情绪、语速来造音色（`README_zh.md:47`）。README 承认音色设计和可控克隆"结果可能因生成次数而异，建议尝试生成 1~3 次"（`README_zh.md:642`）。
- 预置音色：没有。
- 跨语言：30 种语言，不需要语言标签。
- 可复现：`seed` 参数（`core.py:_generate`）。

**中文**：

- 有 TN 但**默认关闭**：`normalize=False`（`core.py:193`），开启后用 `src/voxcpm/utils/text_normalize.py`（基于 wetext，文件头注释说明部分函数抄自 CosyVoice）。
- tokenizer 会把多字中文 token 强制拆成单字（`src/voxcpm/model/utils.py:mask_multichar_chinese_tokens`）。
- 多音字：仓库里没有拼音标注机制，待确认。
- 中英混读：README 称"直接输入原始文本即可合成，无需额外语言标签"（`README_zh.md:46`），效果待确认。

**推理部署**：

- 本仓库：Python API（`src/voxcpm/core.py:VoxCPM`）、CLI（`src/voxcpm/cli.py`）、Gradio（`app.py`）。
- 加速 / 服务：外部的 Nano-vLLM-VoxCPM（支持并发请求、异步 API 和 FastAPI HTTP 服务）和 vLLM-Omni（PagedAttention、连续批处理、OpenAI 兼容的 `/v1/audio/speech`，自述支持流式分块输出）（`README_zh.md:279-323`）。代码都不在本仓库，能力待确认。另有 llama.cpp-omni 端侧推理，Apple M4 Pro 上 RTF 约 1.76（`README_zh.md:360`）。
- 显存：VoxCPM2 约 8 GB，1.5 约 6 GB，0.5B 约 5 GB（`README_zh.md:381`）。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 对外 API | `src/voxcpm/core.py:VoxCPM.generate` / `generate_streaming` | 降噪 → 建 prompt cache → 可选 TN → 生成 |
| 模型加载 / 预热 | `src/voxcpm/core.py:VoxCPM.__init__`、`from_pretrained` | 可初始化 ZipEnhancer 降噪器，`optimize` 时预热 |
| prompt 缓存 | `src/voxcpm/model/voxcpm2.py:VoxCPM2Model.build_prompt_cache` | 参考音频 VAE 编码，可以复用 |
| 带缓存的流式生成 | `src/voxcpm/model/voxcpm2.py:VoxCPM2Model.generate_with_prompt_cache_streaming` | 流式时关闭坏例重试 |
| 自回归主循环 | `src/voxcpm/model/voxcpm2.py:VoxCPM2Model._inference` | 逐步出 patch，`stop_head` 判断结束 |
| 流式 VAE 解码 | `src/voxcpm/model/voxcpm2.py:VoxCPM2Model._generate_with_prompt_cache` 中的 `audio_vae.streaming_decode()` | 有状态逐块解码 |
| 文本规范化 | `src/voxcpm/utils/text_normalize.py:TextNormalizer` | 默认不启用 |
| 时间戳对齐 | `src/voxcpm/timestamps/stable_ts.py` | 生成后用 stable-ts 对齐，词级 / 字级 |

## 对各机制的回答

- 判停：不涉及。
- 打断与截断：流式是同步 generator，调用方停止迭代或关闭 generator 后，就不再生成下一个 patch，可以按约 160 ms 的粒度中止（推断，`src/voxcpm/model/voxcpm2.py:VoxCPM2Model._inference`）。截断记账可以用可选的生成后对齐：`voxcpm[timestamps]` 基于 stable-ts（Whisper）给出词级 / 字级时间戳，字级是 best-effort（`README_zh.md:244-258`，`src/voxcpm/timestamps/`）。它是事后对齐，不是流式时间戳。
- 首音优化：每步一个 patch 的细粒度流式（`src/voxcpm/model/voxcpm2.py:VoxCPM2Model._inference`）。要压首包，需要启动时建好 prompt cache 并复用，开 `optimize()` 并预热；仓库没有首包数字。
- 工具回合：不涉及。
- 会话恢复与上下文同步：不涉及。
- 音频前处理：对**参考音频**有可选处理，不是对用户上行音频：ZipEnhancer 降噪（`core.py` 的 `denoise=True`，模型 `iic/speech_zipenhancer_ans_multiloss_16k_base`），以及默认关闭的 VAD 静音裁剪（`voxcpm2.py:_trim_audio_silence_vad`）。
- 评测：README 给出 Seed-TTS-eval 表（VoxCPM2 test-ZH CER 0.97 / SIM 79.5）和 CV3-eval 多语言表（`README_zh.md:400-450`）。仓库内没有评测脚本，`scripts/` 下只有训练和推理测试。

## 取舍与局限

- **延迟结构好，但单步更重**：流式粒度细，首块门槛在结构上低；但 2B 模型的 RTF 约 0.30，比 0.5B 级模型慢。实际首包必须实测。
- **流式稳定性**：流式下没有坏例重试，极短句或长句的漏读、拖长会直接播出。生产上要配合 ASR 回检，或者限制句长。
- **可控性强但有随机性**：括号风格指令、音色设计很灵活，但官方承认结果随生成次数变化，要固定 `seed` 并做候选筛选。
- **服务化依赖外部项目**：本仓库进程内不能并发，生产并发要靠 Nano-vLLM 或 vLLM-Omni。
- **自评数字有冲突**：VoxCPM README 的 Seed-TTS test-ZH 里 VoxCPM2 SIM 79.5，高于 CosyVoice3-0.5B 的 78.0；IndexTTS README 的 CV3-Eval zh 里 VoxCPM2 SS 74.99，低于 CosyVoice3-0.5B 的 80.01。两家口径不同，需要自己盲听。
- **适合的链路**：级联链路里需要高采样率、灵活音色设计和宽松许可的 TTS 节点，采用短语级调用；1.5（0.6B）是同接口的轻量降级选项。

## 相关

- 机制页：[首音优化](../../03-mechanisms/first-audio.md)、[打断与截断](../../03-mechanisms/interruption.md)、[音频前处理](../../03-mechanisms/audio-preprocessing.md)、[评测](../../03-mechanisms/evaluation.md)
- 对比页：[模型对比矩阵](../../05-comparison/model-matrix.md)
