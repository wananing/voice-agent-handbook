#!/usr/bin/env python3
"""语音 agent 首音预算计算器。

输入一轮对话各段的延迟（秒），输出"有声音"和"有内容"两个首音，
可以同时算普通回合和工具回合，也可以比较几个预设。

用法：
  python3 latency_budget.py --preset list
  python3 latency_budget.py --preset cascade-naive
  python3 latency_budget.py --preset cascade-optimized --preset s2s-button
  python3 latency_budget.py --link cascade --endpoint 0.35 --asr 0.05 --llm 0.3 \
      --chunk 0.1 --tts 0.25 --net 0.08 --tool-decide 0.4 --tool-exec 0.05 --regen 1.2 --cue 0.3

分段含义（都从用户停止说话起算；按键设备从松键起算，此时 endpoint ≈ 0）：
  endpoint     判停：从停止说话到系统认定这一轮结束
  asr          ASR 定稿残余：判停后还要等多久拿到最终转写
  llm          LLM 首 token（级联、半级联）或 S2S 模型首个音频块前的思考时间
  chunk        攒首个可合成片段：从首 token 到首句或首短语凑齐
  tts          TTS 首包（级联、半级联）；S2S 为 0
  net          下行传输加播放启动
  tool-decide  工具回合：模型决定调用工具所需时间（从判停后算）
  tool-exec    工具执行
  regen        结果回传后模型再生成到首个可播内容
  cue          工具回合里本地提示语的首音（预合成，播放时刻从 tool-decide 后算）；0 表示没有提示语

口径说明：有声音 = 用户第一次听到任何声音；有内容 = 第一次听到回答本身。
预设里的数字来自手册 01-foundations/latency-budget.md，标注里写明实测还是推算。
"""
import argparse
import sys

PRESETS = {
    "cascade-naive": dict(
        link="cascade", endpoint=0.8, asr=0.4, llm=0.8, chunk=0.5, tts=0.8, net=0.3,
        note="级联朴素实现，开放麦，固定静音超时、等整句、TTS 整句合成（手册推算）",
    ),
    "cascade-optimized": dict(
        link="cascade", endpoint=0.35, asr=0.0, llm=0.3, chunk=0.1, tts=0.25, net=0.08,
        note="级联优化后，开放麦，语义判停、尾巴冲刷、短首段、TTS 预热（手册推算）",
    ),
    "cascade-button": dict(
        link="cascade", endpoint=0.0, asr=0.37, llm=0.5, chunk=0.1, tts=0.25, net=0.1,
        note="级联按键，一份实践笔记有内容首音 P50 1.4 s（实测），分段为推算",
    ),
    "half-cascade-button": dict(
        link="half-cascade", endpoint=0.0, asr=0.0, llm=0.56, chunk=0.05, tts=0.25, net=0.1,
        note="半级联按键，无实测，目标 ≤ 1.1 s；0.56 s 为 Qwen3-Omni cookbook 参考日志",
    ),
    "s2s-button": dict(
        link="s2s", endpoint=0.0, asr=0.37, llm=0.33, chunk=0.0, tts=0.0, net=0.22,
        tool_decide=0.43, tool_exec=0.05, regen=1.03, cue=0.07,
        note="S2S 按键，一份实践笔记：普通回合 0.87–0.96 s，工具回合有内容 2.1 s（实测）",
    ),
}


def budget(p):
    """返回 (普通回合有声音, 普通回合有内容, 工具回合有声音, 工具回合有内容, 分段表)。"""
    link = p["link"]
    rows = []
    t = 0.0
    for key, label in [("endpoint", "判停"), ("asr", "ASR 定稿残余")]:
        t += p.get(key, 0.0)
        rows.append((label, p.get(key, 0.0), t))
    listen_end = t
    t += p.get("llm", 0.0)
    rows.append(("LLM 首 token" if link != "s2s" else "模型首个音频块", p.get("llm", 0.0), t))
    if link != "s2s":
        t += p.get("chunk", 0.0)
        rows.append(("攒首段", p.get("chunk", 0.0), t))
        t += p.get("tts", 0.0)
        rows.append(("TTS 首包", p.get("tts", 0.0), t))
    t += p.get("net", 0.0)
    rows.append(("下行+播放", p.get("net", 0.0), t))
    normal_content = t
    normal_sound = t

    tool_rows = None
    tool_sound = tool_content = None
    if any(k in p for k in ("tool_decide", "tool_exec", "regen")):
        td = p.get("tool_decide", 0.0)
        te = p.get("tool_exec", 0.0)
        rg = p.get("regen", 0.0)
        cue = p.get("cue", 0.0)
        decide_at = listen_end + td
        tool_content = decide_at + te + rg + p.get("net", 0.0)
        if link != "s2s":
            tool_content += p.get("tts", 0.0)
        tool_sound = decide_at + cue if cue > 0 else tool_content
        tool_rows = [
            ("模型决定调工具", td, decide_at),
            ("工具执行", te, decide_at + te),
            ("结果回传→再生成" + ("" if link == "s2s" else "→TTS"), rg + (0 if link == "s2s" else p.get("tts", 0.0)), tool_content - p.get("net", 0.0)),
            ("下行+播放", p.get("net", 0.0), tool_content),
        ]
    return normal_sound, normal_content, tool_sound, tool_content, rows, tool_rows


def fmt(x):
    return "-" if x is None else f"{x:.2f} s"


def report(name, p):
    ns, nc, ts, tc, rows, tool_rows = budget(p)
    print(f"== {name}  [{p['link']}]")
    if p.get("note"):
        print(f"   {p['note']}")
    print("   普通回合")
    for label, d, acc in rows:
        print(f"     {label:<16} +{d:.2f}   累计 {acc:.2f}")
    print(f"   -> 有声音 {fmt(ns)}   有内容 {fmt(nc)}")
    if tool_rows:
        print("   工具回合")
        for label, d, acc in tool_rows:
            print(f"     {label:<16} +{d:.2f}   累计 {acc:.2f}")
        cue = p.get("cue", 0.0)
        cue_note = f"（提示语在决定调工具后 {cue:.2f} s 出声）" if cue else "（没有提示语，有声音等于有内容）"
        print(f"   -> 有声音 {fmt(ts)}   有内容 {fmt(tc)} {cue_note}")
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", action="append", help="预设名，可重复；'list' 列出全部")
    ap.add_argument("--link", choices=["cascade", "half-cascade", "s2s"])
    for k in ["endpoint", "asr", "llm", "chunk", "tts", "net", "tool-decide", "tool-exec", "regen", "cue"]:
        ap.add_argument(f"--{k}", type=float)
    a = ap.parse_args()

    if a.preset and "list" in a.preset:
        for k, v in PRESETS.items():
            print(f"{k:<22} {v['note']}")
        return
    if a.preset:
        for name in a.preset:
            if name not in PRESETS:
                sys.exit(f"未知预设 {name}，用 --preset list 查看")
            report(name, PRESETS[name])
    if a.link:
        p = {"link": a.link}
        for k in ["endpoint", "asr", "llm", "chunk", "tts", "net", "tool_decide", "tool_exec", "regen", "cue"]:
            v = getattr(a, k)
            if v is not None:
                p[k] = v
        report("自定义", p)
    if not a.preset and not a.link:
        ap.print_help()


if __name__ == "__main__":
    main()
