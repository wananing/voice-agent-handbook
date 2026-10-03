# 评测：延迟、judge、多轮脚本（evaluation）

手册：03-mechanisms/evaluation.md

## 问题

语音 agent 要同时测"快"和"对"。快：延迟跨设备、网络、判停、ASR、LLM、TTS、播放缓冲，起点终点和统计口径不统一时两个"首音 P50 1.0 s"能差几百毫秒，提示语还能让有声音冒充有内容。对：质量依赖多轮上下文、工具、打断时序和声学条件，单句文本测不到；LLM 不确定性让单次运行结论不可靠。不解决就无法判断换 prompt / 换 ASR 是变好还是变坏，线上问题定位不到判停、识别、决策还是 TTS。实践笔记数字：规则在训练句上 100%、在没见过的句子上 54%，只有冻结盲测集能暴露。

## 解法分类

**1. 延迟口径**
- 起点三个锚点，必须写明用哪个：
  - T_ae：用户音频结束，不含尾部静音，所有模式都有定义，适合离线基准和跨方案对比（qwen-audio-agent bench 用这个）。
  - T_rel：松键，按键产品的用户感知起点；T_rel − T_ae 是用户尾部静音，单独监控（`ptt_tail_silence_ms`）。
  - T_ep：判停，开放麦常用；判停段 T_ep − T_ae 单独计、单独设预算，不能藏进起点。
- 终点：有声音 vs 有内容；计时点"送出网关"或"设备播放回执"（后者多设备缓冲和下行）。有内容按用户能听到的时刻算：正文早就绪但排在长提示语后，用户感知仍是提示语播完之后。
- 统计：报 P50、P90（容量测试另报 P95），不报平均。每个分位数附 n 和失败率。实践笔记规范：P50 每组 n ≥ 20，P90 每组 n ≥ 50；不够照样给数，标"样本不足"，不进门禁。
- 分段：stt / vad（判停 + 冲刷）/ llm / tts_start（unmute 拆法）；工具回合再拆工具前 / 工具后（qwen bench 拆法）。

**2. 单项评测**
- ASR：CER（中文统一归一化口径）、RTF、流式首字、STOP 后定稿延迟。FireRedASR `wer.py`（`--do_tn`）、SenseVoice `normalize_zh`、FunASR `realtime_ws_benchmark.md`。
- TTS：合成后 ASR 回转算 CER / WER、说话人相似度 SS、首包 P50、RTF。CosyVoice / Spark-TTS Triton `client_grpc.py`；公开集 Seed-TTS eval、CV3-Eval。
- VAD / 判停：帧级 PR 曲线、判停准确率、推理延迟。ten-vad `plot_pr_curves.py`；smart-turn `benchmark.py`。
- 前处理：降噪 / AEC 前后识别率、误切段率。APM `examples/run-offline.cpp` 离线 A/B。
- 代价：低、可自动；各仓库口径不一，不能直接横比。

**3. 行为和对话评测**
- 单轮断言：进程内注入文本、按事件序列断言（工具、参数、handoff）。LiveKit `AgentSession.run` + `RunResult`。成本很低，测不到时序和声学。
- 多轮脚本：每轮用户话 + 期望事件，`send_after` 锚定事件后插话，`absent` + `within_ms` 断言时限内不应出现。`pipecat eval`。
- 模拟用户：LLM persona + goal，judge 读整段。Pipecat `persona:`、LiveKit `SimulationContext`、tau2-bench。代价：有漂移；persona 说话太完整太礼貌，绝对分不可信。
- judge：整段多维（LiveKit `JudgeGroup` 8 个内置）或逐轮二元（Pipecat yes / no / continue，对转写错误宽容）。代价：中文、儿童口语、同音字误判；太小的模型把"我查一下"判成已回答。
- 模态：文本快且确定；音频用 TTS 合成用户话、ASR 转写 bot 话（Pipecat Kokoro + Moonshine），能测识别和发音但慢，合成音色不像儿童或真实口音时偏乐观。

**4. 分层评测**（实践笔记做法）

| 层 | 测什么 | 怎么跑 | 门禁 |
|---|---|---|---|
| L0 决策 | 给定文本和状态，动作 / 工具 / 参数 / 终态 | 进程内 mock 工具，几百条，毫秒级 | 每次提交 |
| L1 对话 | 多轮脚本：状态、指代、打断、重连、不支持请求、延迟预算 | 模拟客户端 + 模拟后端 + 真上游，文本和音频，`--repeat N` 出通过率 | 发布前 |
| L2 线上只读 | 真实会话离线跑同一套逐轮标准 | 会话结束时跑，禁写操作，低分进归因队列 | 持续 |
| 音频层 | ASR、判停、TTS 在真实声学下 | 合成或录音 + 噪声注入（SNR 20 / 10 / 5 dB）+ 真链路 | 换 ASR、TTS、VAD 时必跑 |

L0 核心是冻结盲测集：从未用来调参的句子，每个方案在同一组上比，每句配 2–3 个文本层 ASR 噪声变体（同音字、截断、叠词）。指标分开命名（动作命中、槽位命中、无工具轮正确率、写操作误触率、终态一致率），不合成一个"准确率"。

L1 脚本示意（格式借 Pipecat eval）：

```yaml
id: interrupt_during_tool_reply
modality: [text, audio]
race: true
repeat: 10
turns:
  - user: "下一站怎么走"
    expect:
      - {event: function_call, name: get_route, args_subset: {to: 海洋馆}}
      - {event: sound_first, within_ms: 900}
      - {event: content_first, within_ms: 1100}
  - user: "等等，厕所在哪"
    send_after: {event: content_first, delay_ms: 1500}
    expect:
      - {event: bot_interrupted}
  - user: "嗯"
    expect:
      - {event: response, absent: true, within_ms: 3000}
```

**5. trace**：每轮一条，固定 schema，每帧每条记录带 `turn_id`（迟到事件、打断、重连归到正确回合）。字段：三个锚点、有声音 / 有内容、各分段、工具前后、`queue_wait_ms`、是否被打断、终态、`rebuild_reason`、上游用量与缓存命中、`prompt_version`。导出为每回合一个 OTel span，usage 沿用 `gen_ai.usage.*`，可接 LiveKit / Pipecat 现成看板。

**6. 人工听**：音色、韵律、读错字、语气机械、打断是否干脆、回声和降噪伪影、judge 校准。最贵，不可扩展。

常见失败：竞态用例（打断、异步工具、判停）只跑一次不说明问题，没有通过率的竞态脚本不进门禁；口径混算（按键与开放麦、送出与回执、不同时钟质量）的分位数没有意义；只算平均、约 5 个样本的测试（xiaozhi `performance_tester.py`）只能当冒烟。

## 推荐

**延迟口径**：离线基准、跨方案对比一律从 T_ae 起算。线上看板按产品形态选主锚点：按键用松键，开放麦用判停，另外两个能测就记。有声音和有内容分开报 P50 / P90，附 n 和失败率。每个数字写成"有内容 P50 1.05 s（T_rel，n=40）"。

**自动跑**：
- 每次提交：L0 冻结盲测集（原句 + ASR 噪声变体 + 写操作负例）。
- 换 ASR、TTS、VAD 时：同一归一化口径下的 CER、TTS 回转 CER 和首包 P50 / P90、VAD PR 曲线、判停准确率。
- 发布前：L1 多轮脚本，文本和音频两种模态，竞态脚本 `--repeat ≥ 10` 按通过率进门禁。必备脚本组：插话打断、工具各阶段的打断、空转写和噪声不回应、不支持的请求、断线重连、指代和早期信息召回。
- 线上：每轮一条 trace，带 `turn_id` 和版本号；L2 离线跑 judge，低分轮次自动进归因队列。

**工具选型**：自写轻量 runner，格式借 Pipecat（`send_after`、`absent`、`within_ms`、`--repeat`），断言借 LiveKit（事件序列 + `mock_tools`），用例字段借 qwen bench（`expect_no_tool`、`forbidden_calls`、`expected_final_state`）。已经用 LiveKit 或 Pipecat 就用它们自带的那套，不要为了评测换框架。

**只能人工听**：音色和韵律、多音字和数字读法（回转 CER 只抓一部分）、打断是否干脆、回声和降噪伪影、目标人群真实录音。judge 定期人工校准：抽 judge 判 yes 的轮次复核，不只看 no。

**模拟用户**只找回归，不报绝对分。儿童、老人等目标人群以真人录音冻结集为准。

共同缺口（开源仓库都没有，要自己补）：音频层噪声 / SNR 注入、TTS 发音评测、儿童语音样本、判停准确率和端到端首音基准。

## 各项目怎么做

- livekit-agents：进程内单轮测试 + 事件断言 + 单条 judge；`mock_tools`；整段 `JudgeGroup`（可挂生产会话结束时跑）；文本 / 音频仿真；示例评测在 push / PR 进 CI。`voice/run_result.py:RunResult`、`evals/judge.py`、`evals/evaluation.py:JudgeGroup`、`simulation.py:SimulationContext`、`testing.py:fake_job_context`。
- pipecat：`pipecat eval` 以 RTVI 客户端驱动 bot，`turns:` 脚本和 `persona:` 模拟用户，断言 `function_call`、`bot_interrupted`、`within_ms`、`absent`、`send_after`，`--repeat` 出通过率；发布前手动跑。`src/pipecat/evals/`、`evals/release/`、`observers/user_bot_latency_observer.py`。
- qwen-audio-agent：smart-cockpit bench（JSONL、参数规则归一化匹配、`expected_final_state`，T_ae 起算、分工具前 / 后）；customer-service 接 tau2-bench；本地 WebSocket 模拟各厂商协议。`examples/smart-cockpit/bench/`、`examples/customer-service/benchmark/`、`server/test/realtime-provider-behavior.test.mjs`。
- ten-framework：扩展级单测和 ASR / TTS 协议一致性测试（如 TTS flush 后不得再出音频）。`integration_tests/asr_guarder`、`integration_tests/tts_guarder`。
- unmute：压测客户端算 stt / vad / llm / tts_start 分段和 TTS 实时率；Prometheus + Grafana。`loadtest_result.py`、`unmute/metrics.py`。
- smart-turn：分语言、分语气词准确率、混淆矩阵、延迟 P50 / P90。`benchmark.py`。
- ten-vad：30 段人工标注 + PR 曲线，可换自己的数据。`examples/plot_pr_curves.py`。
- fireredasr：可复用 CER / WER 打分脚本，适合当 ASR 选型尺子。`fireredasr/utils/wer.py`。

## 相关

- 架构：02-architectures/turn-model.md（`turn_id` 贯穿每帧、终态进 trace）、02-architectures/state-and-context.md（版本进 trace）、02-architectures/cascade.md、02-architectures/half-cascade.md、02-architectures/s2s.md
- 基础：01-foundations/latency-budget.md（1.1 节两种首音）、01-foundations/asr.md、01-foundations/tts.md、01-foundations/vad.md
- 机制：03-mechanisms/first-audio.md、03-mechanisms/turn-detection.md、03-mechanisms/interruption.md、03-mechanisms/tool-calls.md、03-mechanisms/session-recovery.md、03-mechanisms/audio-preprocessing.md
