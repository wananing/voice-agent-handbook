# TEN VAD

> 仓库：https://github.com/TEN-framework/ten-vad
> 分析基于：commit `22a3bcd`（2026-02-02）
> 状态：草稿
> 最后更新：2026-10-01

## 定位

TEN VAD 是 Agora / TEN 生态出的**帧级语音活动检测**库：每送进 10 ms 或 16 ms 的 16 kHz 音频，返回一个语音概率和 0/1 标志。它只回答"这一帧是不是人声"，不做"开始说话 / 说完了"的状态机，也不做语义判停。

面向：需要在端侧或服务端低成本跑 VAD、并且希望句尾检测比 Silero 更快的语音 agent。仓库给出 C 动态库（Linux / Windows / macOS / Android / iOS）、WASM、Python、Java、Go 绑定，以及开源的 ONNX 模型和预处理代码。

和同类的区别（以下对比数字全部来自仓库 README，未复现）：

- 和 Silero VAD 比，README 声称精度更高、计算量和内存更小，句尾"语音→非语音"的转换检测更快，而 Silero 有"几百毫秒"的滞后，并且会漏掉两段语音之间的短静音（`README.md:106`、`:136`）。
- 和 smart-turn 这类判停模型不是同一层：TEN VAD 是判停的前一级，给后者提供"静音开始了"的信号。

许可：Apache 2.0 **加附加条款**，禁止以与 Agora 产品竞争的方式部署（`LICENSE` 第 1–5 条）；`pitch_est.cc` 派生自 LPCNet（BSD）。商用前要看清条款。

## 整体架构

```
int16 PCM，16 kHz，每次 hop_size 个样本（160 或 256）
   │
   ▼  预加重 → STFT → 功率谱
   ├─► 40 维 mel 能量（log，按均值方差归一化）
   └─► LPC 基频估计（pitch_est.cc）→ 1 维
   ▼
41 维特征，堆 3 帧上下文（含 1 帧前瞻）
   ▼
小型带状态网络（ONNX，5 个输入输出，隐藏维度 64）→ 语音概率 p
   ▼
p > threshold → flag=1，否则 0（无 hangover，无最短语音 / 静音约束）
```

- 常量见 `src/aed_st.h:15-36`：采样率 16 kHz、40 个 mel 滤波器、上下文窗口 3 帧、前瞻 1 帧、隐藏维度 64。
- 特征拼装在 `src/aed.cc:440-465`：40 维 log-mel 加 1 维基频，各自做均值方差归一化。
- 判决在 `src/aed.cc:983-990`：只和阈值比较。`aed_st.h` 里定义了 160 ms 的输出平滑长度，`AUP_Aed_runOneFrm` 里也声明了中值滤波的变量，但没有实际使用（`src/aed.cc:588-606`）。
- 开源的 `src/` 用 ONNX Runtime 加载 `onnx_model/ten-vad.onnx`（315,449 字节，`src/aed.cc:27-44`、`:706`）。`lib/` 下的预编译库是否就是这份源码编出来的、是否依赖 ONNX Runtime，仓库里看不出来，**待确认**。
- 没有线程模型：调用方每来一帧调一次 `ten_vad_process`，同步返回。

## 关键代码路径

| 功能 | 入口 | 说明 |
|---|---|---|
| C API | `include/ten_vad.h:ten_vad_create` / `ten_vad_process` / `ten_vad_destroy` | `create(handle, hop_size, threshold)`；`process` 输入长度必须等于 hop_size，输出 `out_probability`（0–1）和 `out_flag`（0/1） |
| C API 实现 | `src/ten_vad.cc` | 封装 `AUP_Aed_*`，`threshold` 写进 `extVoiceThr` |
| 特征与推理 | `src/aed.cc:AUP_Aed_runOneFrm`、`AUP_MODULE_AIVAD` | 基频估计 + mel 特征 + ORT 推理 |
| 基频估计 | `src/pitch_est.cc` | 派生自 LPCNet |
| Python 绑定 | `include/ten_vad.py:TenVad` | ctypes 加载 `lib/` 下的预编译库，默认 `hop_size=256, threshold=0.5` |
| Python + ONNX | `examples_onnx/python/ten_vad_python.cc`、`ten_vad_demo.py` | 从源码 + ONNX Runtime 编译的 Python 扩展，Linux / macOS |
| Web | `lib/Web/ten_vad.wasm`（约 283 KB）、`ten_vad.js` | 浏览器端 |
| 流式示例 | `examples/test.py` | 按 256 样本逐帧调用，打印概率和标志 |
| 评测 | `examples/plot_pr_curves.py` + `testset/` | 30 段人工标注音频（`.wav` + `.scv` 区间标注），和 Silero V5 画 PR 曲线 |

## 接口

```c
// include/ten_vad.h
int ten_vad_create(ten_vad_handle_t *handle, size_t hop_size, float threshold);
int ten_vad_process(ten_vad_handle_t handle, const int16_t *audio_data,
                    size_t audio_data_length, float *out_probability, int *out_flag);
int ten_vad_destroy(ten_vad_handle_t *handle);
```

- 输入只能是 16 kHz，其他采样率要先重采样（`README.md:226`）。
- hop_size 推荐 160 或 256（10 / 16 ms）。
- 阈值默认 0.5，README 明确说要按场景调（`README.md:120`）。
- 输出只有帧级结果。要得到"开始说话 / 结束说话"事件，调用方得自己写状态机。几个已有的上层封装：
  - TEN Framework 的 `ten_vad_python` 扩展：最近 120 ms 全部过阈值判开始，最近 1000 ms 全部低于阈值判结束，hop 16 ms（`ten-framework/.../extension/ten_vad_python/config.py:4-10`）。
  - sherpa-onnx 的 TEN VAD 模型：默认 `threshold=0.5, min_silence_duration=0.5 s, min_speech_duration=0.25 s, window_size=256`（`sherpa-onnx/sherpa-onnx/csrc/ten-vad-model-config.h:20-27`）。

## 与 Silero VAD 的对比（只引 README 的数字）

| 项 | TEN VAD | Silero VAD | 来源 |
|---|---|---|---|
| 精度 | PR 曲线优于 Silero 和 WebRTC VAD（只有图，没有数值） | — | `README.md:114`，测试集来自 LibriSpeech、GigaSpeech、DNS Challenge |
| 句尾检测 | 快速检测语音→非语音转换 | 滞后"几百毫秒"，6.5–7.0 s 处漏掉短静音（示例图） | `README.md:136` |
| RTF（Xeon Gold 6348） | 0.0086 | 0.0127 | `README.md:173-174`，这是表里唯一一行两者都有数字的 |
| RTF（其他平台） | 0.005–0.057（Ryzen 9 0.0150、M1 0.0160、Galaxy J6+ 0.0570、iPhone8 0.0050 等） | 未给出 | `README.md:143-210` |
| 库大小 | Linux 306 KB；Android 373 / 532 KB；iOS 320 KB；Web 277 KB | 2.16 MB（JIT）/ 2.22 MB（ONNX） | `README.md:164-165` 及同表 |

注意：PR 曲线比较用的是 Silero V5 的固定 commit（`examples/plot_pr_curves.py:13`），测试集是英文为主的公开语料；中文、儿童、强噪声场景没有公开评测。

## 端侧可用性

- 平台覆盖：Linux x64、Windows x86/x64、macOS（framework）、Android armeabi-v7a / arm64-v8a、iOS、WASM（`lib/` 目录、`README.md:220`）。
- 资源：库几百 KB，ONNX 模型约 308 KB；手机上 RTF 0.005–0.057（README 自述）。
- **没有 ESP32 / MCU 版本**。在 xiaozhi-esp32 这类 MCU 设备上用不了，那一档设备用的是乐鑫 ESP-SR 自带的 VADNet。
- 只有 16 kHz 输入，端侧采集若是 48 kHz 需要先降采样。
- 输入必须是固定 hop 的 int16，不接受任意长度的块，调用方要自己攒帧。

## 对各机制的回答

- [判停](../../03-mechanisms/turn-detection.md)：只提供判停的底层信号（帧级语音概率），不含"静音多久算结束"的状态机。`src/aed.cc` 的判决是纯阈值比较；上层状态机见 TEN Framework `ten_vad_python` 扩展和 sherpa-onnx `ten-vad-model-config.h`。README 声称句尾检测比 Silero 快几百毫秒，这正是它对判停延迟的贡献。
- [打断与截断](../../03-mechanisms/interruption.md)：不涉及打断逻辑本身。在 TEN Framework 的示例里，TEN VAD 的"开始说话"事件被用来触发打断（见 [ten-framework](../frameworks/ten-framework.md)）。
- [首音优化](../../03-mechanisms/first-audio.md)：间接相关。句尾判定越早，后续 ASR 定稿和 LLM 越早开始；具体收益仓库里只有示意图，没有数字。
- [工具回合](../../03-mechanisms/tool-calls.md)：不涉及。
- [会话恢复与上下文同步](../../03-mechanisms/session-recovery.md)：不涉及。
- [音频前处理](../../03-mechanisms/audio-preprocessing.md)：本身只有预加重和 STFT，不做降噪或 AEC；作为前处理链里 VAD 那一环使用，放在降噪和重采样之后。
- [评测](../../03-mechanisms/evaluation.md)：自带 30 段人工标注测试音频和 `examples/plot_pr_curves.py`，可以和 Silero 对比 PR 曲线，也可以换成自己的数据复用这套脚本。

## 取舍与局限

- **只给帧级结果**：开始 / 结束事件、hangover、最短语音、前缀补发都要自己写，不同封装的默认参数差异很大（TEN 扩展 1000 ms 静音，sherpa-onnx 0.5 s）。
- **对短静音敏感**：README 把"能识别两段语音之间的短静音"当优点。如果上层直接用"静音 N ms 即结束"，阈值要相应调大，否则更容易在句中停顿处切段。
- **许可有附加条款**，不是纯 Apache 2.0。
- **预编译库与开源源码的关系不清楚**：Python 默认绑定走预编译库，ONNX 路径要自己编译。
- 只支持 16 kHz；评测集以英文为主，中文效果待确认。
- 训练数据和训练代码没有开源，只开源了模型和推理预处理，不能自己微调。

## 相关

- 机制页：[判停](../../03-mechanisms/turn-detection.md)、[音频前处理](../../03-mechanisms/audio-preprocessing.md)、[评测](../../03-mechanisms/evaluation.md)
- 项目页：[smart-turn](smart-turn.md)、[ten-framework](../frameworks/ten-framework.md)、[sherpa-onnx](../asr/sherpa-onnx.md)
- 对比页：待补
