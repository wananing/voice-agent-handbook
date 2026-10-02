# Moshi

> 仓库：https://github.com/kyutai-labs/moshi
> 许可：代码 Python 部分 MIT、Rust 后端 Apache-2.0；权重 CC-BY 4.0（见仓库 LICENSE 文件）
> 分析基于：commit e6a55d2
> 状态：草稿
> 最后更新：2026-10-01

## 定位

Moshi 是 Kyutai 发布的**原生全双工语音语言模型**，README 称它为 "speech-text foundation model and full-duplex spoken dialogue framework"。

和级联方案不同，它没有"轮次"的概念。一个 7B 模型在每个 80 ms 时间步里同时做两件事：读入用户的音频，产出自己的音频和对应文本。何时说话、何时沉默、被插话时要不要停，都由模型逐步采样决定。

仓库包含四部分：

- 模型推理代码，三套实现：PyTorch 研究版 `moshi/`、MLX 端侧版 `moshi_mlx/`、Rust/Candle 生产版 `rust/`；
- 流式神经音频编解码器 Mimi；
- Web 客户端 `client/`；
- Rust 的 `moshi-server`。这个服务端同时承载 Kyutai 的 STT 和 TTS，[unmute](unmute.md) 用的就是它。

权重以 CC-BY 4.0 发布，有两个音色：Moshiko（男声）和 Moshika（女声）。

适用边界：

- 只会说英语（`FAQ.md`："Moshi only speaks English"）；
- 没有系统提示，不能调用工具；
- 换声音或人设需要微调（FAQ 写 "not currently supported"，但 README 指向了 `moshi-finetune` 仓库，加载器也支持 `--lora-weight`）。

它更适合当"真全双工长什么样"的参照，而不是直接拿来做业务 agent。

## 整体架构

```
          用户麦克风                                        扬声器
              │ Opus                                          ▲ Opus
              ▼                                               │
   Mimi encoder (24 kHz → 12.5 Hz, 每帧 1920 样本 = 80 ms)    Mimi decoder
              │ 8 个码本（用户流）                             ▲ 8 个码本（Moshi 流）
              ▼                                               │
   ┌──────────────── LMGen.step()：每 80 ms 一步 ────────────────────┐
   │ 输入缓存：[文本 1 | Moshi 音频 8 | 用户音频 8] 共 17 个流（带延迟）  │
   │ Temporal Transformer (7B, context 3000 步) → 文本 logits          │
   │   采样 1 个文本 token（inner monologue，pad=3 表示沉默）           │
   │ Depth Transformer（小，以文本 token 为条件）→ 8 个 Moshi 音频 token │
   └─────────────────────────────────────────────────────────────────┘
              │ 文本 token（非 pad 时作为 MT=2 下发）
```

**三路流**（README "Model architecture" 一节；`moshi/moshi/models/loaders.py` 的 `_lm_kwargs`）：

- 文本流 1 路：这是 Moshi 对**自己要说的话**的转写，即 inner monologue。它不转写用户的话。README 说这一路"greatly improves the quality of its generation"。
- Moshi 自己的音频流 8 个码本：`dep_q = 8`，由 Depth Transformer 生成。
- 用户音频流 8 个码本：从麦克风经 Mimi 编码后写入，模型只读不生成。

`n_q = 16` 是两路音频码本的总数。

**延迟**：

- `delays = [0, 0, 1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1]`：文本流和每路音频的第一个（语义）码本不延迟，其余声学码本延迟 1 步。
- README 给出的延迟账：理论 160 ms（Mimi 帧长 80 ms + 声学延迟 80 ms），L4 GPU 上实测低至 200 ms。

**Mimi**：

- README 描述：24 kHz 音频压到 12.5 Hz、1.1 kbps，全流式，帧长 80 ms。第一个码本蒸馏自 WavLM，同时承载语义和声学信息。只用对抗损失训练。
- 实现在 `moshi/moshi/models/compression.py:MimiModel`；Rust 版在 `rust/moshi-core/src/mimi.rs`。

**推理服务的流式跑法**：一帧进、一步走、一帧出，没有缓冲整句。三套服务端的结构如下。

- PyTorch `moshi/moshi/server.py:ServerState`：
  - 模型以 `streaming_forever(1)` 常驻流式状态。
  - `recv_loop` 把收到的 Opus 解码后攒够 1920 个样本（一帧），调 `mimi.encode`，再逐码本步调 `lm_gen.step`。拿到输出 token 就 `decode_and_send`：Mimi 解码成 PCM，Opus 编码后下发 MT=1；文本 token 不是 0/3（结束/填充）时下发 MT=2。
  - 收发在同一个协程里串行完成。
  - `handle_chat` 用一把 `asyncio.Lock`，同一时刻只服务一个连接。
- Rust `rust/moshi-backend/src/stream_both.rs`：
  - `handle_socket` 为每个连接起一组任务：接收任务把 Opus 解成 PCM 后经 `std::sync::mpsc` 交给推理线程；推理线程（`std::thread::spawn` 跑 `StreamingModel::run`）逐帧 `encode_step → state.step → decode_step`，结果经 `tokio::mpsc` 交给发送任务。
  - 一个 360 s 的总超时兜底。
  - 会话结束后把文本和音频 token 存成 safetensors 和 JSON 日志。
- Rust `rust/moshi-server/src/lm.rs:Lm::handle_socket`：结构相同，`max_steps` 写死 4096，同样 360 s 超时。

**协议**（`rust/protocol.md`）：WebSocket 二进制帧，首字节是消息类型：0 握手、1 音频（Ogg/Opus）、2 文本、3 控制、4 元数据、5 错误。

- 两个服务端都只处理上行的音频消息，其他类型直接丢弃（`moshi-server/src/lm.rs` 的接收循环；`server.py:recv_loop` 的 `unknown message kind`）。
- 控制消息注明 "not used in full streaming mode"。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| 模型结构与码本布局 | `moshi/moshi/models/loaders.py:_lm_kwargs` | `n_q=16`、`dep_q=8`、`context=3000`、`delays` |
| 单步生成 | `moshi/moshi/models/lm.py:LMGen._step` | 把用户码本按延迟写入环形缓存 → 主 Transformer → 采样文本 token → Depth Transformer 出音频 token |
| Depth Transformer | `moshi/moshi/models/lm.py:LMGen.depformer_step` | 逐码本自回归生成 8 个音频 token |
| 特殊 token | `moshi/moshi/models/lm.py:LMModel` | `existing_text_padding_id=3`（沉默时的文本）、`existing_text_end_padding_id=0` |
| Mimi codec | `moshi/moshi/models/compression.py:MimiModel` | 流式 encode/decode，12.5 Hz |
| PyTorch 服务端 | `moshi/moshi/server.py:ServerState.recv_loop` / `decode_and_send` / `handle_chat` | 逐帧编码、生成、解码、下发；全局锁，单会话 |
| Rust 服务端（独立部署） | `rust/moshi-backend/src/stream_both.rs:handle_socket` / `StreamingModel::run_with_state` | 每连接一条推理线程，360 s 超时，`max_steps` 上限 4500 |
| Rust 服务端（moshi-server 模块） | `rust/moshi-server/src/lm.rs:Lm::handle_socket` | `max_steps=4096`，只收音频消息 |
| 采样偏置 | `rust/moshi-core/src/lm_generate_multistream.rs:State` | `pad_mult`：填充 token 概率乘 `exp(pad_mult)`，调节"话多话少" |
| 协议定义 | `rust/protocol.md`、`rust/moshi-server/src/protocol.rs` | 单字节类型 + 载荷 |
| Web 客户端放音缓冲 | `client/src/audio-processor.ts` | 缓冲超过上限就丢弃最旧的包，追回延迟 |
| 采音约束 | `client/src/pages/Conversation/components/UserAudio/UserAudio.tsx` | `echoCancellation`、`noiseSuppression`、`autoGainControl` 全开 |
| 性能基准 | `scripts/moshi_benchmark.py`、`rust/moshi-backend/src/benchmark.rs` | 按步计时 |

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：**不存在外部判停，由模型内部决定**。
  - 每 80 ms，模型在文本流上采样：填充 token（id 3）表示"这一步不说话"，实词 token 表示开口，音频流随之跟上（`lm.py:LMGen._step`）。没有 VAD，没有阈值，也没有"一轮结束"的事件。
  - 唯一的外部旋钮是 Rust 端的 `pad_mult`，它偏置填充 token 的概率，让模型更倾向沉默或更倾向说话（`lm_generate_multistream.rs:State`）。Web 会话可以通过 `SessionConfigReq` 传入这个参数（`stream_both.rs`）。
- [打断与截断](../../03-mechanisms/interruption.md)：**没有打断事件**。
  - 用户音频在 Moshi 说话期间照常写入输入流，模型同一步就能"听到"，由它自己决定是停下、继续还是附和。
  - 服务端没有取消逻辑，没有截断上下文的概念。上下文就是模型内部的 token 缓存，已经说出的和被打断的都在里面。
- [首音优化](../../03-mechanisms/first-audio.md)：结构性解决。
  - Mimi 帧长 80 ms，声学码本只延迟 1 步，每步都可能出声。README：理论 160 ms，L4 实测约 200 ms。
  - 工程侧：服务启动时 `warmup()` 先跑几帧；PyTorch 版用 CUDA graph（`LMGen` 里的 `graphed_main` / `graphed_depth`）。
- [工具回合](../../03-mechanisms/tool-calls.md)：不涉及。协议没有工具事件，模型没有工具相关的训练，上行文本消息会被服务端丢弃。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：不涉及会话恢复，断线即重置（`handle_chat` 里 `reset_streaming()`）。
  - 会话长度有硬上限：moshi-server `max_steps=4096`（约 5.5 分钟）；moshi-backend 最多 4500 步；两者都有 360 s 超时。FAQ 写道 "Moshi stopped talking after 5 min. This is expected"（MLX 和 Rust 版用固定缓冲，不丢弃旧内容）。
  - PyTorch 版理论上不限时长，但 FAQ 说"mostly untested"，质量会下降。
  - 没有系统提示，也不能注入上下文。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：服务端不做前处理，Mimi 直接编码原始 PCM。
  - 回声靠客户端：Web UI 开启浏览器的 `echoCancellation` / `noiseSuppression`（`UserAudio.tsx`）。README 推荐用 Web UI，理由是它的回声消除"helps the overall model quality"。
  - 命令行客户端和 MLX 本地版没有 AEC，也不做跳帧追延迟。
  - 全双工模型一直在听，所以回声消除比级联方案更关键。
- [评测](../../03-mechanisms/evaluation.md)：仓库里没有对话质量评测代码，只有性能基准（`scripts/moshi_benchmark.py` 按步计时）和单元测试（`moshi/tests/test_lm.py`、`scripts/test_mimi.py` 等）。模型层面的评测在论文里，本仓库 README 只给了延迟数字。

## 取舍与局限

- **全双工换可控性**：判停、打断、附和、重叠说话都交给模型，体验自然，延迟低。代价是这些行为外部几乎无法干预，只有采样温度、top-k、`pad_mult` 这类偏置。业务规则（"这句话不许被打断""先查资料再回答"）无处安放。
- **没有文本缓冲窗口**：文本 token 只比声学码本早 1 步（80 ms），音频逐帧下发，生成后来不及做内容审核或改写。
- **文本流只转写自己**：inner monologue 是模型自己说的话，不包含用户的话。如果需要用户转写（用于日志、检索、外挂工具），得另跑 ASR。
- **会话时长约 5 分钟**（Rust/MLX），上下文窗口 3000 步（约 240 s）。
- **并发**：PyTorch 服务端全局锁，单会话；Rust 每个连接一条推理线程，不做跨会话批处理。单卡能撑几路，仓库没有数字，待确认。
- **资源**：PyTorch bf16 约需 24 GB 显存；Rust 支持 int8；MLX 支持 int4/int8。FAQ 说量化到 4 bit 以下质量急剧下降。
- **语言与人设**：只支持英语；有两个固定音色；没有系统提示。
- **生态价值**：同一套多流架构和 `moshi-server` 被用于 Kyutai 的 STT、TTS 和 Hibiki（同声传译）。[unmute](unmute.md) 就是在这些组件之上，用级联方式换回了可控性。两者的对比见 unmute 页"和 moshi 原生全双工的差别"一节。

## 相关

- 机制页：[判停](../../03-mechanisms/turn-detection.md)、[打断与截断](../../03-mechanisms/interruption.md)、[首音优化](../../03-mechanisms/first-audio.md)、[工具回合](../../03-mechanisms/tool-calls.md)、[会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)、[音频前处理](../../03-mechanisms/audio-preprocessing.md)、[评测](../../03-mechanisms/evaluation.md)
- 项目页：[unmute](unmute.md)
- 架构页：[全双工](../../02-architectures/full-duplex.md)、[S2S](../../02-architectures/s2s.md)
- 对比页：[model-matrix](../../05-comparison/model-matrix.md)
