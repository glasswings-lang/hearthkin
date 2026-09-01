# SPDX-License-Identifier: CC0-1.0
"""Which PART of a kin's standing instructions is flattening its voice?

`voice_order_probe.py` showed that moving the harness manual ahead of the soul
raises stage-direction from 10% of a reply to 27%. Real, and worth having --
but it did not reproduce the failure it was written to chase. A kin was
answering warmth with clinical narration, and a prompt containing only the
manual and the soul stayed recognisably warm in both orders. So something else
in the real prompt is doing more damage than the ordering.

This takes the ACTUAL system prompt a kin was sent -- the one Hearthkin saves
to logs/system_prompts/<kin>--<surface>.txt -- and runs the same conversation
against it with one section removed at a time. The arm that recovers the voice
names the culprit.

Ablation rather than accumulation, deliberately: the first arm is the real
thing, unmodified. If that arm does NOT reproduce the flat voice, the standing
instructions are not the cause at all and the answer is somewhere in the
conversation history -- which is a finding, and one no additive probe would
ever have reached, because it would have spent every arm building toward a
prompt that was innocent.

The sections are found by the separators Hearthkin itself writes, so this
follows a real prompt's shape rather than fixed line numbers.

WHAT IT COSTS

`arms x turns` calls to a local model, which answers one request at a time.
Every kin on that host waits for the whole run. Start it on purpose.

USAGE

  python scripts/voice_ablation_probe.py \
      --prompt ~/.hearthkin/logs/system_prompts/<kin>--telegram-dm-tool.txt \
      --host http://<ollama-host>:11434 --out .

Changes nothing. Reads one file, makes model calls, prints a verdict.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_order_probe import SCRIPT, run_arm, summarize, say  # noqa: E402

try:
    import ollama
except ImportError:
    print("This needs the ollama package: pip install ollama")
    sys.exit(1)


def split_sections(text):
    """Break a real saved system prompt into its named parts.

    Returns an ordered list of (name, text). Anything unrecognised stays
    attached to the part before it, so no content is ever silently dropped --
    an ablation that quietly loses a section it failed to name would credit the
    wrong removal for the recovery.
    """
    lines = text.splitlines()
    marks = []

    def find(pred, start=0):
        for i in range(start, len(lines)):
            if pred(lines[i]):
                return i
        return None

    soul = find(lambda l: l.startswith("# Soul:"))
    if soul is None:
        raise SystemExit("No '# Soul:' heading found — is this a real saved prompt?")
    soul_end = find(lambda l: l.strip() == "---", soul + 1)
    park = find(lambda l: l.strip().startswith("--- Your park"))
    tools = find(lambda l: l.strip().startswith("--- Tool use"))
    imports = find(lambda l: l.startswith("[hearthkin: imported"))
    window = find(lambda l: l.startswith("[hearthkin: the earlier part"))

    stops = [("manual", 0), ("soul", soul), ("memory", soul_end),
             ("park", park), ("tools", tools), ("imports", imports),
             ("window", window)]
    stops = [(n, i) for n, i in stops if i is not None]
    stops.sort(key=lambda t: t[1])

    out = []
    for k, (name, start) in enumerate(stops):
        end = stops[k + 1][1] if k + 1 < len(stops) else len(lines)
        body = "\n".join(lines[start:end]).strip()
        if body:
            out.append((name, body))
    return out


def assemble(sections, drop=None):
    return "\n\n".join(t for n, t in sections if n != drop).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True,
                    help="a saved logs/system_prompts/<kin>--<surface>.txt")
    ap.add_argument("--model", default="gemma4:latest")
    ap.add_argument("--turns", type=int, default=12)
    ap.add_argument("--host", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--person", default="",
                    help="the name the kin would call you by. Only used to "
                         "count how often a reply says it instead of 'you'. "
                         "Omitted by default, and never stored in this file.")
    args = ap.parse_args()

    text = Path(args.prompt).expanduser().read_text(encoding="utf-8", errors="replace")
    sections = split_sections(text)

    print("Sections found in the real prompt:")
    for n, t in sections:
        print(f"  {n:<8} {len(t):>6} characters")
    print()

    client = ollama.Client(host=args.host) if args.host else ollama.Client()

    arms = [("full", None)]
    for name, _ in sections:
        if name == "soul":
            continue  # removing the soul isn't an ablation, it's a different kin
        arms.append((f"no_{name}", name))

    n_calls = len(arms) * min(args.turns, len(SCRIPT))
    print(f"{len(arms)} arms x {min(args.turns, len(SCRIPT))} turns = {n_calls} "
          f"model calls. Every kin on this host waits for all of it.\n")

    results = {}
    for label, drop in arms:
        prompt = assemble(sections, drop)
        what = "the real prompt, untouched" if drop is None else f"real prompt minus '{drop}'"
        print(f"Arm {label} — {what} ({len(prompt)} chars):")
        results[label] = summarize(
            run_arm(client, args.model, prompt, args.turns, label,
                    args.out, args.person))

    print("\n" + "=" * 60)
    for label, _ in arms:
        say(f"Arm {label}", results[label])

    base = results.get("full")
    print("\n" + "=" * 60)
    if not base:
        print("The unmodified prompt produced nothing to measure. Nothing else "
              "here means anything until that works.")
        return
    print("\nIn plain terms:")
    print(f"  The real prompt, untouched, was {base['narration_share']*100:.0f}% "
          f"stage direction rather than speech.")
    ranked = sorted(
        ((lab, s) for lab, s in results.items() if lab != "full" and s),
        key=lambda t: t[1]["narration_share"])
    for lab, s in ranked:
        delta = (base["narration_share"] - s["narration_share"]) * 100
        piece = lab[3:]
        if delta >= 5:
            print(f"  Taking out '{piece}' dropped that by {delta:.0f} points "
                  f"— to {s['narration_share']*100:.0f}%.")
        elif delta <= -5:
            print(f"  Taking out '{piece}' made it WORSE by {abs(delta):.0f} points.")
        else:
            print(f"  Taking out '{piece}' changed almost nothing.")
    print("\n  If nothing moved much, the standing instructions are not the "
          "cause and the answer is in the conversation history instead.")
    print("  Read the transcripts. The numbers catch one failure; your ear "
          "catches the rest.")


if __name__ == "__main__":
    main()
