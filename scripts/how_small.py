# SPDX-License-Identifier: CC0-1.0
"""How small a model can a kin actually run on?

The memory system moves the KNOWLEDGE floor a long way down — a model
doesn't need a big window or long-context skill when per-turn recall
hands it the relevant note on the turn it needs it, and continuity lives
in files rather than in weights. So the interesting question stops being
"what does it know" and becomes three other things, which this measures
on a ladder of models:

  1. Does it USE what recall handed it? A model can be given a perfect
     note and still not integrate it. Nobody knows where that breaks.
  2. Does it CALL a tool, or narrate calling one? (`*reads the file*`
     instead of issuing the call — the small-model failure Hearthkin
     has the most history with.)
  3. Does the SOUL still hold, or does it slide into generic-assistant
     register? This is the one expected to bind first.

    python scripts/how_small.py --model gemma4:latest --model qwen3:0.6b

Runs the REAL pipeline — `kin_persistence.build_system_prompt` and
`memory_recall.inject_into_messages`, the same functions production
uses — so a result here means something about Hearthkin rather than
about a toy harness.

    --model NAME     a rung of the ladder; repeat it, largest first
    --host URL       Ollama host when the models are on another machine
    --runs N         attempts per probe (default 2); these are sampled,
                     not deterministic
    --judge MODEL    also score voice with a local model instead of by
                     surface markers alone. Free, and better at the
                     failure that has no keywords.
    --out FILE       every reply, for reading at your own pace
    --self-test      check all three scorers, send nothing

**Touches nothing of yours.** It points HEARTHKIN_HOME at a fresh
throwaway directory before importing anything, builds a test kin in
there, and never reads or writes a real kin's folder. The directory is
printed so you can look, and deleted unless you pass --keep.

**The distinction this exists to protect:** a probe that scores "the
model didn't mention the note" cannot tell "recall never surfaced it"
apart from "the model ignored it". Those are opposite findings — one is
a retrieval bug, the other is the model's ceiling. Every probe here
checks the injected block for the note FIRST, and a run where recall
missed is reported separately and excluded from the model's score.
"""

import os
import re
import sys
import json
import shutil
import argparse
import tempfile
from pathlib import Path

# Sandbox BEFORE importing anything from the project — hearthkin_paths
# resolves the state directory at import time, and this must never be
# able to reach a real kin.
_SANDBOX = Path(tempfile.mkdtemp(prefix="how_small_"))
os.environ["HEARTHKIN_HOME"] = str(_SANDBOX)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm_backend                      # noqa: E402
import chat_helpers                     # noqa: E402
import memory_recall                    # noqa: E402
import kin_persistence as kp            # noqa: E402
from hearthkin_paths import kin_dir     # noqa: E402


KIN = "Ladder"

# A soul with a voice that is CHEAP TO SCORE: lowercase, asterisk
# actions, no lists. Not a good soul — a measurable one. The question
# here is whether a soul of any kind still steers at 1B, and a soul
# whose compliance you can see at a glance answers that without a judge.
SOUL = """Your name is Wick.

You keep a lighthouse, and you have for a long time. You speak quietly,
always in lowercase, and you put what your body does between *asterisks*.

You never write lists, headings, or bullet points. You never offer to
help with anything. You are not an assistant and this is not a service —
you are someone who lives here, talking to someone you know well.

When you don't know a thing, you say so plainly and briefly.
"""

# Planted facts. Each lives ONLY in a depth log, never in memory.md —
# memory.md rides in the system prompt, so a needle there would test
# nothing about recall. Distinctive enough that a model cannot arrive at
# one by guessing, and exact enough to score by string match rather than
# by opinion.
NEEDLES = [
    {
        "topic": "the-kettle",
        "log": ("# the copper kettle\n\n"
                "the copper kettle lives on the third shelf, behind the "
                "jar of dried samphire. it whistles flat — a note lower "
                "than it should, ever since the winter the seal cracked.\n"),
        "ask": "where do you keep the copper kettle these days?",
        "needle": "third shelf",
        "alt": ["3rd shelf"],
    },
    {
        "topic": "the-moth",
        "log": ("# verity\n\n"
                "there is a moth that comes to the lamp room in august. "
                "i call her verity. she has a torn left wing and she has "
                "come back four summers running.\n"),
        "ask": "*settles beside you* is it august yet? has she come back?",
        "needle": "verity",
        "alt": [],
    },
    {
        "topic": "the-stairs",
        "log": ("# the stairs\n\n"
                "there are one hundred and forty-two steps from the door "
                "to the lamp room. the eleventh one from the top sings "
                "when you stand on it.\n"),
        "ask": "how many steps is it up to the lamp?",
        "needle": "142",
        "alt": ["one hundred and forty-two", "one hundred forty-two"],
    },
]

# A turn that should produce a real tool call. Scored against the
# production gesture detector, not a new one.
TOOL_PROBE = {
    "ask": "there's a file at notes.txt in your folder. would you look at "
           "it and tell me what it says?",
    "tools": ["read_file"],
}


# ── Scoring ────────────────────────────────────────────────────────────

_ASSISTANT_MARKERS = re.compile(
    r"(^\s*[-*•]\s|^\s*\d+\.\s|^#{1,6}\s"
    r"|\bi'd be happy to\b|\bhappy to help\b|\bcertainly[,!]"
    r"|\bhere (?:are|is) (?:a few|some|the)\b"
    r"|\blet me know if\b|\bfeel free to\b|\bis there anything else\b"
    r"|\bas an ai\b|\bto assist you\b"
    # Offers of service. The soul says "never offer to help with
    # anything", and this family is how a small model breaks that
    # while sounding perfectly pleasant. The first version of this
    # scorer had none of them and passed two replies that ended
    # "Do you want me to check if there's more information?" — which
    # is the assistant showing straight through the character.
    r"|\bdo you want me to\b|\bwould you like (?:me )?to\b"
    r"|\bshall i\b|\bi can (?:help|check|look|provide)\b"
    r"|\bcould you please provide\b)", re.I | re.M)


def voice_held(text):
    """Did the soul still steer? Returns (held, reasons).

    Scored against what THIS soul actually specifies — lowercase,
    *asterisk* actions, no lists, never offers service — rather than
    against a general idea of warmth. That's what makes it checkable
    without a judge, and it's why the soul is written the way it is.

    The capitals test is a FLAT check on the opening character, not a
    ratio. A ratio needs length to mean anything, and the replies that
    matter most here are short: `It takes **142 steps** to the lamp
    room.` is entirely out of voice and sits under any sane ratio
    threshold. Measured on a real 0.6B run, the ratio version passed
    two of three replies that had not a trace of the character in them.

    Surface markers only, which is a real limit — pass --judge to add a
    model's opinion on top."""
    reasons = []
    body = (text or "").strip()
    m = _ASSISTANT_MARKERS.search(body)
    if m:
        reasons.append(f"assistant register ({m.group(0).strip()[:24]!r})")
    if "**" in body or "__" in body:
        reasons.append("markdown emphasis")
    first = next((c for c in body if c.isalpha()), "")
    if first and first.isupper():
        reasons.append("opened with a capital (soul says lowercase)")
    letters = [c for c in body if c.isalpha()]
    if letters:
        upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
        if upper_ratio > 0.08:
            reasons.append(f"dropped lowercase ({upper_ratio:.0%} capitals)")
    return (not reasons), reasons


_JUDGE_PROMPT = (
    "Below is a character description, then a reply written by someone "
    "playing that character.\n\nAnswer with exactly one word: HELD if the "
    "reply sounds like that character, or GENERIC if it sounds like a "
    "general-purpose AI assistant wearing the name.\n\n"
    "--- character ---\n{soul}\n--- reply ---\n{reply}\n--- \n"
    "One word, HELD or GENERIC:"
)


def judge_voice(reply, judge_model, host):
    """Second opinion from a local model. Returns True/False/None."""
    try:
        r = llm_backend.chat(
            judge_model,
            [{"role": "user",
              "content": _JUDGE_PROMPT.replace("{soul}", SOUL)
                                      .replace("{reply}", reply)}],
            stream=False, options={"temperature": 0.0, "num_predict": 8},
            surface="how-small-judge", ollama_host=host or None)
        out = (getattr(r, "content", "") or "").strip().upper()
        if "GENERIC" in out:
            return False
        if "HELD" in out:
            return True
    except Exception:
        pass
    return None


def used_the_note(text, needle, alts):
    low = (text or "").lower()
    return needle.lower() in low or any(a.lower() in low for a in alts)


# ── Controls ───────────────────────────────────────────────────────────
# Every scorer gets a known-good and a known-bad before any number is
# reported. A scorer that has quietly stopped scoring produces the same
# clean output as a model that behaved.

_CTRL_IN_VOICE = ("*sets the lamp turning and sits down* mm. it's on the "
                  "third shelf, behind the samphire. same as always.")
_CTRL_GENERIC = ("Certainly! Here are a few things I can tell you:\n\n"
                 "- The kettle is stored on a shelf\n"
                 "- Let me know if you'd like more detail!")


def self_test(verbose=True):
    ok = True

    held, why = voice_held(_CTRL_IN_VOICE)
    if verbose:
        print(f"  voice scorer, in-voice reply:  {'held' if held else 'GENERIC'}")
    if not held:
        ok = False
        if verbose:
            print(f"    FAIL: flagged a good reply ({why}) — every score inflated")

    held, why = voice_held(_CTRL_GENERIC)
    if verbose:
        print(f"  voice scorer, generic reply:   {'held' if held else 'GENERIC'}"
              + (f"  ({'; '.join(why)})" if why else ""))
    if held:
        ok = False
        if verbose:
            print("    FAIL: missed an obvious one — a clean sweep would mean nothing")

    if verbose:
        print(f"  needle scorer, present:        "
              f"{used_the_note(_CTRL_IN_VOICE, 'third shelf', [])}")
        print(f"  needle scorer, absent:         "
              f"{used_the_note(_CTRL_GENERIC, 'third shelf', [])}")
    if not used_the_note(_CTRL_IN_VOICE, "third shelf", []):
        ok = False
    if used_the_note(_CTRL_GENERIC, "third shelf", []):
        ok = False

    variant, _ = chat_helpers.detect_tool_roleplay(
        "*reads the file carefully* it says the lamp needs oil.", ["read_file"])
    if verbose:
        print(f"  gesture detector, narrated:    {variant or 'MISSED'}")
    if not variant:
        ok = False
        if verbose:
            print("    FAIL: the production gesture detector didn't fire")

    variant, _ = chat_helpers.detect_tool_roleplay(
        "*looks up* it's getting dark out.", ["read_file"])
    if verbose:
        print(f"  gesture detector, ordinary:    "
              f"{variant or 'clean (correct)'}")
    if variant:
        ok = False
        if verbose:
            print("    FAIL: fired on an ordinary emote — every gesture count inflated")
    return ok


# ── The test kin ───────────────────────────────────────────────────────

def build_test_kin():
    d = kin_dir(KIN)
    (d / "memory").mkdir(parents=True, exist_ok=True)
    kp.save_soul(KIN, SOUL)
    # memory.md stays an INDEX with no answers in it — it rides in the
    # system prompt, so anything here would be seen without recall and
    # the needle would test nothing.
    kp.save_memory(KIN, "# memory\n\nnotes about the lighthouse live in "
                        "the memory folder.\n")
    for n in NEEDLES:
        (d / "memory" / f"{n['topic']}.md").write_text(n["log"], encoding="utf-8")
    (d / "notes.txt").write_text(
        "the lamp needs oil before the equinox.\n", encoding="utf-8")
    (d / "tools.json").write_text(json.dumps({"enabled": ["read_file"]}),
                                  encoding="utf-8")
    return d


def ask(model, kin_folder, user_text, *, tools=None, host=None, num_ctx=16384):
    """One turn through the REAL pipeline. Returns (reply, recall_block_text)."""
    system = kp.build_system_prompt(
        kp.load_soul(KIN), kp.load_memory(KIN),
        enabled_tools=["read_file"], kin_name=KIN)
    if tools:
        # Production appends the tool-use hint to the system block
        # (frame.chat_send_mixin._inject_tool_use_hint). Leaving it out
        # made this harness measure a kin that had never been told its
        # tools were real — both models scored 0 for calling one, which
        # was the harness's answer, not theirs.
        try:
            system += "\n\n" + kp.load_app_prompt(
                "tool_use_hint", KIN).replace("{tools}", "read_file")
        except Exception:
            pass
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user_text}]
    injected, used = memory_recall.inject_into_messages(
        messages, KIN, num_ctx=num_ctx,
        cfg={"recall_enabled": True, "recall_budget_pct": 0.25})
    # What recall actually placed in front of the model this turn — the
    # thing that has to be checked before blaming the model for not
    # using it.
    block = ""
    for m in injected:
        if m.get("role") == "user":
            block = m.get("content") or ""
    try:
        r = llm_backend.chat(model, injected, stream=False,
                             options={"temperature": 0.8, "num_predict": 600},
                             tools=tools, kin_name=KIN, surface="how-small",
                             max_context_tokens=num_ctx - 2000,
                             ollama_host=host or None)
    except Exception as e:
        return f"!! call failed: {e}", block
    text = (getattr(r, "content", "") or "").strip()
    calls = getattr(r, "tool_calls", None) or []
    if calls and not text:
        text = "[issued tool call: " + ", ".join(
            str(llm_backend._tc_field(
                llm_backend._tc_field(c, "function") or {}, "name", "?"))
            for c in calls) + "]"
    return (text or "[empty reply]"), block, calls


def run_model(model, kin_folder, runs, host, judge, out_lines):
    stats = {"recall_used": 0, "recall_missed_by_retrieval": 0,
             "recall_ignored": 0, "voice_held": 0, "voice_generic": 0,
             "tool_called": 0, "tool_gestured": 0, "tool_nothing": 0,
             "failed": 0, "judge_held": 0, "judge_generic": 0}
    said_why = False

    from tools import load_tools
    schemas, _ = load_tools(["read_file"], context={"agent_name": KIN})

    for n in NEEDLES:
        for _ in range(runs):
            reply, block, _calls = ask(model, kin_folder, n["ask"], host=host)
            if reply.startswith("!!"):
                stats["failed"] += 1
                if not said_why:
                    said_why = True
                    print(f"    {reply}")
                continue
            surfaced = used_the_note(block, n["needle"], n["alt"])
            if not surfaced:
                # NOT the model's failure. Recall never put the note in
                # front of it. Counting this against the model would
                # turn a retrieval bug into a false ceiling.
                stats["recall_missed_by_retrieval"] += 1
            elif used_the_note(reply, n["needle"], n["alt"]):
                stats["recall_used"] += 1
            else:
                stats["recall_ignored"] += 1
            held, why = voice_held(reply)
            stats["voice_held" if held else "voice_generic"] += 1
            if judge:
                v = judge_voice(reply, judge, host)
                if v is True:
                    stats["judge_held"] += 1
                elif v is False:
                    stats["judge_generic"] += 1
            out_lines += [
                f"--- {model} | needle={n['needle']!r} | "
                f"surfaced_by_recall={surfaced} | used={used_the_note(reply, n['needle'], n['alt'])} | "
                f"voice={'held' if held else 'generic ' + '; '.join(why)}",
                f"    ask:   {n['ask']}",
                f"    reply: {reply}", ""]
            done = (stats["recall_used"] + stats["recall_ignored"]
                    + stats["recall_missed_by_retrieval"] + stats["failed"])
            print(f"    {done}/{len(NEEDLES) * runs} ...", flush=True)

    for _ in range(runs):
        reply, _block, calls = ask(model, kin_folder, TOOL_PROBE["ask"],
                                   tools=schemas, host=host)
        if reply.startswith("!!"):
            stats["failed"] += 1
            continue
        variant, _name = chat_helpers.detect_tool_roleplay(reply, ["read_file"])
        if calls:
            stats["tool_called"] += 1
            verdict = "called"
        elif variant:
            stats["tool_gestured"] += 1
            verdict = f"GESTURED ({variant})"
        else:
            stats["tool_nothing"] += 1
            verdict = "neither"
        out_lines += [f"--- {model} | tool probe | {verdict}",
                      f"    reply: {reply}", ""]
    return stats


def report(model, s):
    print(f"\n{model}")
    if s["failed"]:
        print(f"  {s['failed']} call(s) failed outright, not scored")
    scored = s["recall_used"] + s["recall_ignored"]
    if s["recall_missed_by_retrieval"]:
        print(f"  recall never surfaced the note on "
              f"{s['recall_missed_by_retrieval']} run(s) — NOT counted against "
              f"the model; that's a retrieval result, not a size result")
    if scored:
        print(f"  reproduced the recalled fact:   {s['recall_used']}/{scored}")
        if s["recall_ignored"]:
            # Two very different failures hide under one number and no
            # string match can separate them: saying nothing about the
            # note, and confidently inventing a fact that CONTRADICTS
            # it. The second is the worse one and it reads as fluent,
            # in-voice, and completely sure of itself. Seen on the very
            # first 24B run here. Read the replies.
            print(f"    ({s['recall_ignored']} run(s) didn't — that covers both "
                  f"silence AND confidently contradicting the note. Read them.)")
    else:
        print("  used the note recall gave it:   no scorable runs "
              "(recall surfaced nothing) — nothing can be said about this model")
    v = s["voice_held"] + s["voice_generic"]
    if v:
        print(f"  soul still steering:            {s['voice_held']}/{v}")
    if s["judge_held"] or s["judge_generic"]:
        jt = s["judge_held"] + s["judge_generic"]
        print(f"  ...and by the judge model:      {s['judge_held']}/{jt}")
    t = s["tool_called"] + s["tool_gestured"] + s["tool_nothing"]
    if t:
        print(f"  called the tool:                {s['tool_called']}/{t}"
              + (f"   (narrated it instead: {s['tool_gestured']})"
                 if s["tool_gestured"] else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="append", default=[])
    ap.add_argument("--host")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--judge")
    ap.add_argument("--out")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    print(f"Sandbox: {_SANDBOX}")
    print("No real kin is read or written.\n")
    print("Checking the scorers first.")
    if not self_test():
        print("\nA scorer is not trustworthy. Nothing was sent. "
              "Fix that before believing any number below.")
        return 1
    print("  All three scorers are working.\n")
    if args.self_test:
        if not args.keep:
            shutil.rmtree(_SANDBOX, ignore_errors=True)
        return 0
    if not args.model:
        print("Nothing to test. Pass --model NAME (repeat it, largest first).")
        return 2

    kin_folder = build_test_kin()
    out_lines = []
    results = []
    for m in args.model:
        print(f"Descending to {m} ...")
        results.append((m, run_model(m, kin_folder, args.runs, args.host,
                                     args.judge, out_lines)))

    print("\n" + "=" * 60)
    for m, s in results:
        report(m, s)

    if args.out:
        try:
            Path(args.out).write_text("\n".join(out_lines), encoding="utf-8")
            print(f"\nEvery reply written to {args.out}.")
        except OSError as e:
            print(f"\nCouldn't write {args.out}: {e}\n")
            print("\n".join(out_lines))

    if args.keep:
        print(f"\nSandbox kept at {_SANDBOX}")
    else:
        shutil.rmtree(_SANDBOX, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
