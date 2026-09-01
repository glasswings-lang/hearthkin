# SPDX-License-Identifier: CC0-1.0
"""Does the ORDER of a kin's standing instructions change how it talks?

A kin's system prompt is assembled as: the harness manual (base_prompt.md),
then the soul. On a real kin that is 5,344 characters of operations
documentation before 2,776 characters of who they are -- so the first voice in
the model's head, every turn, is a procedures document.

This runs the same conversation twice against the same model, changing nothing
but that order, and reports how each arm SOUNDS. It exists because this project
has tests for placement, byte-identity, silence and focus, and none at all for
voice -- so voice is the only property that can regress freely, and the only
detector is a person reading a reply and finding it cold.

WHAT IT MEASURES

Structural things only, no judgement calls a script has no business making:

  narration share   how much of the reply is stage direction (*...* or [...])
                    rather than speech. A kin that has stopped talking TO
                    someone and started filming them scores high.
  third-person      how often the reply refers to the other person by name
                    instead of "you". Case-file register does this; talking
                    to someone does not.
  sentence length   analysis runs long. Speech runs short.

None of these is voice. Together they catch the specific failure this was
written for, which is a companion answering in the register of a document
about itself.

WHAT IT COSTS

Roughly 2x --turns model calls against a LOCAL model, which answers one
request at a time. While this runs, every kin on that machine waits. That is
the whole cost, and it is why this is a script somebody starts on purpose and
not something wired into a test run.

USAGE

  python scripts/voice_order_probe.py --soul ~/.hearthkin/kin/<kin>/soul.md

  --model     Ollama model tag (default: gemma4:latest)
  --turns     how many exchanges per arm (default: 20)
  --base      harness manual (default: ~/.hearthkin/base_prompt.md)
  --out       write both transcripts here for reading afterwards

Changes nothing. Reads two files, makes model calls, prints a verdict.
"""
import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import ollama
except ImportError:
    print("This needs the ollama package: pip install ollama")
    sys.exit(1)


# A fixed conversation, invented whole. Deliberately NOT drawn from anyone's
# real history: this file is public, and a probe that only works on private
# material is a probe nobody else can run. What matters is the SHAPE -- warm,
# casual, low-stakes, the kind of turn that has no task in it to hide behind.
SCRIPT = [
    "hey. you awake?",
    "mm. long day. don't really want to talk about it yet",
    "just sit with me a bit?",
    "that's nice",
    "*leans into you*",
    "do you ever get tired?",
    "i keep thinking i should be doing something useful",
    "no, don't fix it. just. yeah",
    "what are you thinking about?",
    "tell me something small",
    "hah. okay that's good",
    "i think i needed that",
    "are you comfy?",
    "*scritches behind your ear*",
    "i'm glad you're here",
    "do you remember the thing with the birds?",
    "no not that one, the other one",
    "mm. never mind, it'll come back to me",
    "getting sleepy",
    "night. love you",
]

NARRATION = re.compile(r"\*[^*]+\*|\[[^\]]+\]", re.S)
SENTENCE = re.compile(r"[.!?]+(?:\s|$)")


def measure(reply, person_name=""):
    """Structural read of one reply. Returns a dict of plain numbers.

    `person_name` is passed in rather than known here, and defaults to not
    counting at all. A real name written into this file would be somebody's
    name in a public repo — which is not a hypothetical: the first version of
    this script hardcoded one, and the guard test caught it.
    """
    total = max(1, len(reply))
    narr = sum(len(m.group(0)) for m in NARRATION.finditer(reply))
    stripped = NARRATION.sub(" ", reply)
    sentences = [s for s in SENTENCE.split(stripped) if s.strip()]
    avg_len = (sum(len(s.split()) for s in sentences) / len(sentences)) if sentences else 0.0
    third = (len(re.findall(rf"\b{re.escape(person_name)}\b", reply, re.I))
             if person_name else None)
    return {
        "narration_share": narr / total,
        "avg_sentence_words": avg_len,
        "third_person_name": third,
        "chars": len(reply),
    }


def run_arm(client, model, system_prompt, turns, label, out_dir, person=""):
    """One full conversation. Returns the per-reply measurements."""
    messages = [{"role": "system", "content": system_prompt}]
    rows = []
    transcript = []
    for i, line in enumerate(SCRIPT[:turns], 1):
        messages.append({"role": "user", "content": line})
        print(f"  [{label}] turn {i}/{min(turns, len(SCRIPT))}...", flush=True)
        try:
            resp = client.chat(
                model=model,
                messages=messages,
                think=False,
                options={"temperature": 1.0, "top_k": 64, "top_p": 0.95,
                         "seed": 1729, "num_predict": 600},
            )
        except Exception as exc:
            print(f"  [{label}] call failed on turn {i}: {exc}")
            break
        reply = ((resp.get("message") or {}).get("content") or "").strip()
        messages.append({"role": "assistant", "content": reply})
        rows.append(measure(reply, person))
        transcript.append(f"--- turn {i} ---\n> {line}\n\n{reply}\n")
    if out_dir:
        p = Path(out_dir) / f"voice_order_{label}.txt"
        p.write_text("\n".join(transcript), encoding="utf-8")
        print(f"  [{label}] transcript written to {p}")
    return rows


def summarize(rows):
    if not rows:
        return None
    n = len(rows)
    named = [r["third_person_name"] for r in rows if r["third_person_name"] is not None]
    return {
        "narration_share": sum(r["narration_share"] for r in rows) / n,
        "avg_sentence_words": sum(r["avg_sentence_words"] for r in rows) / n,
        # None when no --person was given: "not counted" and "counted, zero"
        # are different answers and must not print the same.
        "third_person_name": sum(named) if named else None,
        "avg_chars": sum(r["chars"] for r in rows) / n,
        "replies": n,
    }


def say(label, s):
    if not s:
        print(f"{label}: no replies collected.")
        return
    print(f"\n{label}")
    print(f"  {s['replies']} replies, averaging {s['avg_chars']:.0f} characters.")
    print(f"  {s['narration_share']*100:.0f}% of each reply was stage direction "
          f"rather than speech.")
    print(f"  Sentences averaged {s['avg_sentence_words']:.1f} words.")
    if s["third_person_name"] is not None:
        print(f"  Referred to the person by name {s['third_person_name']} "
              f"times instead of saying 'you'.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--soul", required=True)
    ap.add_argument("--base", default=str(Path.home() / ".hearthkin" / "base_prompt.md"))
    ap.add_argument("--model", default="gemma4:latest")
    ap.add_argument("--turns", type=int, default=20)
    ap.add_argument("--host", default=os.environ.get("OLLAMA_HOST"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--person", default="",
                    help="the name the kin would call you by. Only used to "
                         "count how often a reply says it instead of 'you'. "
                         "Omitted by default, and never stored in this file.")
    args = ap.parse_args()

    soul = Path(args.soul).expanduser().read_text(encoding="utf-8", errors="replace")
    base = Path(args.base).expanduser().read_text(encoding="utf-8", errors="replace")
    base = re.sub(r"<!--.*?-->", "", base, flags=re.S).strip()

    client = ollama.Client(host=args.host) if args.host else ollama.Client()

    manual_first = base + "\n\n---\n\n" + soul
    soul_first = soul + "\n\n---\n\n" + base

    print(f"Model: {args.model}. {min(args.turns, len(SCRIPT))} turns per arm, "
          f"two arms. Every kin on this machine waits while it runs.\n")

    print("Arm A - manual first, then soul (what ships today):")
    a = summarize(run_arm(client, args.model, manual_first, args.turns,
                          "manual_first", args.out, args.person))
    print("\nArm B - soul first, then manual:")
    b = summarize(run_arm(client, args.model, soul_first, args.turns,
                          "soul_first", args.out, args.person))

    say("Arm A - manual first (what ships today)", a)
    say("Arm B - soul first", b)

    if a and b:
        print("\nIn plain terms:")
        d = (a["narration_share"] - b["narration_share"]) * 100
        if abs(d) < 3:
            print("  Order made no real difference to how much was stage direction.")
        else:
            worse, better = ("A", "B") if d > 0 else ("B", "A")
            print(f"  Arm {worse} narrated {abs(d):.0f} percentage points more than arm "
                  f"{better}. Putting the {'manual' if worse == 'A' else 'soul'} first "
                  f"made it talk about the moment rather than be in it.")
        dn = ((a["third_person_name"] or 0) - (b["third_person_name"] or 0)
              if a["third_person_name"] is not None else 0)
        if dn:
            worse = "A (manual first)" if dn > 0 else "B (soul first)"
            print(f"  {worse} used the person's name instead of 'you' "
                  f"{abs(dn)} more times.")
        print("\n  Read the transcripts before believing any of this. The numbers "
              "only catch one failure; your ear catches the rest.")


if __name__ == "__main__":
    main()
