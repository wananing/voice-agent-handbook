# CosyVoice

> 仓库：https://github.com/FunAudioLLM/CosyVoice
> 分析基于：commit 074ca6d
> 状态：草稿
> 最后更新：2026-10-01

## 定位

阿里通义 FunAudioLLM 的 LLM-based 零样本 TTS，当前主力是 Fun-CosyVoice3-0.5B-2512（另有 CosyVoice2-0.5B、CosyVoice-300M 系列）。它面向需要中文质量、零样本克隆和低首包的生产场景。同类项目里，它是少数**同时**提供文本流式输入（README 叫 "Bi-Streaming"）、音频流式输出、vLLM 和 Triton + TensorRT-LLM 部署路径的开源 TTS，代码和权重许可也比较宽松。代码是 Apache-2.0；权重许可仓库里没写，待确认。

在 voice agent 里，它适合做级联链路的 TTS 节点：上游 LLM 的文本按短语切好后送进来，音频流式输出给播放端。

## 整体架构

```
tts_text ──► CosyVoiceFrontEnd ─► text token ─┐
prompt_wav ─► speech tokenizer / CAM++ / mel ─┤
                                              ▼
            [llm_job 线程] Qwen2 LM ── 25 Hz speech token ──► tts_speech_token_dict[uuid]
                                                                     │（主线程每 0.1 s 轮询）
                                                                     ▼
            flow matching（CausalConditionalCFM + DiT）─► mel ─► CausalHiFTGenerator ─► 24 kHz PCM
```

**模型结构**：文本 LM（`CosyVoice3LM`，Qwen2 骨干）自回归生成 25 Hz 语音 token，再经 causal flow matching（CV3 的 estimator 是 DiT）转成 mel，最后由 HiFT vocoder 出波形（`examples/libritts/cosyvoice3/conf/cosyvoice3.yaml:23-97`）。CV2/CV3 输出 24 kHz，CV1 是 22.05 kHz。

**进程 / 线程模型**：`CosyVoice2Model.tts` 为每次请求生成一个 uuid，起一个 `llm_job` 线程往共享 dict 里追加 token。主线程在 `stream=True` 时每 0.1 s 轮询一次，攒够 `token_hop_len + pre_lookahead_len` 个 token 就跑一次 flow + HiFT 并 yield 一块音频（`cosyvoice/cli/model.py:328-397`）。所有请求共享同一个模型实例，靠 `self.lock` 保护 dict。

**流式能力**：

- 音频流式输出：支持（`stream=True`）。本地 Python 路径的首块要攒 `25 + pad + 3` 个 token（pad 用来把参考 token 数补到 25 的整数倍），相当于 LLM 先生成约 1.1–2.1 s 音频的 token（推断）。之后 hop 按 `stream_scale_factor=2` 放大，上限 `token_max_hop_len`。
- 文本流式输入：支持，但有限制。`tts_text` 传 Python generator 时走 `Qwen2LM.inference_bistream`（`cosyvoice/llm/llm.py:552`），按训练时的 `mix_ratio=[5, 15]`，每攒 5 个文本 token 解码 15 个语音 token。只支持 CV2/3，**不能和 vLLM 同用**（`model.py:105` 的 assert）。Triton runtime 里也没有 bistream。官方示例提醒"仍然需要基本的切句逻辑"（`example.py:60-61`）。
- 首包量级：README 写 "latency as low as 150ms"（`README.md:19`），没有说明硬件和口径。Triton + TRT-LLM 的数字口径是"客户端发请求到收到第一块"：CV2 在 L20、并发 1 时 P50 218 ms，开说话人缓存后 185 ms（`runtime/triton_trtllm/README.Cosyvoice2.Unet.md:97-100`）；CV3 只给了并发 4 的数字，P50 740 ms（`README.Cosyvoice3.md:62-70`）。CV3 单并发的首包待确认。
- 疑似问题：`model.py:360` 改的是实例属性 `self.token_hop_len`，不是局部变量。第一次流式请求之后 hop 会停在上限，后续请求的首块会变大，并发请求之间也会互相影响（推断，需实测）。CV1 分支用的是局部变量（`model.py:193,209`）。

**音色**：

- 零样本克隆：`inference_zero_shot(tts_text, prompt_text, prompt_wav)`，参考音频上限 30 s（`frontend.py` 的 `_extract_speech_token` assert）。CV3 的 prompt_text 前面要加 `You are a helpful assistant.<|endofprompt|>`（`example.py`）。
- 预置 / 缓存音色：CV1-SFT 有 `inference_sft(spk_id)`；零样本说话人可以用 `add_zero_shot_spk` + `save_spkinfo` 缓存成 `zero_shot_spk_id`（`cosyvoice/cli/cosyvoice.py:69-78`），省掉每次提参考特征。
- 跨语言：`inference_cross_lingual` 不需要参考文本。CV3 覆盖 9 种语言、18+ 种方言（`README.md:15`）。
- 指令控制：`inference_instruct2` 支持方言、情感、语速、音量；`speed` 参数只在非流式下生效（`model.py:445` 的 assert）。

**中文**：

- 有文本前端：`CosyVoiceFrontEnd.text_normalize`（`frontend.py:127-160`）。优先用 `ttsfrd`（需要另装 whl），否则用 `wetext`（默认）。中文分支先做 TN，再把 `.` 换成 `。`、去括号，最后按标点切成 60–80 token 的段，默认不按逗号切。
- 会跳过 TN 的三种情况（`frontend.py:128-135`）：
  - 输入是 generator（文本流式输入）；
  - 文本里含 `<|` 和 `|>`，例如 CV3 的 `inference_cross_lingual` 示例把 `You are a helpful assistant.<|endofprompt|>` 直接写进 `tts_text`（`example.py:80`）；
  - 调用方传 `text_frontend=False`。
- 多音字：支持拼音纠音（README 叫 pronunciation inpainting），如 `[j][ǐ]`（`example.py:94`），英文可用 CMU 音素（`README.md:17`）。
- 细粒度标签：`[breath]` 等标签写在文本里（`example.py:80`，支持列表见 `cosyvoice/tokenizer/tokenizer.py`）。
- 日文需要先转成片假名（`example.py` 注释）。
- 中英混读：README 称 CV3 不靠传统前端也能读数字和符号（`README.md:18`）；混读效果待确认。

**推理部署**：

- vLLM：CV2/3 的 LLM 部分可以跑在 vLLM 上（`load_vllm`，`model.py:281`；`vllm_example.py`），只占 `gpu_memory_utilization=0.2`。flow 部分可以用 TensorRT（`load_trt`）。
- Triton + TensorRT-LLM：`runtime/triton_trtllm/`，LLM 走 in-flight batching，Token2Wav 可以分卡部署；README 称 TRT-LLM 比 HF 实现快 4 倍（`README.md:204`）。
- 服务端示例：`runtime/python/fastapi/server.py` 和 `runtime/python/grpc/server.py`。两者调用 `inference_*` 时**都没开 `stream=True`**，也没有 WebSocket，接 voice agent 要自己封装。
- 显存：0.5B 模型；整体显存需求待确认。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 模型加载 | `cosyvoice/cli/cosyvoice.py:AutoModel` | 按目录选 CosyVoice / CosyVoice2 / CosyVoice3，可开 `load_trt`、`load_vllm` |
| 零样本合成 | `cosyvoice/cli/cosyvoice.py:CosyVoice.inference_zero_shot` | 先 `text_normalize` 切段，再对每段调 `model.tts` |
| 说话人缓存 | `cosyvoice/cli/cosyvoice.py:add_zero_shot_spk` / `save_spkinfo` | 预先提取参考音频特征，常驻服务时省 prefill 前的准备 |
| 文本前端 | `cosyvoice/cli/frontend.py:CosyVoiceFrontEnd.text_normalize` | ttsfrd / wetext TN + 切段；generator 输入直接跳过 |
| 流式主循环 | `cosyvoice/cli/model.py:CosyVoice2Model.tts` | 后台 `llm_job` 线程出 token，主线程轮询并分块 `token2wav` |
| 文本流式输入 | `cosyvoice/llm/llm.py:Qwen2LM.inference_bistream` | 5:15 交错，文本不够时插 fill token 等待 |
| vLLM 后端 | `cosyvoice/cli/model.py:CosyVoice2Model.load_vllm` | 与 bistream 互斥 |
| Triton 流式 | `runtime/triton_trtllm/model_repo_cosyvoice3/cosyvoice3/1/model.py` | 首块 15 + 3 个 token，之后指数放大 |
| HTTP / gRPC 示例 | `runtime/python/fastapi/server.py`、`runtime/python/grpc/server.py` | 非流式，整段合成后分段返回 |

## 对各机制的回答

- 判停：不涉及。
- 打断与截断：仓库没有取消接口。`model.tts` 的 `llm_job` 跑在独立线程里，调用方停止迭代 generator 后，该线程仍会生成到结束（推断，`cosyvoice/cli/model.py:CosyVoice2Model.tts`）；要中途停止需要自己加停止标志。没有字级时间戳，按已播放截断只能靠调用方按短语记账。
- 首音优化：音频流式输出 + 文本流式输入两条路径都有；说话人缓存（`add_zero_shot_spk`）在 Triton 上把首块 P50 从 218 ms 降到 185 ms（`README.Cosyvoice2.Unet.md:97,100`）。内置切段面向离线质量（首段可能长达 60–80 token），voice agent 里要自己按短语切。
- 工具回合：不涉及。
- 会话恢复与上下文同步：不涉及。每次请求无状态，跨请求只靠同一份参考 / `spk2info` 保持音色。
- 音频前处理：不涉及（只对参考音频做重采样和特征提取）。
- 评测：README 有 test-zh / test-en / test-hard 的 CER / WER 和 SS 表（`README.md:62-81`），另发布了 CV3-Eval 评测集；仓库内没有首包延迟的评测脚本，Triton 目录下的 `client_grpc.py` 可以测首块延迟。

## 取舍与局限

- **质量 vs 延迟**：0.5B 模型、中文 CER 和 SS 在开源里属第一梯队（README 自述）。但本地流式首块要先攒 1–2 s 音频的 token，单并发首包能否稳定在几百毫秒内，要看部署方式，需要实测。
- **文本流式输入不是免费的**：不能用 vLLM，不做 TN，参考音频越长、首包前需要的文本越多（参考语音 token 要先和文本按 5:15 交错完）。更稳妥的接法是"短语级调用 + 音频流式输出"，文本流式输入作为对照。
- **可控性**：instruct、拼音纠音、细粒度标签都有，可控性在同类里较强；但采样带随机性，跨请求韵律会变。
- **服务化要自己补**：官方 FastAPI / gRPC 示例不开流式，生产形态要么自己封装本地流式，要么走 Triton + TRT-LLM（只支持整句输入）。
- **适合的链路**：级联链路的 TTS 节点；半级联里如果上游是端到端模型出文本，也可以只用它做下行合成。
- **接入建议**（推断，未实测）：
  1. 启动时用 `add_zero_shot_spk` 缓存说话人，服务常驻并预热；
  2. 自己从 LLM delta 里切短语（首段短一些），每个短语一次 `inference_zero_shot(..., zero_shot_spk_id=..., stream=True)`；
  3. 把 `token_hop_len` 改成请求内局部变量，避免跨请求放大；
  4. 在调用前自己做一道 TN，文本流式输入模式下这是唯一的 TN。

## 相关

- 机制页：[首音优化](../../03-mechanisms/first-audio.md)、[打断与截断](../../03-mechanisms/interruption.md)、[评测](../../03-mechanisms/evaluation.md)
- 对比页：[模型对比矩阵](../../05-comparison/model-matrix.md)
