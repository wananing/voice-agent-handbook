#!/usr/bin/env python3
"""校验回合 trace（JSON Lines）并汇总两种首音的 P50 / P90。

用法：
    python3 scripts/trace_check.py <trace.jsonl> [--quiet]

输入：每行一条回合 trace，字段按 references/specs/trace-evaluation.md 的字段表。
空行和以 # 开头的行跳过。

校验项：
  1. JSON 能解析，(session_id, turn_id) 唯一。
  2. 必填字段齐全：两个 profile 都必填的最小集；A（无屏设备）/ B（有屏 App）各自的必填；
     条件必填（turn_source=text 要 turn_input；failed 要 error_code；工具回合要 tool_calls、
     speak_mode 和工具前 / 工具后分段；按键 / 开放麦要对应锚点）。
  3. 枚举字段只能取规定的值，包括终态类字段：turn_terminal、turn_closed_by（按终态细分）、
     tool_calls[].closed_by、outputs[].status、usage.responses[].response_status。
  4. 首音口径：latency.sound_first_at_ms 和 latency.content_first_at_ms 两个键都必须存在；
     值为整数或 null（null = 应测未测）；都为整数时 content >= sound；
     content_kind=none 时 content_first 必须为 null。
  5. 时间戳单调：主锚点字段值为 0；audio_end <= release / endpoint；sound_first >= 0；
     content_first_ready <= content_first；pre_tool <= tool_done <= output_sent；
     tool_done <= content_first；*_played_at_ms >= 对应送出时刻；
     每个调用 started <= done <= settled；handoff_start <= handoff_done。
  6. 派生量一致：ptt_tail_silence_ms = release - audio_end；queue_wait_ms = content_first -
     content_first_ready；post_tool_ms = content_first - tool_done（两侧都有值时）。

汇总：只用通过校验的 trace。按回合类型（普通 = chat，工具 = tool_*，其他）× 主锚点分组，
turn_terminal 为 failed / discarded 的回合不进延迟分布（failed 计入失败率）；null 计入缺失。
分位数用最近秩法。P50 n < 20、P90 n < 50 时标"样本不足"。

退出码：全部通过为 0，有校验错误为 1，文件读不了为 2。只用标准库。
"""

import json
import math
import sys
from collections import OrderedDict, defaultdict

# ---- 字段表（与 trace-evaluation.md 一致） ----

REQUIRED_ALL = [
    "trace_schema_version", "profile", "session_id", "user_session_id", "turn_id",
    "generation", "turn_source", "t0_epoch_ms", "pipeline", "provider", "model",
    "prompt_version", "gateway_build",
    "turn_kind", "user_text", "action", "decision_layer",
    "anchors.primary", "anchors.clock_quality",
    "latency.sound_first_at_ms", "latency.content_first_at_ms", "latency.content_kind",
    "session_seq", "rebuild_reason", "state_block_version", "state_update",
    "reply_modality", "turns_in_upstream_session",
    "speech_ms", "interrupted",
    "outputs", "double_voice_count",
    "uplink_jitter_ms", "rtt_ms",
    "turn_terminal", "turn_closed_by", "restart_attempts_in_turn", "watchdog_fired", "stuck",
    "usage.usage_semantics",
]
REQUIRED_A = ["device_id", "server_vad_segments_in_turn", "dropped_short_press",
              "uplink_seq_gaps", "downlink_lead_ms"]
REQUIRED_B = ["user_id_hash", "route", "risk_level"]
REQUIRED_TOOL_LATENCY = ["latency.fc_received_at_ms", "latency.pre_tool_ms",
                         "latency.tool_done_at_ms", "latency.post_tool_ms"]
REQUIRED_TOOL_CALL = ["call_id", "name", "args", "presentation", "is_write", "latency_class",
                      "tool_route", "generation", "late_result_dropped", "started_at_ms",
                      "done_at_ms", "tool_exec_ms", "reply_required", "closed_by", "settled_at_ms"]
REQUIRED_OUTPUT = ["request_id", "origin", "kind", "content_role", "generation",
                   "sentence_id", "sent_at_ms", "played_ms", "status"]

INTERRUPT_SOURCES = {"button", "vad", "wake", "text", "link", "critical", "guardrail"}
ERROR_CODES = {"no_speech", "asr_timeout", "upstream_unavailable", "upstream_timeout",
               "upstream_rejected", "tts_failed", "audio_gap", "link_lost", "protocol_error",
               "session_expired", "internal"}

ENUMS = {
    "profile": {"A", "B"},
    "turn_source": {"button", "wake", "vad", "text", "event"},
    "turn_input": {"typed", "tap", "test"},
    "turn_kind": {"chat", "tool_verbatim", "tool_compose", "tool_write", "event", "ui_input", "error"},
    "decision_layer": {"rule", "model", "correction", "gate", "stage"},
    "speak_mode": {"verbatim", "model", "delegated", "screen", "none"},
    "write_gate": {"passed", "blocked", "n_a"},
    "route": {"s2s", "cascade", "half_cascade"},
    "risk_level": {"low", "high"},
    "anchors.primary": {"release", "endpoint", "audio_end", "tap", "event"},
    "anchors.clock_quality": {"synced", "rx_minus_half_rtt", "gateway_rx"},
    "latency.content_kind": {"answer", "progress", "none"},
    "rebuild_reason": {"none", "disconnect", "upstream_error", "context_overflow", "state_change",
                       "turn_limit", "scheduled", "modality", "app_resume"},
    "history_format": {"pairs", "packed", "pairs_tools_as_text"},
    "history_scope": {"full", "truncated", "summary_recent"},
    "state_update": {"none", "immediate", "lazy"},
    "reply_modality": {"audio", "text_only", "mixed", "none"},
    "interrupt_source": INTERRUPT_SOURCES,
    "receipt_mode": {"receipts", "estimated"},
    "turn_terminal": {"completed", "interrupted", "discarded", "failed", "cancelled"},
    "error_code": ERROR_CODES,
    "error_class": {"fatal", "transient", "context_overflow", "benign"},
    "watchdog_fired": {"none", "T_asr", "T_filler", "T_respond", "T_drain", "T_intr_receipt",
                       "T_listen_max", "tool_timeout"},
    "usage.usage_semantics": {"net", "gross"},
    "guardrail_result": {"pass", "rewrite", "block", "n_a"},
}
TOOL_CALL_ENUMS = {
    "presentation": {"verbatim", "model", "screen", "silent"},
    "latency_class": {"ms", "seconds", "background"},
    "tool_route": {"A0", "A1", "A2", "A3", "silent"},
    "closed_by": {"result", "interrupted", "cancelled", "progress", "superseded", "duplicate"},
    "interrupt_phase": {"received", "running", "done_not_sent", "sent_speaking",
                        "progress_closed", "narration", "delegating", "n_a"},
}
OUTPUT_ENUMS = {
    "kind": {"cue", "speech", "narration"},
    "content_role": {"filler", "answer", "progress", "error"},
    "origin": {"model", "tool_result", "delegated_result", "cue", "task_progress", "task_result",
               "reminder", "device_event", "system_notice", "system_exit"},
    "status": {"played", "truncated", "flushed", "dropped_stale", "dropped_expired",
               "dropped_duplicate", "dropped_overflow", "dropped_retry_exhausted"},
}
RESPONSE_STATUS = {"completed", "cancelled", "failed", "incomplete"}
CLOSED_BY_FOR_TERMINAL = {
    "completed": {"playback_ended", "T_drain"},
    "discarded": {"short_press", "short_speech", "false_wake", "rule_silence"},
    "interrupted": INTERRUPT_SOURCES,
    "failed": ERROR_CODES,
}
PRIMARY_FIELD = {"release": "anchors.release_at_ms", "endpoint": "anchors.endpoint_at_ms",
                 "audio_end": "anchors.audio_end_at_ms", "tap": "anchors.tap_at_ms",
                 "event": "anchors.event_at_ms"}

_MISSING = object()


def get(rec, path):
    cur = rec
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return _MISSING
        cur = cur[key]
    return cur


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def num(rec, path):
    """返回整数值；缺失或 null 返回 None。"""
    v = get(rec, path)
    return v if is_int(v) else None


def turn_group(kind):
    if kind == "chat":
        return "普通"
    if isinstance(kind, str) and kind.startswith("tool_"):
        return "工具"
    return "其他"


def validate(rec):
    errs = []

    def need(path, where=rec, prefix=""):
        if get(where, path) is _MISSING:
            errs.append("缺必填字段 %s%s" % (prefix, path))

    # 2. 必填
    for p in REQUIRED_ALL:
        need(p)
    profile = rec.get("profile")
    for p in (REQUIRED_A if profile == "A" else REQUIRED_B if profile == "B" else []):
        need(p)
    if rec.get("turn_source") == "text":
        need("turn_input")
    if rec.get("turn_terminal") == "failed":
        need("error_code")
    is_tool = turn_group(rec.get("turn_kind")) == "工具"
    if is_tool:
        need("tool_calls")
        need("speak_mode")
        for p in REQUIRED_TOOL_LATENCY:
            need(p)
    primary = get(rec, "anchors.primary")
    if primary in PRIMARY_FIELD:
        need(PRIMARY_FIELD[primary])
    if rec.get("turn_source") in ("button", "wake", "vad"):
        need("anchors.audio_end_at_ms")

    # 3. 枚举
    for path, allowed in ENUMS.items():
        v = get(rec, path)
        if v is not _MISSING and v is not None and v not in allowed:
            errs.append("%s=%r 不在取值范围 %s" % (path, v, sorted(allowed)))
    term = rec.get("turn_terminal")
    tcb = rec.get("turn_closed_by")
    if term in CLOSED_BY_FOR_TERMINAL and tcb is not None and tcb not in CLOSED_BY_FOR_TERMINAL[term]:
        errs.append("turn_terminal=%s 时 turn_closed_by=%r 不合规，应取 %s"
                    % (term, tcb, sorted(CLOSED_BY_FOR_TERMINAL[term])))

    calls = rec.get("tool_calls") or []
    if not isinstance(calls, list):
        errs.append("tool_calls 必须是数组")
        calls = []
    for i, c in enumerate(calls):
        pre = "tool_calls[%d]." % i
        for p in REQUIRED_TOOL_CALL:
            need(p, c, pre)
        for f, allowed in TOOL_CALL_ENUMS.items():
            v = c.get(f)
            if v is not None and v not in allowed:
                errs.append("%s%s=%r 不在取值范围" % (pre, f, v))
        s, d, st = c.get("started_at_ms"), c.get("done_at_ms"), c.get("settled_at_ms")
        if is_int(s) and is_int(d) and s > d:
            errs.append("%s started_at_ms > done_at_ms" % pre)
        if is_int(d) and is_int(st) and d > st:
            errs.append("%s done_at_ms > settled_at_ms" % pre)
    if is_tool and isinstance(rec.get("tool_calls"), list) and not calls:
        errs.append("工具回合 tool_calls 为空")

    outputs = rec.get("outputs")
    if outputs is not None and not isinstance(outputs, list):
        errs.append("outputs 必须是数组")
        outputs = []
    for i, o in enumerate(outputs or []):
        pre = "outputs[%d]." % i
        for p in REQUIRED_OUTPUT:
            need(p, o, pre)
        for f, allowed in OUTPUT_ENUMS.items():
            v = o.get(f)
            if v is not None and v not in allowed:
                errs.append("%s%s=%r 不在取值范围" % (pre, f, v))

    for i, r in enumerate(get(rec, "usage.responses") if isinstance(get(rec, "usage.responses"), list) else []):
        v = r.get("response_status")
        if v not in RESPONSE_STATUS:
            errs.append("usage.responses[%d].response_status=%r 不在取值范围" % (i, v))

    # 时间字段类型
    for obj_name in ("anchors", "latency"):
        obj = rec.get(obj_name)
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.endswith("_ms") and v is not None and not is_int(v):
                    errs.append("%s.%s 必须是毫秒整数或 null，实际 %r" % (obj_name, k, v))

    # 4. 首音口径
    sf = get(rec, "latency.sound_first_at_ms")
    cf = get(rec, "latency.content_first_at_ms")
    if sf is not _MISSING and cf is not _MISSING:
        if is_int(sf) and is_int(cf) and cf < sf:
            errs.append("content_first_at_ms(%d) < sound_first_at_ms(%d)" % (cf, sf))
        if get(rec, "latency.content_kind") == "none" and cf is not None:
            errs.append("content_kind=none 时 content_first_at_ms 应为 null")

    # 5. 时间戳单调
    def le(a_path, b_path):
        a, b = num(rec, a_path), num(rec, b_path)
        if a is not None and b is not None and a > b:
            errs.append("时间不单调：%s(%d) > %s(%d)" % (a_path, a, b_path, b))

    if primary in PRIMARY_FIELD:
        v = num(rec, PRIMARY_FIELD[primary])
        if v is not None and v != 0:
            errs.append("主锚点 %s 应为 0，实际 %d" % (PRIMARY_FIELD[primary], v))
    le("anchors.audio_end_at_ms", "anchors.release_at_ms")
    le("anchors.audio_end_at_ms", "anchors.endpoint_at_ms")
    if is_int(sf) and sf < 0:
        errs.append("sound_first_at_ms(%d) 早于 T0" % sf)
    le("latency.content_first_ready_at_ms", "latency.content_first_at_ms")
    le("latency.pre_tool_ms", "latency.tool_done_at_ms")
    le("latency.tool_done_at_ms", "latency.output_sent_at_ms")
    le("latency.tool_done_at_ms", "latency.content_first_at_ms")
    le("latency.sound_first_at_ms", "latency.sound_first_played_at_ms")
    le("latency.content_first_at_ms", "latency.content_first_played_at_ms")
    le("handoff_start_at_ms", "handoff_done_at_ms")

    # 6. 派生量
    def derived(name, a_path, b_path):
        got, a, b = num(rec, name), num(rec, a_path), num(rec, b_path)
        if got is not None and a is not None and b is not None and got != a - b:
            errs.append("%s=%d 与 %s - %s = %d 不一致" % (name, got, a_path, b_path, a - b))

    derived("latency.ptt_tail_silence_ms", "anchors.release_at_ms", "anchors.audio_end_at_ms")
    derived("latency.queue_wait_ms", "latency.content_first_at_ms", "latency.content_first_ready_at_ms")
    derived("latency.post_tool_ms", "latency.content_first_at_ms", "latency.tool_done_at_ms")
    return errs


def percentile(values, p):
    s = sorted(values)
    return s[max(0, math.ceil(p / 100.0 * len(s)) - 1)]


def summarize(records):
    groups = OrderedDict()
    order = {"普通": 0, "工具": 1, "其他": 2}
    for rec in sorted(records, key=lambda r: order[turn_group(r.get("turn_kind"))]):
        key = (turn_group(rec.get("turn_kind")), get(rec, "anchors.primary"))
        groups.setdefault(key, []).append(rec)

    header = ["类型", "锚点", "指标", "回合", "失败率", "n", "缺失", "P50(ms)", "P90(ms)", "备注"]
    rows = []
    for (grp, anchor), recs in groups.items():
        total = len(recs)
        failed = sum(1 for r in recs if r.get("turn_terminal") == "failed")
        eligible = [r for r in recs if r.get("turn_terminal") not in ("failed", "discarded")]
        for label, path, skip_none in (("有声音", "latency.sound_first_at_ms", False),
                                       ("有内容", "latency.content_first_at_ms", True)):
            pool = [r for r in eligible
                    if not (skip_none and get(r, "latency.content_kind") == "none")]
            vals = [get(r, path) for r in pool]
            ok = [v for v in vals if is_int(v)]
            missing = len(vals) - len(ok)
            p50 = str(percentile(ok, 50)) if ok else "-"
            p90 = str(percentile(ok, 90)) if ok else "-"
            notes = []
            if len(ok) < 20:
                notes.append("P50 样本不足")
            if len(ok) < 50:
                notes.append("P90 样本不足")
            rows.append([grp, str(anchor), label, str(total),
                         "%.0f%%" % (100.0 * failed / total), str(len(ok)), str(missing),
                         p50, p90, "，".join(notes)])

    def width(s):
        return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)

    cols = list(zip(*([header] + rows)))
    widths = [max(width(c) for c in col) for col in cols]
    lines = []
    for i, r in enumerate([header] + rows):
        lines.append("  ".join(c + " " * (w - width(c)) for c, w in zip(r, widths)).rstrip())
        if i == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    quiet = "--quiet" in argv
    if len(args) != 1:
        print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
        return 2
    try:
        with open(args[0], encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        print("读取失败：%s" % e, file=sys.stderr)
        return 2

    valid, seen, n_records, n_bad = [], set(), 0, 0
    for lineno, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        n_records += 1
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            errs, rec = ["JSON 解析失败：%s" % e], None
        else:
            errs = validate(rec) if isinstance(rec, dict) else ["一行必须是一个 JSON 对象"]
            if isinstance(rec, dict):
                key = (rec.get("session_id"), rec.get("turn_id"))
                if key in seen:
                    errs.append("(session_id, turn_id)=%r 重复" % (key,))
                seen.add(key)
        if errs:
            n_bad += 1
            if not quiet:
                tid = rec.get("turn_id") if isinstance(rec, dict) else None
                print("第 %d 行（turn_id=%s）：" % (lineno, tid))
                for e in errs:
                    print("  - " + e)
        else:
            valid.append(rec)

    print("校验：%d 条 trace，通过 %d，不通过 %d" % (n_records, len(valid), n_bad))
    if valid:
        print()
        print("首音汇总（仅通过校验的 trace；failed / discarded 不进分布；最近秩分位数）")
        print(summarize(valid))
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
