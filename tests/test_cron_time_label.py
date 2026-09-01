"""Multi-time cron: the fired time_label must survive the request file.

Plain Python; run via tests/run_all.py.

A multi-time entry has "times": [...] and NO "time" key, so a consumer
CANNOT re-derive which of its times fired — only the firing scheduled task
knows (it's passed --time-label). The request file is the only channel from
that task to a running Hearthkin. Before the label rode along, the exact
same entry journaled correctly when Hearthkin was closed (subprocess reads
the CLI arg) and as "(no time)" when it was open.
"""

import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _failures.append(label)


import cron_helpers
import hearthkin_cron

# Redirect the request dir into a temp tree so the real ~/.hearthkin is
# never touched and a stray file can't be consumed by a live app.
_tmp = tempfile.mkdtemp(prefix="hk_cron_test_")
cron_helpers.request_dir = lambda: __import__("pathlib").Path(_tmp)

try:
    # ── The round-trip ───────────────────────────────────────────────
    p = cron_helpers.write_request_file(
        "Tarn", "Afternoon rounds.", 5, time_label="15:00")
    payload = json.loads(open(p, encoding="utf-8").read())
    check(payload.get("time_label") == "15:00",
          "write_request_file carries the fired time_label")
    check(payload.get("entry_index") == 5 and payload.get("kin") == "Tarn",
          "the rest of the payload is unchanged")

    read_back = cron_helpers.read_and_delete_request_file(p)
    check(read_back.get("time_label") == "15:00",
          "the label survives read_and_delete_request_file")

    # Omitted label -> key absent, so the consumer's `or` fallback fires
    # rather than the framing carrying a literal empty time.
    p2 = cron_helpers.write_request_file("Tarn", "x", 0)
    payload2 = json.loads(open(p2, encoding="utf-8").read())
    check("time_label" not in payload2,
          "no label written when none is given (legacy shape preserved)")
    cron_helpers.read_and_delete_request_file(p2)

    # ── Why it can't be re-derived ───────────────────────────────────
    # This is the whole reason the field exists: _resolve_entry reads the
    # legacy "time" key, which a multi-time entry does not have.
    multi = {"times": ["07:00", "11:00", "15:00"], "prompt": "tend",
             "enabled": True}
    legacy = {"time": "09:00", "prompt": "tend", "enabled": True}
    _e, label_multi, _p = hearthkin_cron._resolve_entry(
        {"cron_entries": [multi]}, 0)
    _e, label_legacy, _p = hearthkin_cron._resolve_entry(
        {"cron_entries": [legacy]}, 0)
    check(label_multi == "",
          "_resolve_entry CANNOT derive a multi-time entry's fired time")
    check(label_legacy == "09:00",
          "_resolve_entry still derives a legacy single-time entry")

    # And the entry really does fire at all three times — so "which one
    # fired" is a real question with three possible answers.
    check(cron_helpers.cron_entry_fire_times(multi)
          == ["07:00", "11:00", "15:00"],
          "the multi-time entry fires at three distinct times")

    # ── The consumer prefers the payload ─────────────────────────────
    # Since the 2026-07 modularisation the frame's methods live in frame/*.py;
    # search the concatenated frame source so this guard survives a method
    # moving between mixins (_on_cron_timer_tick is in frame/cron_exec_mixin.py).
    import glob
    _frame_files = [os.path.join(ROOT, "hearthkin.pyw")] + sorted(
        glob.glob(os.path.join(ROOT, "frame", "*.py")))
    src = "\n".join(open(p, encoding="utf-8").read() for p in _frame_files)
    i = src.find("def _on_cron_timer_tick")
    tick = src[i:i + 8000]
    check('payload.get("time_label"' in tick,
          "the tick handler reads the label off the request payload")
    check("args=(kin, entry_index, prompt, time_label)" in tick,
          "the label is threaded into the isolated worker")

    # The subprocess must send it.
    cron_src = open(os.path.join(ROOT, "hearthkin_cron.py"), encoding="utf-8").read()
    check("time_label=time_label" in cron_src,
          "the cron subprocess passes its resolved label to the request file")

finally:
    shutil.rmtree(_tmp, ignore_errors=True)

print()
if _failures:
    print(f"FAILED ({len(_failures)}): " + "; ".join(_failures))
    sys.exit(1)
print("test_cron_time_label.py: all checks passed")
