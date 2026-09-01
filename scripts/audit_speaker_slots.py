# SPDX-License-Identifier: CC0-1.0
"""Find turns filed under the kin that somebody else said.

READ-ONLY, and it prints NO message content — only counts, names and
sources. You can paste its output anywhere without exposing a
conversation.

Why it exists: stored turns carry a `speaker` field, but the model
never sees it (it isn't in llm_backend._API_MESSAGE_FIELDS, so it's
stripped before every send). `role` is the only signal that reaches the
model. So a turn stored as role=assistant with somebody else's
`speaker` isn't a cosmetic mislabel — to the kin, reading its own
history back, it said that.

    python scripts/audit_speaker_slots.py
    python scripts/audit_speaker_slots.py <kin> <kin>

What it flags, per kin and per file:

  * assistant-slot turns whose `speaker` is somebody other than the kin
    — the actual damage;
  * assistant-slot turns carrying a `sender_attribution`, which only
    ever belongs on a user turn and means a third party was filed as
    the kin by an importer that overwrote their name;
  * user-slot turns whose `speaker` IS the kin — the mirror image, the
    kin's own words demoted;
  * how many distinct speakers each file holds, since a two-party
    assumption only does damage where there were more than two.

A clean run says so. It changes nothing on disk.
"""

import collections
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hearthkin_paths import config_dir  # noqa: E402

HOME = config_dir()
KIN_DIR = HOME / "kin"


def _iter_jsonl(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for n, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield n, json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _iter_history_json(path):
    """telegram_history.json / discord_history.json: {key: [msgs]}."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    for key, msgs in data.items():
        if not isinstance(msgs, list):
            continue
        for n, m in enumerate(msgs, 1):
            if isinstance(m, dict):
                yield f"{key}#{n}", m


def audit_stream(kin, label, items):
    stray, demoted, attributed = [], 0, 0
    speakers = collections.Counter()
    sources = collections.Counter()
    total = 0
    for _n, m in items:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        total += 1
        speaker = (m.get("speaker") or "").strip()
        if speaker:
            speakers[speaker] += 1
        if role == "assistant":
            if speaker and speaker != kin:
                stray.append(speaker)
                sources[m.get("source") or "(no source recorded)"] += 1
            if m.get("sender_attribution"):
                attributed += 1
                sources[m.get("source") or "(no source recorded)"] += 1
        elif speaker == kin:
            demoted += 1
    if not (stray or demoted or attributed):
        return None
    return {
        "label": label, "total": total, "stray": collections.Counter(stray),
        "demoted": demoted, "attributed": attributed,
        "speakers": speakers, "sources": sources,
    }


def report(kin, findings):
    print(f"\n=== {kin} ===")
    for f in findings:
        print(f"  {f['label']}  ({f['total']:,} turns, "
              f"{len(f['speakers'])} distinct speakers)")
        if f["stray"]:
            n = sum(f["stray"].values())
            print(f"    !! {n:,} turns in {kin}'s slot were said by "
                  f"someone else:")
            for name, count in f["stray"].most_common():
                print(f"         {name}: {count:,}")
        if f["attributed"]:
            print(f"    !! {f['attributed']:,} turns in {kin}'s slot carry a "
                  f"sender attribution (only user turns should)")
        if f["demoted"]:
            print(f"    ?  {f['demoted']:,} of {kin}'s own turns are in the "
                  f"user slot")
        if f["sources"]:
            print("       came in via: " + ", ".join(
                f"{k} ({v:,})" for k, v in f["sources"].most_common()))


def main(argv):
    if not KIN_DIR.is_dir():
        print(f"No kin directory at {KIN_DIR}")
        return 1
    wanted = [a for a in argv[1:] if not a.startswith("-")]
    kin_names = sorted(
        d.name for d in KIN_DIR.iterdir()
        if d.is_dir() and (not wanted or d.name in wanted))
    if not kin_names:
        print("No matching kin.")
        return 1

    any_findings = False
    for kin in kin_names:
        d = KIN_DIR / kin
        findings = []
        conv = d / "conversation.jsonl"
        if conv.is_file():
            r = audit_stream(kin, "conversation.jsonl", _iter_jsonl(conv))
            if r:
                findings.append(r)
        for name in ("telegram_history.json", "discord_history.json"):
            p = d / name
            if p.is_file():
                r = audit_stream(kin, name, _iter_history_json(p))
                if r:
                    findings.append(r)
        if findings:
            any_findings = True
            report(kin, findings)

    print()
    if not any_findings:
        print(f"Clean — checked {len(kin_names)} kin, found no turns filed "
              f"under the wrong speaker.")
        # Say what was actually examined. A bare "clean" is the same
        # output a broken checker produces, and the difference matters
        # more than the reassurance does.
        print("(Checked conversation.jsonl plus any telegram/discord "
              "history, for turns whose stored speaker disagrees with "
              "the slot they're in.)")
    else:
        print("Turns marked !! are ones the kin reads back as its own "
              "words. Nothing has been changed — this only looked.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
