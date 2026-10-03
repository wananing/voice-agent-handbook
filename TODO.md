# 待修的页面间不一致

写 skill 参考文件时发现的，按影响排序。修一条删一条。

1. **livekit 级联的截断精度**。05-comparison/decision-guide.md §2.1 叶子 [B] 写"按 TTS 词级时间戳截断"并推荐 livekit-agents；03-mechanisms/interruption.md 和 05-comparison/framework-matrix.md 写 livekit 级联按匀速估算（`synchronized_transcript`，中文按字切），只有 pipecat 和 unmute 有词级时间戳。决策页要改。
2. **qwen-audio-agent 的历史恢复归类**。02-architectures/state-and-context.md §6 归为"建会话后插入"（实测 0/6 的那类）；03-mechanisms/session-recovery.md 单列为第五种"状态进 instructions、历史在建连时一次注入"，更接近"建会话时带入"（6/6）。两页要统一，这决定评审时算不算反模式。
3. **推荐框架与推荐同步策略冲突**。03-mechanisms/session-recovery.md 推荐"只同步工具结果，不做全量 diff"，但多个配方推荐的 livekit-agents 用的是全量镜像 diff，重连时重放镜像，而 session-recovery 页把重放镜像列为反模式。要在配方或 session-recovery 页里说明怎么处理。
4. **"只有 CosyVoice 支持文本流式输入"的范围**。05-comparison/decision-guide.md §3.4 这么说，但 03-mechanisms/first-audio.md 也列了 Kyutai TTS（经 unmute）支持。应限定为"六个自托管 TTS 里"。
5. **Step-Audio 2 显存说法三种**。02-architectures/half-cascade.md 说 24 GB 单卡"大概够（推算）"；05-comparison/model-matrix.md 说待确认；05-comparison/decision-guide.md 说"24 到 80 GB 级"。统一成待确认加推算依据。
6. **竞态脚本重复次数**。03-mechanisms/evaluation.md 和决策页写 `--repeat ≥ 10`，原始 specs 02 和 05 写每条断言至少 20 次。取 20 或说明为什么 10。
7. **无屏设备静默阈值口径**。02-architectures/turn-model.md 提交后 2 s 播填充语；03-mechanisms/first-audio.md 说静默超过 2 s 算故障；specs/01 验收允许最多 2.5 s。不冲突，但三个数不在一个基准上，加一句说明。
8. **配方 3.6 自相矛盾**。05-comparison/decision-guide.md 配方 3.6 的首音一栏说工具回合播本地提示语，已知坑又说本地提示语和原生声音听起来像两个人，没给缓解办法（同音色预合成，或改半级联）。
