# 选型决策：我要做 X 场景，该怎么搭

> 状态：草稿
> 最后更新：2026-10-02

**要点**：这一页不下新结论，只把 [02 架构](../02-architectures/README.md)、[03 机制](../03-mechanisms/README.md)、[05 矩阵](framework-matrix.md) 已有的判断串成决策路径。先回答第 1 节的几个问题，再沿第 2 节的树走到一个叶子，最后对照第 3 节的配方和第 4 节的否决条件。每条推荐后面都给了依据页；第 5 节列出手册里还没有数据、但会改变选型的点。

---

## 1. 先问的问题

| # | 问题 | 为什么它决定后面的选择 |
|---|---|---|
| 1 | **设备形态**：按键硬件 / 开放麦硬件 / 浏览器 / App？ | 决定回合边界从哪来。按键和开放麦是同一个回合状态机的两种配置，判停只在开放麦下是真问题（[turn-model](../02-architectures/turn-model.md)、[turn-detection](../03-mechanisms/turn-detection.md)） |
| 2 | **能否在扬声器所在的机器上做 AEC**？ | 服务端拿不到扬声器参考信号；做不到端侧 AEC，就只能用按键或半双工，"说话即打断"不成立（[audio-preprocessing](../03-mechanisms/audio-preprocessing.md)、[full-duplex](../02-architectures/full-duplex.md) §4.1） |
| 3 | **用户群**：成人 / 儿童 / 户外噪声？ | 儿童和噪声会放大所有判停方案的失败模式；服务端 VAD 关不掉的 S2S 和组装式全双工在这里最先出问题（[turn-detection](../03-mechanisms/turn-detection.md) "儿童和停顿多的用户"） |
| 4 | **声音统一、逐字可控、出声前审核**有多硬？ | 硬要求意味着"文本先于语音"，只有级联和半级联满足；S2S 的文本只比音频早几十毫秒（[half-cascade](../02-architectures/half-cascade.md) §2） |
| 5 | **工具回合占比多大、有没有写操作**？ | S2S 普通回合最快、工具回合最慢（2.1–2.3 s vs 级联 1.4 s）；写操作要求后端为真相、幂等、执行前查代际（[s2s](../02-architectures/s2s.md) §3、[tool-calls](../03-mechanisms/tool-calls.md)） |
| 6 | **能否依赖闭源上游**的会话语义？ | S2S 的判停、上下文注入、轮数上限、缓存、错误语义都由上游决定，换上游可能重写适配（[s2s](../02-architectures/s2s.md) §4） |
| 7 | **中文要求**？ | unmute、moshi 只训练了英语（法语），换语言要换模型；其余框架语言中立，中文能力取决于所接的 ASR / TTS / 实时模型（[framework-matrix](framework-matrix.md) 第五节） |
| 8 | **自托管还是云**？ | 开源 S2S 权重没有流式输入和实时服务端，自托管时只能当半级联理解端；自托管 TTS 的流式、取消、许可差异很大（[model-matrix](model-matrix.md) 第二、三节） |

---

## 2. 决策树

先定链路（说什么、谁决定），再定双工方式（说话时听不听）。后者是独立的一维，组装式全双工可以建在级联、半级联或 S2S 之上（[full-duplex](../02-architectures/full-duplex.md) §5）。

```
需要附和、重叠、抢话，且只做英文演示，不要工具？
├─ 是 ──► [A] 原生全双工（研究 / 演示，不进产品）
└─ 否
    要求出声前有完整文本？（审核、逐字念、声音统一、工具密集、写操作）
    ├─ 是
    │   需要专门挑 ASR（中文、方言、儿童）或需要用户转写进历史？
    │   ├─ 是 ──► [B] 级联
    │   └─ 否，想省 ASR 一跳 / 用模型的音频理解 ──► [C] 半级联
    │            （自托管理解端要 24 GB 到 80 GB 级卡，见 model-matrix 第三节）
    └─ 否：在场感优先、工具少、能依赖闭源上游
        ├─ 工具回合占比高 ──► 回到 [B] / [C]，或按回合混合（half-cascade §5.2）
        └─ 否 ──► [D] 半双工 S2S（云端实时 API）

然后定双工方式（对 B / C / D 都适用）：
    有可靠端侧 AEC（浏览器 getUserMedia、芯片 AEC、APM AEC3）且成人为主？
    ├─ 是 ──► 开放麦 + 可打断（组装式全双工）
    └─ 否（无 AEC、儿童、户外噪声）──► 按键半双工
```

### 2.1 叶子的推荐组合

| 叶子 | 框架 | 判停 | 打断 | 首音 | 工具回合 | 评测 |
|---|---|---|---|---|---|---|
| **[A] 原生全双工** | [moshi](../04-projects/full-duplex/moshi.md) | 模型内部，唯一旋钮 `pad_mult` | 无打断事件，由模型决定 | 结构性解决（自述 ~200 ms） | 无 | 只有性能基准；体验靠人工听 |
| **[B] 级联** | [livekit-agents](../04-projects/frameworks/livekit-agents.md)、[pipecat](../04-projects/frameworks/pipecat.md)；中文硬件可从 [xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md) 起步 | 开放麦：VAD 0.2–0.3 s + 音频语义判停 + min/max 0.5 / 3.0 s；按键：只认按键 | 代际冲刷；TTS 词级时间戳截断，写已播 + 打断标记 | 首段短切 → 真流式 TTS → 预热 → 开放麦才抢先生成（只抢 LLM） | 可模板化的本地直念 + 不再生成；要组织语言的回传再生成 + 短 cue | 分段计时，有声音 / 有内容分开报 |
| **[C] 半级联** | livekit-agents（`RealtimeModel(modalities=["text"])` + TTS）、pipecat（`realtime-openai-text.py` 示例）；自托管理解端 [step-audio2](../04-projects/e2e-models/step-audio2.md) / [qwen3-omni](../04-projects/e2e-models/qwen3-omni.md) / [ultravox](../04-projects/e2e-models/ultravox.md) | 完全自控；开源理解端无流式输入，开放麦先判停再整段提交 | 同级联，按 TTS 播放位置截断 | 照搬级联第 1–3、5 条，不做抢先生成 | 工具调用先于声音，结果可直接念 | 理解端与"ASR + 同底座 LLM"在同一批样本上比 |
| **[D] 半双工 S2S** | livekit-agents `RealtimeModel`、pipecat S2S 服务；前台 + 后台用 [qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md) 的分工 | 能关服务端判停就关、自己判停后 commit；关不掉用上游语义判停 + 前置停顿压缩 | 上游 `speech_started` 只负责发现；cancel、truncate（按回执）、代际自己做 | 普通回合不花工程；工具回合在 function call 到达时本地播预合成 cue | 先探上游有无静默收口；没有就回传再生成 + cue，或改走 [C] | 冻结盲测集；按版本比较人设、状态块 |

依据：[cascade](../02-architectures/cascade.md)、[half-cascade](../02-architectures/half-cascade.md)、[s2s](../02-architectures/s2s.md)、[full-duplex](../02-architectures/full-duplex.md)；机制见 [turn-detection](../03-mechanisms/turn-detection.md)、[interruption](../03-mechanisms/interruption.md)、[first-audio](../03-mechanisms/first-audio.md)、[tool-calls](../03-mechanisms/tool-calls.md)、[evaluation](../03-mechanisms/evaluation.md)。

### 2.2 不随链路变的结构决策

不管走哪个叶子，下面三件都要按"必须做 / 可以从简"判断一次：

| 结构决策 | 必须认真做 | 可以从简 |
|---|---|---|
| [turn-model](../02-architectures/turn-model.md)：`turn_id` 贯穿每帧、每回合一个终态、识别回显软确认 | 无屏硬件；多来源出声；要过滤迟到帧、按回合统计 | 单一链路、有屏幕、原型 |
| [floor-control](../02-architectures/floor-control.md)：单话筒、代际、租约、插话窗口 | 多个出声来源；回合外有播报；S2S 上游可能推迟到帧 | 纯级联、只有模型回复一个来源 |
| [state-and-context](../02-architectures/state-and-context.md)：后端为真相、本地上下文为真相、状态块、快照 | S2S；断线频繁的设备；有写操作；长会话 | 纯级联、短会话、无写操作 |

---

## 3. 典型场景配方

### 3.1 按键儿童硬件（中文，无屏）

| 项 | 选择 |
|---|---|
| 链路 | 级联；半级联（Step-Audio 2 mini 整段提交正好对应松键）作备选，前提是理解端在儿童语音上不低于"ASR + LLM"（未验证） |
| 框架 | 设备 [xiaozhi-esp32](../04-projects/devices/xiaozhi-esp32.md) `manual` 模式 + [xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md) 起步，按 [turn-model](../02-architectures/turn-model.md) 补 `turn_id`、终态、播放回执 |
| ASR / TTS / 模型 | 流式中文 ASR（[funasr](../04-projects/asr/funasr.md) 2pass 出 partial，松键时基本已定稿）；带 TN 的流式 TTS（[cosyvoice](../04-projects/tts/cosyvoice.md)）；文本 LLM 任选 |
| 判停 | 只认按键；VAD 只做停顿压缩、短按过滤（< 250 ms 或语音 < 150 ms）、补充窗口（smart-turn P < τ 时留 600 ms，上限 1 s）。不用文本判停 |
| 打断 | 只认按键：设备本地立即停播、清缓冲、本地代际 +1，再上报播放位置和 `abort` |
| 首音 | 识别定稿即起跑（不做抢先生成）；首段 ≥ 6 字遇标点切、12–15 字强制切；本地预合成 cue 第一优先级；每个 `failed` 都要发声 |
| 工具策略 | 状态、进度类本地直念 + 不再生成；写操作后端判定、幂等键、执行前查代际；递归上限 3–5 步 |
| 评测门禁 | 主锚点松键（T_rel）；儿童真人录音冻结集为准，模拟用户只找回归；VAD PR 曲线和 smart-turn P 分布在自己的样本上定阈值 |
| 已知坑 | 小智协议没有 turn id、没有播放回执，被打断的回复全文写入历史；点击打断可能残留约 1.2 s 已缓冲音频；CosyVoice 无取消接口，要自加停止标志；RNNoise 无增益下限，不压儿童主路；AGC2 在户外底噪下恰好不放大小声孩子；所有判停参数都没在儿童集上测过 |

依据：[turn-detection](../03-mechanisms/turn-detection.md) 我们的判断、[interruption](../03-mechanisms/interruption.md)、[first-audio](../03-mechanisms/first-audio.md)、[audio-preprocessing](../03-mechanisms/audio-preprocessing.md)、[turn-model](../02-architectures/turn-model.md) §6.1。

### 3.2 浏览器客服（工具密集、有写操作）

| 项 | 选择 |
|---|---|
| 链路 | 级联或半级联。客服评测里纯 S2S 44%、S2S + 外围工程 62%、纯文本大模型 68%（qwen-audio-agent 自述），工具回合 S2S 最慢 |
| 框架 | [livekit-agents](../04-projects/frameworks/livekit-agents.md)（发言级仲裁、异步工具、`say()`）或 [pipecat](../04-projects/frameworks/pipecat.md)（`run_llm=False`、`pipecat eval`）；需要节点式流程时用并入 pipecat 本体的 Flows（1.5.0 起） |
| ASR / TTS / 模型 | 云或自托管均可，每段单独评测；最强文本 LLM 做决策 |
| 判停 | 开放麦：VAD 0.2–0.3 s + 音频语义判停 + min/max 0.5 / 3.0 s，有条件开动态端点 |
| 打断 | 浏览器 `echoCancellation` / `noiseSuppression` / `autoGainControl` 全开；服务端 VAD 起说 + AEC 预热 3 s + 最短 0.5 s；判停和打断都在服务端 |
| 首音 | 抢先生成只抢 LLM，遇工具撤销；首段短切；TTS 建连与 LLM 并行 |
| 工具策略 | 可模板化结果本地直念；需组织语言时回传再生成，工具 > 300 ms 先播 cue；秒级写操作走异步：先回"已提交"收口、结果等插话窗口；写操作门槛留在委派之前，同一回合只认第一个写调用 |
| 评测门禁 | 每次提交 L0 冻结盲测集（含 ASR 噪声变体和写操作负例）；发布前 L1 脚本 `--repeat ≥ 10`；主锚点判停（T_ep） |
| 已知坑 | pipecat 打断只清队列、不按代际过滤迟到帧；livekit-agents 服务端镜像没处理 `truncated`；短工具委派后台反而更慢（1.317 s → 3.363 s）；框架自带评测默认是文本模态，不等于测过语音链路 |

依据：[cascade](../02-architectures/cascade.md)、[tool-calls](../03-mechanisms/tool-calls.md)、[interruption](../03-mechanisms/interruption.md)、[framework-matrix](framework-matrix.md) 第三、四节。

### 3.3 自有开放麦硬件（音箱、免按键）

| 项 | 选择 |
|---|---|
| 链路 | 组装式全双工建在级联上（unmute 的路线，硬件版是小智 `realtime` 模式） |
| 框架 | 服务端 livekit-agents / pipecat；设备侧参考 [xiaozhi-esp32](../04-projects/devices/xiaozhi-esp32.md) 的状态机门控和 `playback_generation_` |
| ASR / TTS / 模型 | 同级联；ASR 前加判别支路降噪，主路默认不降噪 |
| 判停 | 同 3.2；静音时长按采样数算，VAD 不设绝对音量门槛 |
| 打断 | **必须端侧 AEC**：芯片 AEC（ESP-SR AFE）或移植 APM AEC3（播放期间麦克风常开、实例跨轮保持、render 取 DAC 前最后一份 PCM）；服务端 VAD 起说 + 过滤；做不到就退回按键 |
| 首音 | 同级联；无屏设备预合成 cue 优先 |
| 工具策略 | 同 3.2；有事件播报时加插话窗口，以设备播放回执为准 |
| 评测门禁 | 边播边说录音测 AEC 收敛时间和残余回声；误打断率进 trace |
| 已知坑 | ESP-SR AFE 只对白名单板型开放；小智服务端 AEC 标注 Unstable；小智 `realtime` 下服务端要等 ASR 出整句才打断；附和和咳嗽不过滤就会被自己的听众打断 |

依据：[full-duplex](../02-architectures/full-duplex.md) §2.2、§4.1，[audio-preprocessing](../03-mechanisms/audio-preprocessing.md) 我们的判断，[floor-control](../02-architectures/floor-control.md) §5。

### 3.4 纯自托管中文

| 项 | 选择 |
|---|---|
| 链路 | 级联为主；半级联用 Step-Audio 2 mini（7B、原生工具解析、权重 Apache 2.0） |
| 框架 | pipecat 或 livekit-agents（语言中立）；判停模型用 [smart-turn](../04-projects/turn-vad/smart-turn.md)（8 MB、CPU、BSD、可微调），LiveKit v1-mini 闭源只作对照 |
| ASR / TTS / 模型 | ASR：[funasr](../04-projects/asr/funasr.md) 2pass，端侧用 [sherpa-onnx](../04-projects/asr/sherpa-onnx.md)；TTS：[cosyvoice](../04-projects/tts/cosyvoice.md)（唯一支持文本流入）或 [voxcpm](../04-projects/tts/voxcpm.md)（代码和权重 Apache-2.0、可停止迭代） |
| 判停 | 按设备形态走 3.1 或 3.2 |
| 打断 | 代际冲刷；TTS "能否中途停止"当硬指标 |
| 首音 | 自己切分，不用 TTS 内置切句；在自己的并发下实测首块（裁首部静音），目标 P50 ≤ 0.4 s |
| 工具策略 | 级联同 3.2；Step-Audio 2 不自动续答，天然可跳过再生成 |
| 评测门禁 | 同一归一化口径的 CER、TTS 回转 CER、首包 P50 / P90；单机并发自己压测 |
| 已知坑 | 端到端模型都不产出用户转写，要另跑 ASR；Step-Audio 2 要用 stepfun 的 vLLM 分支；Qwen3-Omni 只用 Thinker 也要约 69–79 GB；fish-speech 商用需另签、index-tts 大主体需申请；多数模型权重许可待确认；unmute 按空格切词对中文无效 |

依据：[model-matrix](model-matrix.md) 第一至四节、[half-cascade](../02-architectures/half-cascade.md) §4、[first-audio](../03-mechanisms/first-audio.md)、[session-recovery](../03-mechanisms/session-recovery.md) 我们的判断。

### 3.5 快速原型验证

| 项 | 选择 |
|---|---|
| 链路 | 云端 S2S（一个 API）或最简级联 |
| 框架 | [openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md)（浏览器直连，chat-supervisor / handoff 两种参照）；或 pipecat / livekit-agents 的示例 |
| ASR / TTS / 模型 | 云服务；级联时用 ASR 自带端点（FunASR 800 ms、sherpa 1.2 s） |
| 判停 | 服务端 VAD 或纯 VAD 静音，"够用，但别在上面调体验" |
| 打断 | 框架默认；可以只做"取消任务 + 清队列 + 回合级 id 过滤" |
| 首音 | 不优化；但两个首音分开打点 |
| 工具策略 | 框架默认 |
| 评测门禁 | 冒烟；只算均值、5 次左右的测试只能当冒烟 |
| 已知坑 | openai-realtime-agents 是参考配置不是运行时：没有测试、没有重连、工具在浏览器执行、没有跳过再生成；原型上的 S2S 普通回合速度不能外推到工具回合 |

依据：[turn-detection](../03-mechanisms/turn-detection.md) 我们的判断、[floor-control](../02-architectures/floor-control.md) §7、[state-and-context](../02-architectures/state-and-context.md) §4、[framework-matrix](framework-matrix.md) 第五节。

### 3.6 最低延迟的成人闲聊

| 项 | 选择 |
|---|---|
| 链路 | 半双工 S2S（普通回合首音 0.87–0.96 s 量级）；英文研究演示可看原生全双工 moshi |
| 框架 | livekit-agents `RealtimeModel`（`RealtimeCapabilities` 能力位）或 pipecat S2S 服务 |
| ASR / TTS / 模型 | 云端实时模型，按能力位而不是厂商名选 |
| 判停 | 上游判停（能关就关、自己判）；浏览器端开三项约束 |
| 打断 | 上游 `speech_started` 发现；`cancel` + `truncate(audio_end_ms)` 以回执为准；Gemini / AWS 不支持 truncate 时只改本地 |
| 首音 | 普通回合不花工程；工具回合本地 cue 不进上下文 |
| 工具策略 | 少工具；工具表建会话时一次给全，按阶段在工具层拒绝 |
| 评测门禁 | 冻结盲测集；长会话召回脚本（名字、早期信息） |
| 已知坑 | 历史只能在建会话参数里带入（中途插入 0/6）；上游约 20 轮上限要靠状态块 + 主动换会话；`session.update` 让缓存失效（输入 ×3.2）；本地 cue 和原生声音听成"两个人"；往音频会话灌文本可能翻转成纯文本回复 |

依据：[s2s](../02-architectures/s2s.md) §3–§4、[state-and-context](../02-architectures/state-and-context.md) §2.5、§3，[session-recovery](../03-mechanisms/session-recovery.md)。

---

## 4. 什么情况下别选

### 4.1 链路

| 链路 | 否决条件 |
|---|---|
| 级联 | 闲聊陪伴、普通回合首音和自然度要求极高；需要副语言理解且不愿另接分类器；资源紧张、不想运维三套模型 |
| 半级联 | 强调语气和在场感；普通回合首音要压到 S2S 的 0.9 s 量级；不想自托管大模型（开源理解端要么推理弱，要么要 80 GB 卡） |
| 半双工 S2S | 工具密集；事实必须准确、要出声前审核；长会话强记忆；按键、儿童、户外噪声且上游服务端 VAD 关不掉 |
| 原生全双工 | 需要工具、事实、审核、中文、长会话（约 5 分钟上限）、并发的任何产品；按键说话设备 |
| 组装式全双工（开放麦） | 没有可靠端侧 AEC；高噪声、儿童 |

### 4.2 框架与项目

| 项目 | 否决条件 |
|---|---|
| [pipecat](../04-projects/frameworks/pipecat.md) | 多出声来源、上游可能推迟到帧，又不打算自己补代际过滤；要求 OpenAI Realtime 截断精确到播放位置（按墙钟、未扣缓冲） |
| [pipecat-flows](../04-projects/frameworks/pipecat-flows.md) | 用独立包（已冻结于 1.4.0）；跑在 S2S 上（Flows 依赖的帧在 OpenAI Realtime / Gemini Live 上未实现或无动作） |
| [livekit-agents](../04-projects/frameworks/livekit-agents.md) | 单会话开销敏感（一会话一进程）；上游是 Gemini / AWS / Ultravox 又要自己判停（关不掉）；依赖 GPT-Live 做静默收口；Krisp 降噪插件是商业授权 |
| [ten-framework](../04-projects/frameworks/ten-framework.md) | 要装在终端用户设备上或与 Agora 竞争（附加条款）；需要异步工具、上下文截断、带退避的重连 |
| [openai-realtime-agents](../04-projects/frameworks/openai-realtime-agents.md) | 当生产运行时用；需要异步工具或跳过再生成 |
| [qwen-audio-agent](../04-projects/frameworks/qwen-audio-agent.md) | 需要级联或本地判停（仓库无本地 VAD）；需要按播放位置截断 |
| [unmute](../04-projects/full-duplex/unmute.md) | 中文；需要工具或长会话（每轮全量历史、无摘要） |
| [moshi](../04-projects/full-duplex/moshi.md) | 同原生全双工 |
| [xiaozhi-esp32-server](../04-projects/devices/xiaozhi-esp32-server.md) | 不补协议直接上产品：没有 turn id、没有播放回执、被打断全文写历史、断线不恢复、无异步工具 |

---

## 5. 本手册还没有数据支撑的决策点

下面这些会直接改变上面的选择，但目前只有推算、建议值或"待确认"：

| 决策点 | 影响哪个选择 | 出处 |
|---|---|---|
| 各家 S2S 上游的服务端 VAD 能否关闭（DashScope `turn_detection: null`、Doubao mute / unmute） | 按键设备能否用 S2S | [s2s](../02-architectures/s2s.md) 待确认 |
| 上游收到工具结果后有无静默收口选项；跳过再生成的 S2S 有内容首音（估算约 1.1 s） | 工具密集产品选 S2S 还是半级联 | [tool-calls](../03-mechanisms/tool-calls.md) 我们的判断 |
| 上游 cancel 后是否真会推来迟到音频 delta | S2S 下是否必须做中心仲裁和每帧代际比较 | [floor-control](../02-architectures/floor-control.md) 待确认 |
| 开源理解端（Step-Audio 2、Qwen3-Omni、Ultravox）的 TTFT 和端到端首音 | 自托管半级联能否达到松键到首音 P50 ≤ 1.1 s | [half-cascade](../02-architectures/half-cascade.md)、[first-audio](../03-mechanisms/first-audio.md) 待确认 |
| 理解端在儿童语音、户外噪声下是否不低于"ASR + 同底座 LLM" | 儿童硬件选级联还是半级联 | [half-cascade](../02-architectures/half-cascade.md) 待确认 |
| smart-turn、LiveKit turn detector 的中文和儿童准确率；判停参数在儿童集上的表现 | 开放麦中文能否依赖语义判停；儿童配方的参数 | [turn-detection](../03-mechanisms/turn-detection.md)、[model-matrix](model-matrix.md) 第四节 |
| 自托管中文 TTS 单并发首块 P50 ≤ 0.4 s 是否可达；IndexTTS、VoxCPM 无首包数字 | 自托管级联的首音预算 | [first-audio](../03-mechanisms/first-audio.md) 待确认 |
| 抢先生成在中文、按键场景下的命中率和浪费率 | 是否值得在开放麦中文场景实现 | [first-audio](../03-mechanisms/first-audio.md)、[cascade](../02-architectures/cascade.md) 待确认 |
| 级联"全串行 3.6 s → 优化后 1.1–1.4 s"只是推算 | 级联能否满足闲聊场景的首音 | [cascade](../02-architectures/cascade.md) 待确认 |
| 任何框架、模型的单机并发路数 | 成本和部署形态 | [framework-matrix](framework-matrix.md) 口径差异 |
| 多数 ASR / TTS / 端到端模型的权重许可，框架代码许可的核对 | 能否商用 | [model-matrix](model-matrix.md)、[framework-matrix](framework-matrix.md) 第五节 |
| 降噪方案在目标人群录音上的识别率、误切段率 | 主路是否降噪 | [audio-preprocessing](../03-mechanisms/audio-preprocessing.md) 上线前要实测的项 |
| 除 OpenAI Realtime 协议外，框架是否支持 S2S 与级联按回合切换；云端文本模态计费 | 混合架构是否可行、半级联云成本 | [half-cascade](../02-architectures/half-cascade.md) §5.2、待确认 |

---

## 相关

- 架构层：[cascade](../02-architectures/cascade.md)、[half-cascade](../02-architectures/half-cascade.md)、[s2s](../02-architectures/s2s.md)、[full-duplex](../02-architectures/full-duplex.md)、[turn-model](../02-architectures/turn-model.md)、[floor-control](../02-architectures/floor-control.md)、[state-and-context](../02-architectures/state-and-context.md)
- 机制层：[turn-detection](../03-mechanisms/turn-detection.md)、[interruption](../03-mechanisms/interruption.md)、[first-audio](../03-mechanisms/first-audio.md)、[tool-calls](../03-mechanisms/tool-calls.md)、[session-recovery](../03-mechanisms/session-recovery.md)、[audio-preprocessing](../03-mechanisms/audio-preprocessing.md)、[evaluation](../03-mechanisms/evaluation.md)
- 对比页：[framework-matrix](framework-matrix.md)、[model-matrix](model-matrix.md)
