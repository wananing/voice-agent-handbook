# 评测：延迟、judge、多轮脚本

> 状态：草稿
> 最后更新：2026-10-02

## 问题是什么

语音 agent 要同时满足"快"和"对"，两者都比文本 agent 难测：

- **快**：一轮的延迟跨了设备、网络、判停、ASR、LLM、TTS、播放缓冲好几段（见 [latency-budget](../01-foundations/latency-budget.md)）。起点、终点、统计方法不统一的话，两个"首音 P50 1.0 s"可能差出几百毫秒，提示语还能把"有声音"提前，冒充"有内容"。
- **对**：回复质量依赖多轮上下文、工具调用、打断时序和声学条件。单句文本测试测不到打断、重连、噪声和儿童语音；LLM 的不确定性又让单次运行的结论不可靠。

不解决会怎样：换一版 prompt、换一个 ASR，没法判断是变好还是变坏；线上问题没法定位到是判停、识别、决策还是 TTS 出的错；规则在训练句上 100%、在没见过的句子上只有 54%（一份实践笔记的数字），这种差距只有冻结的盲测集能暴露。

## 解法分类

**1. 延迟口径**

- **起点**有三个锚点，用哪个必须写明：
  - **T_ae**：用户音频流结束，不含尾部静音。在所有模式下都有定义，适合离线基准和跨方案对比（qwen-audio-agent 的 bench 用这个口径）。
  - **T_rel**：松键。按键说话产品的用户感知起点；T_rel − T_ae 是用户自己的尾部静音，要单独监控。
  - **T_ep**：判停。开放麦产品常用这个点；判停段 T_ep − T_ae 要单独计、单独设预算，不能藏进起点里。
- **终点**分两种首音（定义见 [latency-budget](../01-foundations/latency-budget.md) 第 1.1 节）：**有声音**（包括"我查一下"这类提示语）和**有内容**（回答本身的第一段）。计时点有"送出网关"和"设备播放回执"两种口径，后者多了设备缓冲和下行。
- **统计**：报 P50 和 P90，容量测试另报 P95，不报平均值。每个分位数附有效样本数 n 和失败率。一份实践笔记的规范是：P50 每组 n ≥ 20，P90 每组 n ≥ 50；n 不够时照样给数，但标"样本不足"，不用于门禁。
- **分段**：首音拆成 stt / vad（判停 + 冲刷）/ llm / tts_start 几段（unmute 压测的拆法）；工具回合再拆"工具前"和"工具后"两段（qwen-audio-agent bench 的拆法）。

**2. 单项评测**

| 对象 | 指标 | 开源里能直接用的 |
|---|---|---|
| ASR | CER（中文要统一归一化口径）、RTF、流式首字、STOP 后定稿延迟 | FireRedASR `wer.py`（`--do_tn` 数字归一）；SenseVoice `normalize_zh` 口径；FunASR `realtime_ws_benchmark.md` |
| TTS | 合成后用 ASR 转写回来算 CER / WER，说话人相似度 SS，首包 P50，RTF | CosyVoice / Spark-TTS 的 Triton `client_grpc.py` 测首块延迟；公开集是 Seed-TTS eval、CV3-Eval |
| VAD / 判停 | 帧级 PR 曲线、判停准确率、推理延迟 | ten-vad `plot_pr_curves.py`；smart-turn `benchmark.py`（分语言准确率、混淆矩阵、延迟 P50 / P90） |
| 前处理 | 降噪、AEC 前后的识别率、误切段率 | APM `examples/run-offline.cpp` 可对录好的播放 / 采集文件做离线 A/B；其余没有 |

**3. 行为和对话评测**

- **单轮断言**：进程内注入文本，按事件序列断言（有没有调工具、参数对不对、有没有 handoff），可以用 LLM 判单条回复。代表是 LiveKit 的 `AgentSession.run` + `RunResult`。
- **多轮脚本**：每轮写好用户话和期望事件，能锚定某个事件之后插话（`send_after`），能断言"时限内不应出现"（`absent` + `within_ms`）。代表是 `pipecat eval`。
- **模拟用户**：LLM 扮演带目标的用户（persona + goal），judge 读整段对话。Pipecat `persona:`、LiveKit `SimulationContext`、qwen-audio-agent 接的 tau2-bench。
- **judge**：整段对话的多维 judge（LiveKit 内置任务完成、准确性、工具使用、简洁等 8 个），或逐轮二元 judge（Pipecat：条件式写法，"还没答完"单独一个结论 `continue`，对转写错误宽容）。
- **文本模态 vs 音频模态**：文本快且确定；音频模态用 TTS 合成用户话、ASR 转写 bot 的话再给 judge（Pipecat 用 Kokoro + Moonshine，qwen 用 macOS `say`），能测到识别和发音问题，但慢，而且合成音色不像真实用户。

**4. 分层评测**（一份实践笔记的做法）

| 层 | 测什么 | 怎么跑 | 门禁 |
|---|---|---|---|
| L0 决策 | 给定文本和状态，动作 / 工具 / 参数 / 终态对不对 | 进程内，mock 工具，几百条，毫秒级 | 每次提交 |
| L1 对话 | 多轮脚本：状态变化、指代、打断、重连、不支持的请求、延迟预算 | 模拟客户端 + 模拟后端 + 真上游，文本和音频两种模态，`--repeat N` 出通过率 | 发布前 |
| L2 线上只读 | 对真实会话离线跑同一套逐轮标准 | 会话结束时跑，禁止写操作，低分轮次进待归因队列 | 持续，看板 + 告警 |
| 音频层 | ASR、判停、TTS 在真实声学条件下的表现 | 合成或录音 + 噪声注入（SNR 20 / 10 / 5 dB）+ 真链路 | 换 ASR、TTS、VAD 时必跑 |

一条 L1 脚本大致长这样（格式借 Pipecat eval，断言是该实践补的）：

```yaml
id: interrupt_during_tool_reply
modality: [text, audio]
race: true            # 竞态脚本，只按通过率进门禁
repeat: 10
turns:
  - user: "下一站怎么走"
    expect:
      - {event: function_call, name: get_route, args_subset: {to: 海洋馆}}
      - {event: sound_first, within_ms: 900}      # 从主锚点起算
      - {event: content_first, within_ms: 1100}
  - user: "等等，厕所在哪"
    send_after: {event: content_first, delay_ms: 1500}   # 正文播放中插话
    expect:
      - {event: bot_interrupted}
      - {event: response, eval: [understanding, honesty]}
  - user: "嗯"
    expect:
      - {event: response, absent: true, within_ms: 3000} # 附和语不回应
```

L0 的核心是**冻结盲测集**：一组从未用来调参的句子，每个方案都在同一组上比，每句再配 2–3 个文本层 ASR 噪声变体（同音字、截断、叠词）。指标分开命名（动作命中、槽位命中、无工具轮正确率、写操作误触率、终态一致率），不合成一个"准确率"。

**5. trace 与可观测**

- **每轮一条 trace**，字段固定，所有链路共用一个 schema。每帧和每条记录都带 `turn_id`，这样迟到事件、打断和重连都能归到正确的回合（见 [turn-model](../02-architectures/turn-model.md)）。
- **阶段打点**：三个锚点、有声音 / 有内容、各分段、工具前后、排队等待、是否被打断、终态、`rebuild_reason`、上游用量与缓存命中。
- **版本进 trace**：人设、工具表、状态块的版本号写进 `prompt_version`，换一版不用改门禁，按版本分批比较。
- **一个工具回合的打点示例**（按键产品，主锚点松键；数字为示意，来自一份实践笔记的规范）：

```
T_ae        -180   用户说完（松键前 180 ms 静音 → ptt_tail_silence_ms=180，不算系统延迟）
T_rel = T0     0
stt_final    370
fc_received  800   ← 工具前 ≈ 805
tool_done    815   工具执行 10 ms
cue 有声音    830   sound_first_at_ms=830
正文就绪     1060   content_first_ready_at_ms
cue 播完     1590   queue_wait_ms=540（正文排在提示语后面）
有内容       1600   content_first_at_ms=1600
```

  这个例子说明"有内容"按用户能听到的时刻算：正文早就准备好了，但排在长提示语后面，用户感知仍是 1.6 s。

- **开源里现成的打点**：Pipecat `user_bot_latency_observer.py` 测"用户停说 → bot 出声"，开 `enable_metrics` 后再按各服务 TTFB、文本聚合、函数调用拆段，没人认领的空档也单列；unmute `metrics.py` 有 STT / TTS / vLLM 各自的 TTFT；LiveKit `metrics/` 和 OTel。
- **导出**：OTel span 每回合一个，usage 沿用 `gen_ai.usage.*`，这样能直接接 LiveKit / Pipecat 的现成看板。

**6. 人工听**：音色、韵律、读错字、"语气是不是太机械"、打断是否干脆、回声和降噪伪影，以及 judge 自身的校准。

## 各解法的代价

| 做法 | 成本 | 能发现什么 | 发现不了什么 |
|---|---|---|---|
| 单项 CER / 首包 | 低，可自动 | 模型选型、回归 | 端到端体验；数字口径各仓库不一，不能直接横比 |
| L0 进程内决策 | 很低，毫秒级 | 决策、参数、写操作误触 | 时序、声学、多轮漂移 |
| 多轮脚本（文本） | 中，每次调用真上游 | 指代、状态、打断、空转写不回应 | ASR 错误、发音、真实延迟 |
| 多轮脚本（音频） | 高，慢 | 识别、判停、端到端延迟 | 合成音色不像儿童或真实口音时，结果偏乐观 |
| 模拟用户 | 高，而且有漂移：同一任务每次对话都不同 | 回归、恢复能力 | 绝对分数不可信：LLM persona 说话太完整、太礼貌 |
| LLM judge | 中 | 规模化打分 | judge 自身偏差：中文、儿童口语、同音字会误判；太小的模型会把"我查一下"判成已回答 |
| 线上 trace + L2 | 需要基础设施 | 真实分布、长尾 | 只读，不能重放写操作 |
| 人工听 | 最贵，不可扩展 | 音质、韵律、语气、打断手感 | 统计显著性 |

两个常见的失败模式：

- **竞态用例单次运行不说明问题**。打断、异步工具、判停这类有竞态的东西，单次运行只能说明这次过没过。Pipecat 的 README 原话是 `--repeat` 才能说明多常过。没有通过率的竞态脚本不进门禁。
- **口径混算**。按键和开放麦、送出和播放回执、不同时钟质量的数据混在一起算分位数，结果没有意义。只算平均值、样本 5 次左右的测试（xiaozhi-esp32-server 的 `performance_tester.py`）只能当冒烟用。

## 各项目怎么做

| 项目 | 做法 | 代码路径 | 备注 |
|---|---|---|---|
| [livekit-agents](../04-projects/frameworks/livekit-agents.md) | 进程内单轮测试 + 事件断言 + 单条回复 judge；`mock_tools` 替换工具；整段对话 `JudgeGroup`（8 个内置 judge，可挂在生产会话结束时跑）；文本 / 音频两种仿真 | `voice/run_result.py:RunResult`、`evals/judge.py`、`evals/evaluation.py:JudgeGroup`、`simulation.py:SimulationContext`、`testing.py:fake_job_context` | 示例评测在 push / PR 时进 CI；文本层 ASR 噪声靠预置错误转写的历史；没有音频噪声注入、判停准确率或首音基准；运行期 `metrics/`、OTel |
| [pipecat](../04-projects/frameworks/pipecat.md) | `pipecat eval` 以 RTVI 客户端身份驱动 bot：脚本（`turns:`）和模拟用户（`persona:`），断言 `function_call`、`bot_interrupted`、`within_ms`、`absent`、`send_after`；文本或音频模态；`--repeat` 出通过率 | `src/pipecat/evals/`、`evals/release/`、`evals/turn-completion/`、`tests/utils.py:run_test`、`observers/user_bot_latency_observer.py` | release 场景集是发布前手动跑，CI 里未见自动跑 `pipecat eval`；音频模式的 Kokoro / Moonshine 能否换中文模型待确认 |
| [pipecat-flows](../04-projects/frameworks/pipecat-flows.md) | 用 `pipecat eval` 对 8 个示例跑行为评测，断言函数触发、参数、回复内容；judge 用本地 Ollama | `evals/README.md` | 默认文本模态，可切音频；发布前手动跑 |
| [qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md) | smart-cockpit bench：JSONL 用例、规则归一化参数匹配、`expected_final_state`，延迟从用户音频结束（不含尾部静音）起算并分工具前 / 后；customer-service 接 tau2-bench；provider 行为测试用本地 WebSocket 模拟各厂商协议 | `examples/smart-cockpit/bench/`、`examples/customer-service/benchmark/`、`server/test/realtime-provider-behavior.test.mjs` | 前台直连 vs 后台委派的工具时延均值 1.317 s vs 3.363 s；EVA Airline 44% / 62% / 68% 是纯文本评测；`response_quality` 默认不评；没有判停准确率和首音端到端评测 |
| [ten-framework](../04-projects/frameworks/ten-framework.md) | 扩展级单测 `AsyncExtensionTester`；ASR / TTS 扩展的协议一致性测试（如 TTS flush 之后不得再出音频） | `integration_tests/asr_guarder`、`integration_tests/tts_guarder` | 没有对话质量 judge、判停准确率或端到端延迟基准 |
| [unmute](../04-projects/full-duplex/unmute.md) | 压测客户端算 stt / vad / llm / tts_start 分段延迟和 TTS 实时率；运行期 Prometheus 指标和 Grafana 面板 | `loadtest_result.py`、`unmute/metrics.py` | 没有对话质量或判停评测；没有公开端到端数字 |
| [moshi](../04-projects/full-duplex/moshi.md) | 只有按步计时的性能基准和单元测试 | `scripts/moshi_benchmark.py`、`moshi/tests/test_lm.py` | 模型层评测在论文里 |
| [xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md) | 测各 provider 耗时（ASR 整段 / 首字、LLM 首 token、TTS 首包）；provider 契约测试 | `performance_tester.py`、`tests/` | 只算平均值，测试音频是系统提示音，不测识别准确率；没有端到端对话评测 |
| [xiaozhi-esp32](../04-projects/devices/xiaozhi-esp32.md) | 只有调试工具：UDP 把音频发到调试服务器；配网状态下的音频回环测试 | `main/audio/audio_debugger.cc` | 不涉及评测 |
| [openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md) | 没有测试和评测集；调试靠 UI 事件日志、转写和录音下载 | `hooks/useAudioDownload.ts` | — |
| [ultravox](../04-projects/e2e-models/ultravox.md) | 完整评测框架：ASR / 翻译（CoVoST2、FLEURS、CommonVoice）和对话能力（VoiceBench），BLEU / WER 和 GPT 评分器 | `ultravox/evaluation/`、`eval_config_voicebench.yaml` | README 没有给数字 |
| [qwen3-omni](../04-projects/e2e-models/qwen3-omni.md) | README 有大量 benchmark 表，仓库不含评测代码 | `README.md` | 中文 ASR：WenetSpeech net / meeting 4.69 / 5.89 |
| [step-audio2](../04-projects/e2e-models/step-audio2.md) | README 有自家评测集和多张表，不含评测代码 | `README.md` | 工具调用表里没有 mini 的数字 |
| [fireredasr](../04-projects/asr/fireredasr.md) | 可复用的 CER / WER 打分脚本；`transcribe` 输出 rtf | `fireredasr/utils/wer.py` | 适合当 ASR 选型时的准确率上限尺子 |
| [funasr](../04-projects/asr/funasr.md) | 离线 RTF / CER 报告；实时服务的首次更新延迟、STOP 后定稿延迟、多客户端基准 | `docs/benchmark/realtime_ws_benchmark.md`、`docs/benchmark/rtf_reproducibility.md` | 2pass C++ 服务没有对应延迟基准，待确认 |
| [sensevoice](../04-projects/asr/sensevoice.md) | CER（micro，`normalize_zh`）和 CPU 实时倍数；情感识别评测契约 | `runtime/llama.cpp/BENCHMARKS.md`、`benchmarks/ser/` | 没有流式延迟评测 |
| [sherpa-onnx](../04-projects/asr/sherpa-onnx.md) | 仓库不提供 CER 或延迟基准 | — | 精度看各模型来源，待确认 |
| [cosyvoice](../04-projects/tts/cosyvoice.md) | CER / WER 和 SS 表；发布 CV3-Eval 评测集；Triton 客户端测首块 | `client_grpc.py` | 说话人缓存把首块 P50 从 218 ms 降到 185 ms（仓库自述） |
| [spark-tts](../04-projects/tts/spark-tts.md) | Triton 客户端在 Seed-TTS 数据集上测首块延迟和 RTF | `runtime/triton_trtllm/client_grpc.py` | 首块 P50 210 ms（并发 1，仓库自述）；没有质量评测表 |
| [voxcpm](../04-projects/tts/voxcpm.md) | README 有 Seed-TTS-eval、CV3-eval 表 | `README_zh.md:400-450` | 仓库内无评测脚本；没有首包数字 |
| [index-tts](../04-projects/tts/index-tts.md) | CV3-Eval WER / SS 表和 RTX 4090 上的 RTF 表；回归测试 | `tests/regression_test.py` | 回归测试不是质量评测 |
| [fish-speech](../04-projects/tts/fish-speech.md) | README 给 Seed-TTS Eval、Audio Turing Test 等结果 | `README.md:90-105` | 仓库内无评测脚本 |
| [fireredtts2](../04-projects/tts/fireredtts2.md) | 只定性描述，没有表和脚本 | — | 第三方数字差别很大，待确认 |
| [smart-turn](../04-projects/turn-vad/smart-turn.md) | HF 测试集上的准确率、混淆矩阵、分语言和分语气词指标，以及延迟 P50 / P90 | `benchmark.py` | 没有对话级评测 |
| [ten-vad](../04-projects/turn-vad/ten-vad.md) | 30 段人工标注音频 + PR 曲线脚本，可和 Silero 对比 | `examples/plot_pr_curves.py` | 可以换成自己的数据复用 |
| [aiortc](../04-projects/audio-processing/aiortc.md) | 单元测试；RTCP 统计可用于线上监控抖动和丢包 | `tests/test_jitterbuffer.py` | 不涉及 agent 评测 |
| [webrtc-audio-processing](../04-projects/audio-processing/webrtc-audio-processing.md) | 没有评测脚本；离线示例可做播放 / 采集 A/B | `examples/run-offline.cpp` | 上游单元测试在拆包时被去掉 |
| [rnnoise](../04-projects/audio-processing/rnnoise.md) | 没有评测脚本或客观指标 | — | 只有在线 demo |

**框架自带工具对比**：

| | LiveKit | Pipecat | qwen-audio-agent |
|---|---|---|---|
| 驱动 | 进程内 `session.run` | 独立进程，RTVI 客户端 | 自研 runner |
| 用户输入 | 文本；预置错误转写的历史 | 文本 / 合成语音 / 录音；LLM persona | 文本 / 合成语音；tau2 模拟器 |
| 回复判定 | 单条 judge（不看前文）；整段 `JudgeGroup` | 逐轮二元 judge（带前文，yes / no / continue） | rubric 字段，默认不评 |
| 打断 / 时序 | 无（judge 转录里标 `[interrupted]`） | `send_after`、`bot_interrupted`、`within_ms`、`absent` | 延迟口径定义 |
| 波动处理 | 可选路径、重跑 | `--repeat` 出通过率 | 只在"超时且无调用无文字"时重试一次 |
| CI | GitHub Actions，push / PR | 发布前手动 | 手动 |

**共同缺口**：开源仓库里都没有音频层的噪声 / SNR 注入、TTS 发音评测和儿童语音样本，判停准确率和端到端首音的基准也很少。

## 我们的判断

**延迟口径**：离线基准、跨方案对比一律从 T_ae（用户音频结束，不含尾部静音）起算。线上看板按产品形态选主锚点：按键产品用松键，开放麦用判停，同时把另外两个锚点能测到的都记下来。有声音和有内容分开报，报 P50 / P90 并附 n 和失败率。每个数字写成"有内容 P50 1.05 s（T_rel，n=40）"这样，带上锚点和样本数。

**自动跑的**：

- 每次提交跑 L0 冻结盲测集，包括原句和 ASR 噪声变体，以及写操作负例。
- 换 ASR、TTS、VAD 时跑单项评测：同一归一化口径下的 CER、TTS 回转 CER 和首包 P50 / P90、VAD PR 曲线、判停准确率。
- 发布前跑 L1 多轮脚本，文本和音频两种模态，竞态脚本 `--repeat ≥ 10` 按通过率进门禁。必备脚本组：插话打断、工具各阶段的打断、空转写和噪声不回应、不支持的请求、断线重连、指代和早期信息召回。
- 线上每轮一条 trace，带 `turn_id` 和版本号。L2 对真实会话离线跑 judge，低分轮次自动进归因队列。

**工具选型**：自己写一套轻量 runner，格式借 Pipecat（`send_after`、`absent`、`within_ms`、`--repeat`），断言借 LiveKit（事件序列 + `mock_tools`），用例字段借 qwen bench（`expect_no_tool`、`forbidden_calls`、`expected_final_state`）。直接用 LiveKit 或 Pipecat 时，就用它们自带的那套，不要为了评测换框架。

**只能人工听的**：音色和韵律、多音字和数字读法（自动回转 CER 只能抓一部分）、打断是否干脆、回声和降噪伪影、儿童或目标人群的真实录音。另外，judge 本身要定期人工校准：抽 judge 判 yes 的轮次复核，不只看 no。

**模拟用户**只用来找回归，不报绝对分。儿童、老人这类目标人群的效果，以真人录音冻结集为准。

## 相关

- 架构层：[turn-model](../02-architectures/turn-model.md)（`turn_id` 贯穿每帧、回合终态进 trace）、[state-and-context](../02-architectures/state-and-context.md)（状态块、人设版本进 trace）、[cascade](../02-architectures/cascade.md)、[half-cascade](../02-architectures/half-cascade.md)、[s2s](../02-architectures/s2s.md)
- 其他机制：[first-audio](first-audio.md)（有声音 / 有内容）、[turn-detection](turn-detection.md)（判停准确率）、[interruption](interruption.md)（打断脚本）、[tool-calls](tool-calls.md)（工具前 / 后分段）、[session-recovery](session-recovery.md)（重连脚本、`rebuild_reason`）、[audio-preprocessing](audio-preprocessing.md)（噪声注入）
- 基础：[latency-budget](../01-foundations/latency-budget.md)、[asr](../01-foundations/asr.md)、[tts](../01-foundations/tts.md)、[vad](../01-foundations/vad.md)
- 项目页：[livekit-agents](../04-projects/frameworks/livekit-agents.md)、[pipecat](../04-projects/frameworks/pipecat.md)、[qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md)、[unmute](../04-projects/full-duplex/unmute.md)、[smart-turn](../04-projects/turn-vad/smart-turn.md)、[fireredasr](../04-projects/asr/fireredasr.md)
