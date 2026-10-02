# ASR：流式与离线、热词、时间戳

> 状态：草稿
> 最后更新：2026-10-01

这一页讲 agent 视角下的 ASR：结果什么时候到、到的是什么、哪些地方会错。模型结构（CTC / transducer / Paraformer / LLM-ASR）只在影响这几个问题时提到。

---

## 1. 核心概念

### 1.1 流式、离线、伪流式、2pass

| 形态 | 怎么工作 | 例子 |
|---|---|---|
| 真流式 | 音频按块进，模型带状态逐块解码，持续输出 partial | FunASR 在线 Paraformer：600 ms 一块，回看 / 前瞻各 300 ms（`chunk_size=[5,10,5]`）；sherpa-onnx 在线 paraformer：610 ms 一块；zipformer 的块长由模型元数据决定 |
| 离线 | 整段音频一次前向出结果 | SenseVoiceSmall（非自回归编码器 + CTC）；离线 Paraformer-large（标注输入 ≤ 20 s） |
| 伪流式 | 用离线模型按固定间隔把"到目前为止的整段"重解一遍，和上次结果做 diff | SenseVoice 官方 server 默认每 400 ms 重解；sherpa-onnx 的 SenseVoice 示例每 0.2 s 重解 |
| 2pass | 流式模型出 partial，段尾再用离线模型重识别一遍作为定稿 | FunASR `2pass` 模式：`2pass-online` 是 partial，`2pass-offline` 是纠错后的定稿 |
| 延迟流 | 输出文本固定比音频晚若干帧 | Kyutai STT（unmute 用）：文本比音频晚约 0.5 s，判停后要把这段尾巴"挤"出来 |

agent 里常见的取舍是：**partial 用流式，定稿用离线**。离线那一遍精度更高，而按键说话或判停之后的一句话通常只有几秒，离线重解的代价不大。

### 1.2 首字延迟与定稿延迟

两个延迟要分开看：

- **首字延迟**：用户开口 → 第一个 partial 出来。取决于块长和前瞻。FunASR 在线模型的 partial 不包含前瞻区的字，推算滞后约 0.6–0.9 s（待确认，未实测）。
- **定稿延迟**：用户说完（或松键、或判停）→ final 结果到达。**这一段直接在首音的关键路径上**，见 [latency-budget](latency-budget.md)。

定稿要"冲尾巴"：流式模型手里还有没解完的尾部音频，必须有一个结束信号把它挤出来。各家做法不同：

| 实现 | 结束时怎么冲刷 |
|---|---|
| FunASR runtime | 客户端发 `{"is_speaking": false}`，服务端自动冲尾巴，再跑离线纠错 + 标点 + ITN |
| sherpa-onnx | **要自己灌尾部静音**再 `InputFinished`：示例用 0.3 / 0.66 s，C++ websocket 服务端默认 `end_tail_padding` 0.8 s |
| SenseVoice server | 客户端 `commit`，整段编码一次 |
| Kyutai STT | 判停后补约 0.5 s 让延迟流吐完 |

灌进去的零帧处理得比实时快，但这段计算仍然算在定稿延迟里。

实测参考：一条 S2S 上游链路里，从"提交"到"识别完成"约 0.37 s（来自实战笔记的工具回合分解，单一样本口径）。自托管离线 ASR 对一句 3 s 短句的定稿推算在 0.1–0.3 s（待确认，依据是仓库给的 RTF，线程配置和单路服务口径不同）。

### 1.3 partial 与 final

| | partial | final |
|---|---|---|
| 稳定性 | 会被后面的结果改写 | 不再变 |
| 标点 / ITN | 一般没有（FunASR partial 不加标点，在线模式只在段尾加） | 有 |
| 热词 | 看实现，FunASR 的热词**只作用在离线那一遍**，partial 认不出专有名词 | 有 |
| agent 里的用途 | 回显给用户、抢先启动 LLM（投机生成）、检测用户开口以触发打断 | 进 LLM 的正式输入 |

一个容易忽略的点：**一次按键可能产生多条 final**。FunASR 2pass 的离线纠错是按服务端 VAD 段触发的，孩子句中停顿超过尾部静音阈值（代码默认 800 ms），一次按键就会收到多条 `2pass-offline`，客户端要自己拼。用 `mode: offline` 可以避开，代价是没有 partial。

### 1.4 热词（hotword / contextual biasing）

热词把一组词的识别概率往上抬，用来救专有名词：角色名、产品名、地名、景点名。

| 实现 | 做法 | 限制 |
|---|---|---|
| FunASR | fst 热词或 nn 热词（需要 contextual 模型）；客户端首包带 `hotwords`，或服务端 `--hotword` 文件 | 只进离线那一遍；建议不超过 1k 个、每个不超过 10 字 |
| sherpa-onnx | transducer + `modified_beam_search` 时生效，默认分数 1.5，可以按流设置 | 其他模型结构不支持；另有所有模型都能用的同音替换器 |
| SenseVoice | 官方没有，只有第三方版本 | — |

热词的代价是**误触发**：热词表越大、权重越高，普通说法被"纠"成热词的概率越高。上线前要同时测召回和误触发率。

### 1.5 时间戳

| 实现 | 粒度 | 条件 |
|---|---|---|
| FunASR | 字级 `[[start_ms, end_ms], ...]` | 只有离线那一遍、且用带时间戳的模型时才有 |
| SenseVoice（Python） | token 级，CTC 强制对齐，60 ms 粒度 | `output_timestamp=True`；C++ server 是否输出待确认 |
| sherpa-onnx | token 被解出的时刻（秒） | 只有 transducer / CTC 填这个字段 |

agent 里用时间戳做的事：

- 把转写和按键、播放时间线对齐（用户在 agent 说到哪一句时开口）；
- 判断某段识别是不是 agent 自己的回声；
- 统计句中停顿，作为判停或评测的输入。

### 1.6 儿童和噪声场景的错误模式

本手册分析的 ASR 仓库（FunASR、SenseVoice、sherpa-onnx）都**没有**儿童数据集、评测或专门参数。公开的 CER 都是成人普通话，跟儿童短句不是一个分布。实战里观察到的错误模式：

- **句中停顿被切段**：孩子停顿多，端点检测或服务端 VAD 把一句话切成两段，第二段可能只剩一个字。
- **一两个字的有效回答**："好的""不去"和咳嗽、语气词在文本上很难区分，"≤2 字当噪音"这类规则会误伤。
- **空结果和误触**：按键误触、只有噪声，返回空文本或几个无意义字。
- **小声、离麦远**：以音量做门槛的 VAD（例如 Pipecat 默认 `min_volume=0.6`）可能把孩子的声音判成非语音（待确认，需样本验证）。
- **噪声下的插入**：LLM-ASR 在无语音输入上可能生成内容（Fun-ASR-Nano 一类模型需要设重复惩罚，说明有重复倾向；属于推断，待确认）。

SenseVoice 会额外输出事件标签（含 `Cry`、`Laughter`），在儿童场景可能有用，准确率待确认。

### 1.7 中文 ASR 的特殊点

- **指标用 CER**（字错率），不是 WER。比较不同系统前要统一归一化口径（标点、全半角、数字写法）。
- **同音字是主要错误**。中文没有空格分词，专有名词错成同音字最常见，所以热词和同音替换比英文更重要。
- **标点和 ITN 是独立环节**：FunASR 用 ct-punc 加标点、FST 做 ITN（"三点五"→"3.5"）；sherpa-onnx 流式模型本身不出标点，ITN 靠 `rule_fsts`；SenseVoice 的 `use_itn` 同时控制两者。下游如果用规则匹配文本，要清楚拿到的是 ITN 前还是 ITN 后。
- **中英混说**：FunASR 支持中英，SenseVoice 支持中 / 粤 / 英 / 日 / 韩，sherpa-onnx 有中英双语模型。
- **语气词**：嗯、啊、呃、那个，有的模型会输出，有的会吞。它们对判停和"是不是噪音"的判断都有影响。

---

## 2. 对 agent 设计的影响

| 决策 | 跟 ASR 的哪个特性有关 | 去哪看 |
|---|---|---|
| 用不用自己的 ASR（级联）还是交给 S2S 上游 | 准确率、热词、可控性 vs 链路延迟 | [级联](../02-architectures/cascade.md)、[S2S](../02-architectures/s2s.md)、[半级联](../02-architectures/half-cascade.md) |
| 判停靠谁 | 很多 ASR 自带端点（FunASR FSMN-VAD 800 ms、sherpa 端点规则 1.2 / 2.4 s），会和你的判停打架 | [判停](../03-mechanisms/turn-detection.md) |
| 投机生成 | 基于 partial 启动 LLM，final 改写时要能撤销 | [首音优化](../03-mechanisms/first-audio.md) |
| 打断 | 用 partial 或 VAD 检测用户开口；用时间戳判断回声 | [打断与截断](../03-mechanisms/interruption.md) |
| 回合终态 | 识别失败、空结果、超时都要给设备一个明确的回合结束 | [回合模型](../02-architectures/turn-model.md) |
| 评测 | CER 口径、按年龄段和噪声条件分层、空结果率 | [评测](../03-mechanisms/evaluation.md) |

---

## 3. 常见坑

- **把 partial 当 final 用**。partial 会被改写，基于它做的写操作、工具调用要能撤销，或者干脆只用 final。
- **只拼最后一条 final**。2pass 或带端点的服务在一次按键里可能给出多条 final，漏拼就丢半句话。
- **忘了冲尾巴**。sherpa-onnx 不灌尾部静音，最后一两个字出不来；冲刷用的静音时长要算进定稿延迟。
- **ASR 内置端点和自己的判停同时开**。两套阈值互相切段，表现为"偶尔只识别出一个字"。
- **送错格式**：采样率、int16 / float32 不对，见 [audio-basics](audio-basics.md)。三家开源服务都不收 Opus，要先解码。
- **热词只在离线那遍生效**，却拿 partial 去做实体匹配。
- **单路演示服务当多路服务用**。SenseVoice server 用全局锁串行推理，默认 `--max-connections 4`，是给端侧和单机演示的。
- **拿成人长音频的 CER 推断儿童短句效果**。仓库里的公开数字（例如 184 条 44–60 s 成人普通话长音频）跟儿童按键短句不是一个分布，必须自己建样本集实测。

---

## 4. 相关页面

- 同层：[audio-basics](audio-basics.md)、[vad](vad.md)、[latency-budget](latency-budget.md)
- 架构：[级联](../02-architectures/cascade.md)、[半级联](../02-architectures/half-cascade.md)、[S2S](../02-architectures/s2s.md)、[回合模型](../02-architectures/turn-model.md)
- 机制：[判停](../03-mechanisms/turn-detection.md)、[首音优化](../03-mechanisms/first-audio.md)、[打断与截断](../03-mechanisms/interruption.md)、[音频前处理](../03-mechanisms/audio-preprocessing.md)、[评测](../03-mechanisms/evaluation.md)
