# SPDX-License-Identifier: CC0-1.0
"""Guard test: the speed check reads usage.log correctly and says what's wrong.

This exists because of a failure that went unnoticed for six weeks. A power cut
rebooted the machine running the models; a different service won the race for
the port on the way back up, and every performance setting silently stopped
applying. Replies went from about one minute to about six. Nothing anywhere
reported it — the only alarm that ever sounded was a person getting tired of
waiting.

Every number needed to spot it was already in `logs/usage.log`. The gap was
that nobody had a reason to read a one-megabyte text file, and reading it by
eye wouldn't have surfaced the ratio that mattered anyway.

So the thing under test is a diagnosis, not a dashboard, and the properties
that matter are:

  * it must PARSE. The first version used one regex with optional groups after
    a lazy `.*?`, which matched the prefill group as zero-width and dropped
    every prefill figure — taking the headline with it. The report still
    rendered, still looked fine, and silently omitted the only section that
    mattered. That failure mode is the whole reason for this file.
  * it must call a problem a problem, in words, with a cause named.
  * and it must never crash. A diagnostic that dies on malformed input fails
    exactly when something is already wrong, which is the one moment it has a
    job to do.
"""

import os
import sys
import tempfile
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load the module directly: importing `dialogs` pulls in the whole package and
# needs a real wx display, which a headless test run doesn't have.
_spec = importlib.util.spec_from_file_location(
    "health_check",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "dialogs", "health_check.py"))
hc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hc)

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def line(kin="Opal", ts="2026-07-27T11:30:00", intok=20000,
         ptok=20000, psec=250.0, ptps=80, gtok=800, gsec=78.0, gtps=10,
         surface="desktop-tool"):
    return (f"{ts} kin={kin} model=gemma4:31b in={intok} out={gtok} cached=0 "
            f"est_cost=$0.0000 "
            f"prefill={ptok}tok/{psec}s({ptps}tps) "
            f"gen={gtok}tok/{gsec}s({gtps}tps) surface={surface}")


def report_for(lines):
    fd, path = tempfile.mkstemp(prefix="speedcheck-", suffix=".log")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    try:
        return hc.build_report(path)
    finally:
        os.unlink(path)


# --- it must actually parse ---------------------------------------------
#
# The original bug: prefill silently dropped, headline silently absent, report
# still looked plausible. Pin every field independently.

rows = hc._parse.__wrapped__ if hasattr(hc._parse, "__wrapped__") else hc._parse
fd, p = tempfile.mkstemp(suffix=".log")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(line() + "\n")
parsed = rows(p)
os.unlink(p)
check("a usage line parses at all", len(parsed) == 1)
r = parsed[0] if parsed else {}
check("prefill tokens are read", r.get("ptok") == 20000)
check("prefill SECONDS are read (the field that went missing)", r.get("psec") == 250.0)
check("prefill rate is read", r.get("ptps") == 80)
check("generation rate is read", r.get("gtps") == 10)
check("prompt size is read", r.get("intok") == 20000)
check("surface is read", r.get("surface") == "desktop-tool")


# --- the headline verdict ------------------------------------------------

cold_only = report_for([line(ts=f"2026-07-27T10:{i:02d}:00", ptps=80, psec=250.0)
                        for i in range(20)])
check("all-cold is reported as a PROBLEM", "PROBLEM" in cold_only)
check("...with the actual percentage", "0%" in cold_only)
check("...and names a cause rather than just a number",
      "parallel slots" in cold_only)
check("...and says what it costs in seconds of silence",
      "250 seconds" in cold_only or "seconds of silence" in cold_only)

warm_only = report_for([line(ts=f"2026-07-27T10:{i:02d}:00", ptps=8000, psec=3.0)
                        for i in range(20)])
check("all-warm is reported as OK", "OK:" in warm_only and "PROBLEM" not in warm_only)

mixed = report_for([line(ts=f"2026-07-27T10:{i:02d}:00",
                         ptps=(8000 if i < 18 else 80),
                         psec=(3.0 if i < 18 else 250.0)) for i in range(20)])
check("a mostly-warm machine is not called a problem", "PROBLEM" not in mixed)


# --- background work is surfaced ----------------------------------------
#
# The answer to "why was it slow when I was only talking to one kin".

bg = report_for(
    [line(ts=f"2026-07-27T10:{i:02d}:00", surface="heartbeat") for i in range(6)]
    + [line(ts=f"2026-07-27T11:{i:02d}:00", surface="cron-subprocess-tools") for i in range(4)]
    + [line(ts=f"2026-07-27T12:{i:02d}:00", surface="desktop-tool") for i in range(10)])
check("background work is broken out by source", "heartbeat" in bg)
check("...and totalled as a share", "50% of all work was background" in bg)


# --- per-kin sizes -------------------------------------------------------

per = report_for([line(kin="Bracken", ts=f"2026-07-27T10:{i:02d}:00", intok=21000) for i in range(3)]
                 + [line(kin="hollis", ts=f"2026-07-27T11:{i:02d}:00", intok=9000) for i in range(3)])
check("each kin's prompt size is listed", "Bracken" in per and "hollis" in per)
check("...biggest first, since that's who waits longest",
      per.index("Bracken") < per.index("hollis"))


# --- it must never crash -------------------------------------------------
#
# A diagnostic dies exactly when it's needed if it can't survive bad input.

for junk in ([], ["garbage"], ["", "   "],
             ["2026-13-45T99:99:99 kin=X model=Y in=abc"],
             [line(ptok=None) if False else "2026-07-27T10:00:00 kin=X model=Y"]):
    try:
        out = report_for(junk) if junk else hc.build_report("/nonexistent/path.log")
        ok = isinstance(out, str) and len(out) > 0
    except Exception as e:
        ok = False
        print(f"   raised: {e!r}")
    check(f"survives {str(junk)[:40]!r}", ok)

check("a missing log file says so rather than exploding",
      "No usage data" in hc.build_report("/nonexistent/path.log"))


print()
if _fails:
    print(f"{len(_fails)} FAILURE(S): " + ", ".join(_fails))
    sys.exit(1)
print("test_speed_check: all checks passed")
