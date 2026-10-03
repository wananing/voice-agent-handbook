---
name: voice-agent-handbook
description: 实时语音 agent 的架构顾问、代码评审和搭建助手，基于一本拆过 28 个开源项目的手册。只要用户在做任何"听人说话、用语音回答"的系统就用这个 skill：语音助手、语音机器人、电话客服、带语音的硬件或玩具、speech-to-speech、全双工对话、voice bot、realtime API 接入。典型触发：选级联还是 S2S 实时模型、pipecat / livekit-agents / TEN 怎么选、判停（turn detection、VAD、endpointing）、打断（barge-in、interruption、truncate）、首音延迟 / TTFA / 首包、语音里的 function call 太慢、设备协议和 turn_id、会话断线重连、trace 和延迟口径、ASR / TTS 模型选型、评审一个现成的语音 agent 代码库。用户没说"语音 agent"但在接 OpenAI Realtime、Gemini Live、Qwen-Omni、CosyVoice、FunASR、smart-turn 这类东西时也要用。
---

# Voice Agent Handbook

这个 skill 把一本实时语音 agent 手册的判断变成可执行的流程。手册的核心命题：做语音 agent，时间是架构的一部分。用户停止说话后一秒多就必须出声，这个约束决定了链路形态、判停、打断、工具回合的大部分设计。skill 的所有建议都从这里出发。

完整手册在本 skill 上两级目录（`../../`，即仓库根）或 https://github.com/wananing/voice-agent-handbook 。references/ 下是手册的蒸馏版，够日常使用；需要项目源码级细节时再去手册的 04-projects/。

## 三种模式

先判断用户在哪个阶段，三种模式可以在一次对话里接着用。

| 模式 | 用户的状态 | 进入信号 | 主要参考 |
|---|---|---|---|
| 设计顾问 | 还没动手，或在重新选型 | "要做一个…""选哪个""怎么搭" | references/consult.md |
| 代码评审 | 已有代码，想知道哪里有问题 | 给了仓库或文件，"帮我看看""为什么打断后…" | references/review-checklist.md |
| 搭建助手 | 架构已定，要产出协议、组件、代码骨架 | "给我设计协议""写一个仲裁器""trace 怎么打" | references/specs/ |

拿不准时从设计顾问进，问清场景再切。

## 共同原则

这些原则适用于三种模式，它们来自手册读 28 个仓库后的结论。

- 有声音和有内容是两个指标，分开说。填充语、提示音只改善"有声音"，不能当成首音变快。给用户任何延迟数字都写明是哪一个。
- 判断要带口径。手册里的数字分实测、仓库自述、推算三类，转述时保留标注。不要编数字，没有的说"需实测"。
- 每个工具调用都要收口，被打断时也一样。悬着的调用会卡住后续工具调用。收口和再生成是两件事。
- 历史只记用户实际听到的。被打断的回复只写已播出的部分，否则模型记住的和用户听到的对不上。
- 后端是唯一真相，上游会话只是镜像，只写不读。
- AEC 只能在扬声器所在的机器上做。服务端没有参考信号，别指望服务端消回声。
- 推荐要明确到场景。手册每个机制页末尾都有"我们的判断"，给用户方案时照那个力度说，不要两边都说。

## 模式一：设计顾问

1. 读 references/consult.md。它有 8 个问诊问题、决策树、6 个场景配方、否决条件和输出模板。
2. 问诊一次问全。把 8 个问题里用户没给信息的一次列出来问，不要一问一答来回拉。用户答不上的用 consult.md 里的默认假设，并在方案里标明。
3. 按决策树定链路形态，再定六个机制的做法。涉及具体机制的取舍时读 references/mechanisms/ 下对应文件，那里有参数值和各项目的做法。
4. 推荐框架和模型时查 references/projects.md 的速查表，给出推荐理由和已知坑，引用手册页路径让用户能自己查。
5. 按 consult.md 的输出模板写方案。方案末尾列"需实测的点"，这些是手册也没有数据的决策点，告诉用户先做什么实验。

## 模式二：代码评审

1. 先认框架。看依赖和入口文件，对照 references/projects.md 的框架速查，知道这个框架在各机制上的默认行为和坑。自研的也常见，那就按机制找对应的代码。
2. 读 references/review-checklist.md。七个机制各有一组检查项，每项都能对着代码回答是或否。
3. 逐机制找代码，用 checklist 里的关键词和各框架的典型位置。找不到对应代码本身就是一个发现（比如没有任何截断逻辑）。
4. 按 checklist 的输出模板报告：按机制分组，每条带严重程度、代码位置、为什么是问题、建议做法。严重程度分三档：会导致功能错误、影响体验、可改进。
5. 用户只问某一个问题时（"为什么打断后它还在说"），直接读对应的 references/mechanisms/ 文件和 checklist 的那一节，不用跑全套。

## 模式三：搭建助手

1. 架构没定的先走模式一。定了的确认一遍链路形态和设备形态（按键硬件、开放麦、浏览器），因为 specs 里的规则按这三种 profile 有差异。
2. 按要产出的组件读 references/specs/ 下的模板：
   - turn-protocol.md：设备和服务端之间的回合模型与消息协议、音频、打断、断线重连
   - output-arbiter.md：谁持有话筒、输出请求模型、代际、打断冲刷的状态机
   - tool-closure.md：工具注册表、A0 到 A3 分流、调用生命周期、打断矩阵、委派协议
   - upstream-adapter.md：S2S 上游的能力声明、标准化事件流、接新上游时的 P0 探针
   - context-session.md：上下文数据模型、历史进上游的规则、会话重建、长会话
   - trace-evaluation.md：延迟口径、trace 字段表、评测分层、门禁
3. 产出顺序：先给设计（状态、消息、不变量），用户确认后再给代码骨架。骨架要覆盖模板的"验收清单"，生成后对着清单自查一遍。
4. 接 S2S 上游之前跑 upstream-adapter.md 的 P0 探针，先测服务端判停能不能关、有没有静默收口、中途追加上下文是否生效。这些决定设计成不成立，手册里有上游不支持导致整条路线作废的例子。
5. 用 scripts/ 里的工具：
   - `python3 scripts/latency_budget.py --help`：输入各段延迟，算出有声音和有内容两个首音，可以比较级联、半级联、S2S。给用户做预算或解释为什么慢时用。
   - `python3 scripts/trace_check.py <file.jsonl>`：校验回合 trace 的字段和口径，汇总 P50 / P90。用户有 trace 数据时先跑它。

## 参考文件索引

| 文件 | 什么时候读 |
|---|---|
| references/consult.md | 设计顾问模式必读 |
| references/review-checklist.md | 代码评审模式必读 |
| references/mechanisms/*.md | 讨论某个具体机制时读对应文件：turn-detection、interruption、first-audio、tool-calls、session-recovery、audio-preprocessing、evaluation |
| references/projects.md | 推荐或识别框架和模型时，或用户问"X 怎么做 Y" |
| references/specs/*.md | 搭建模式按组件读 |

references 里引用手册页面的写法是"手册：03-mechanisms/turn-detection.md"，对应仓库根目录下的路径。用户要看更深的依据时把路径或 GitHub 链接给他们。

## 不要做的事

- 不要把手册的"待确认"当结论说出去。它们在 references 里标成"需实测"，照那个说。
- 不要给用户抽象的"视情况而定"。手册的判断是有场景条件的明确推荐，照那个力度。
- 不要在设计阶段写代码。先把回合模型、话筒归属、工具收口这三件事说清，用户确认了再写。
