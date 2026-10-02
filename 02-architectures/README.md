# 02 架构策略

回答"该选哪条路"。前四页是链路形态，后三页是链路无关的结构决策。

| 页面 | 内容 |
|---|---|
| [cascade](cascade.md) | 级联 |
| [half-cascade](half-cascade.md) | 半级联 |
| [s2s](s2s.md) | 端到端 S2S |
| [full-duplex](full-duplex.md) | 全双工 |
| [turn-model](turn-model.md) | 回合模型与设备协议 |
| [floor-control](floor-control.md) | 话筒归属与输出仲裁 |
| [state-and-context](state-and-context.md) | 状态与上下文 |

## 原始长文

手册起因里提到的三篇，写于手册之前，正文保留原貌，头部有引用简写到手册页面的对照表。

| 页面 | 内容 | 吸收进 |
|---|---|---|
| [cascade-first-audio-optimization](essays/cascade-first-audio-optimization.md) | 级联的首音在架构和程序设计上还能优化多少 | [cascade](cascade.md)、[first-audio](../03-mechanisms/first-audio.md) |
| [half-cascade-hybrid-architecture](essays/half-cascade-hybrid-architecture.md) | 半级联与混合架构能否同时照顾速度、智力和自然度 | [half-cascade](half-cascade.md)、[state-and-context](state-and-context.md) |
| [s2s-intelligence-externalization](essays/s2s-intelligence-externalization.md) | S2S 模型智力不足时的外置手段 | [s2s](s2s.md)、[tool-calls](../03-mechanisms/tool-calls.md) |
