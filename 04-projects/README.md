# 04 开源项目实现

每个项目一篇，结构见 [_templates/project.md](../_templates/project.md)。
"对各机制的回答"一节按 03 的机制逐条作答，并回链到对应机制页。
每篇必须标注分析所基于的 commit 或 tag。

## frameworks：编排框架

| 页面 | 一句话 |
|---|---|
| [pipecat](frameworks/pipecat.md) | 帧驱动的 Python pipeline 框架，打断靠高优先级帧广播并清空各处理器队列，级联和 S2S 共用工具执行与上下文聚合 |
| [pipecat-flows](frameworks/pipecat-flows.md) | 叠在 pipecat 上的节点图式对话流程，每个节点限定可见函数；独立包已冻结，1.5.0 起并入 pipecat 本体 |
| [livekit-agents](frameworks/livekit-agents.md) | 以 SpeechHandle 调度为核心的 Python 语音 agent 运行时，级联和实时模型共用一套会话、工具、打断 API |
| [ten-framework](frameworks/ten-framework.md) | C 内核加多语言扩展的图式实时框架，在 property.json 的 graph 里按节点拼装 RTC/ASR/LLM/TTS |
| [openai-realtime-agents](frameworks/openai-realtime-agents.md) | OpenAI 官方 Realtime API 多 agent 示例（TypeScript）：chat-supervisor 委派与 handoff 两种模式 |
| [qwen-audio-agent](frameworks/qwen-audio-agent.md) | 实时模型做前台对话，长任务经 ACP/A2A 委派给后台 agent，结果在安全窗口注入前台再说出来 |

## full-duplex：开源全双工

| 页面 | 一句话 |
|---|---|
| [unmute](full-duplex/unmute.md) | 用 Kyutai 流式 STT/TTS 加任意文本 LLM，由服务端状态机组装的近全双工系统 |
| [moshi](full-duplex/moshi.md) | 原生全双工语音语言模型，文本流加双音频流，Mimi codec |

## e2e-models：端到端模型

| 页面 | 一句话 |
|---|---|
| [qwen3-omni](e2e-models/qwen3-omni.md) | 全模态 Thinker-Talker 大模型，开源版是请求响应式，能出文本和语音 |
| [step-audio2](e2e-models/step-audio2.md) | 7B 级端到端音频模型，原生工具调用，每回合可选出文本或出语音 |
| [ultravox](e2e-models/ultravox.md) | 语音进文本出的音频 LLM，天然适合当半级联的理解端 |

## asr

| 页面 | 一句话 |
|---|---|
| [funasr](asr/funasr.md) | 阿里中文 ASR 工具箱，C++ 2pass websocket 服务：流式出中间结果，VAD 断句后用离线大模型定稿 |
| [sensevoice](asr/sensevoice.md) | 234M 非流式多语种整句模型，内置标点 / ITN，附带情感和事件标签 |
| [fireredasr](asr/fireredasr.md) | 1.1B AED / 8.3B LLM 的非流式中文模型，公开普通话集上 CER 最低一档，适合当准确率参照 |
| [sherpa-onnx](asr/sherpa-onnx.md) | 跨平台本地语音推理运行时（不是模型），承载流式 / 非流式 ASR，附带 VAD、唤醒、增强等能力 |

## tts

| 页面 | 一句话 |
|---|---|
| [cosyvoice](tts/cosyvoice.md) | LLM + flow matching 零样本 TTS，支持文本流入和音频流出，有 vLLM / TRT-LLM 部署路径 |
| [fish-speech](tts/fish-speech.md) | Dual-AR 高表现力多语种 TTS，本仓库没有句内流式，商用需另签授权 |
| [index-tts](tts/index-tts.md) | 情感与音色分离、可控性最强的零样本 TTS，官方路径整段输出 |
| [spark-tts](tts/spark-tts.md) | 纯 Qwen2.5 + BiCodec 的极简 TTS，16 kHz，流式只在 Triton 部署路径里 |
| [fireredtts2](tts/fireredtts2.md) | 面向多人长对话的逐帧（80 ms）流式 TTS |
| [voxcpm](tts/voxcpm.md) | 连续表征扩散自回归 TTS，48 kHz，可凭描述设计音色，Apache-2.0 |

## turn-vad：判停与 VAD

| 页面 | 一句话 |
|---|---|
| [smart-turn](turn-vad/smart-turn.md) | 只看音频的语义判停模型，Pipecat 默认判停 |
| [ten-vad](turn-vad/ten-vad.md) | 轻量帧级 VAD，句尾检测比 Silero 快 |

## audio-processing：音频前处理与传输

| 页面 | 一句话 |
|---|---|
| [rnnoise](audio-processing/rnnoise.md) | 开源 RNN 单通道降噪库（48 kHz / 10 ms） |
| [webrtc-audio-processing](audio-processing/webrtc-audio-processing.md) | WebRTC APM 独立打包：AEC / NS / AGC |
| [aiortc](audio-processing/aiortc.md) | 纯 Python WebRTC 协议栈，Pipecat SmallWebRTC 的底层 |

## devices：端侧

| 页面 | 一句话 |
|---|---|
| [xiaozhi-esp32](devices/xiaozhi-esp32.md) | ESP32 语音硬件固件，哑终端 + 公开端云协议 |
| [xiaozhi-esp32-server](devices/xiaozhi-esp32-server.md) | 小智开源服务端：插件化级联 VAD/ASR/LLM/TTS |
