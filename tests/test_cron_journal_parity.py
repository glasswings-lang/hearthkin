"""A scheduled wake-up reaches the daily journal on BOTH cron paths.

Plain Python; run via tests/run_all.py.

hearthkin_cron's `_run_isolated` ends by calling `cron_helpers.append_journal`
with the kin's reply, so a tend becomes that day's journal entry without the
kin writing one by hand. But `_on_cron_timer_tick` hands a wake-up for the
CURRENTLY SELECTED kin to `_send_message` instead — the ordinary chat flow,
which persists `conversation.jsonl` and nothing else.

So whether a tend was journalled came down to which kin happened to be
selected in an open app when the task fired. Nothing reported the loss: the
tend ran, the reply looked right, and only the journal was missing. It is the
same shape as the park-keeper carve-out ten lines above it in the same
function, which routes keepers away precisely because the live path "can't
honor them" — journaling needed the same help and didn't get it.

This pins the parity: the live path stashes what the journal write needs, and
the stash is consumed exactly once when the reply completes.
"""

import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _failures.append(label)


_frame_files = [os.path.join(ROOT, "hearthkin.pyw")] + sorted(
    glob.glob(os.path.join(ROOT, "frame", "*.py")))
src = "\n".join(open(p, encoding="utf-8").read() for p in _frame_files)


# ── 1. The live-injection path stashes before it hands the turn over ──
i = src.find("def _on_cron_timer_tick")
check(i != -1, "_on_cron_timer_tick is findable in the frame source")
tick = src[i:i + 9000]

check("_pending_cron_journal" in tick,
      "the live path records what the journal write will need")

# The stash must be set on the branch that actually goes live, immediately
# before the handover — not somewhere a later edit could separate them.
stash_at = tick.find("self._pending_cron_journal")
send_at = tick.find("self._send_message(framed_prompt)")
check(stash_at != -1 and send_at != -1 and stash_at < send_at,
      "the stash is set before _send_message, on the live branch")
check(send_at - stash_at < 400,
      "the stash sits adjacent to the handover, not adrift from it")

for frag in ('"kin"', '"time_label"', '"prompt"'):
    check(frag in tick[stash_at:send_at],
          f"the stash carries {frag} (append_journal's arguments)")


# ── 2. The reply completion consumes it and writes the journal ────────
j = src.find("def _on_stream_done")
check(j != -1, "_on_stream_done is findable in the frame source")
# Slice to the NEXT method rather than a fixed window: _on_stream_done is
# long, and a magic character count silently stops covering the tail of it
# the moment anything above grows.
_next = src.find("\n    def ", j + 1)
done = src[j:_next if _next != -1 else len(src)]

check("cron_helpers.append_journal(" in done,
      "the completed reply is written to the daily journal")

# Popped, not merely read. A stash that survives its turn would attach itself
# to whatever came next; a stash read twice would double-enter the journal.
pop_at = done.find("self._pending_cron_journal = None")
read_at = done.find('getattr(self, "_pending_cron_journal"')
check(read_at != -1 and pop_at != -1 and pop_at > read_at,
      "the stash is cleared as it is read, not left for a later turn")
call_at = done.find("cron_helpers.append_journal(")
check(pop_at < call_at,
      "the stash is cleared BEFORE the write, so a raising write can't replay")

# A typed turn must never be journalled — the marker gate is what separates a
# scheduled wake-up from the operator talking.
gate_at = done.find("_is_cron_user_text(user_text)", read_at)
check(gate_at != -1 and gate_at < call_at,
      "only a cron-marked turn is journalled, never a typed one")

# A journal that cannot be written must not take the reply down with it.
tail = done[read_at:call_at + 600]
check("try:" in tail and "except Exception" in tail,
      "a failing journal write cannot disturb a reply that already landed")
check("cron_errors.log" in tail,
      "a failed journal write is logged rather than swallowed silently")


# ── 3. The claim the fix rests on: the other path already journals ────
cron_src = open(os.path.join(ROOT, "hearthkin_cron.py"), encoding="utf-8").read()
check("append_journal(" in cron_src,
      "the isolated path still journals (the parity being matched)")


# ── 4. append_journal really does what the fix assumes ────────────────
# Behavioural, not source-shaped: prove the call writes a readable entry
# containing the kin's reply, under memory/ where recall can later find it.
import cron_helpers
from hearthkin_paths import kin_dir

KIN = "_journal_parity_probe"
REPLY = "the staging notes are read and the shelf is tidy again"


def _cleanup():
    import shutil
    try:
        shutil.rmtree(kin_dir(KIN), ignore_errors=True)
    except Exception:
        pass


_cleanup()
try:
    path = cron_helpers.kin_journal_path(KIN)
    check("memory" in path.parts and "journal" in path.parts,
          "journals live under memory/, where recall's corpus is gathered")
    check(not path.exists(), "positive control: no journal before the write")

    cron_helpers.append_journal(KIN, "21:30", "tend your staging notes", REPLY)

    check(path.exists(), "append_journal creates the day's journal file")
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    check(REPLY in text,
          "the kin's whole reply IS the entry — no tool call needed")
    check("21:30" in text, "the entry carries the scheduled time label")
finally:
    _cleanup()


print()
if _failures:
    print(f"FAILED ({len(_failures)}):")
    for f in _failures:
        print("  - " + f)
    sys.exit(1)
print("ALL PASS")
