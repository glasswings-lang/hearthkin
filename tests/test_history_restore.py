# SPDX-License-Identifier: CC0-1.0
"""Guard test: restoring a kin's OWN conversation.jsonl.

A restore is not an import. Foreign history goes through
write_imported_history, which brackets it in "[hearthkin: imported ...]"
markers and stamps every row `source: import:<label>` — right, because the
kin is being told it's reading something carried in from elsewhere.

A kin's own archived turns are not carried in from elsewhere. Sending them
down the import path would relabel the kin's own past as seed history it
"may not remember writing" and overwrite the `source` recording where each
turn actually came from. These checks pin the difference: provenance
survives untouched, no marker is invented, duplicates don't double up, and
the import dispatcher refuses our own format rather than mangling it.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from importers import hearthkin_jsonl  # noqa: E402
from importers._canonical import restore_rows  # noqa: E402

_fails = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        _fails.append(label)


def _row(content, ts=None, role="user", source=None, **extra):
    m = {"role": role, "content": content}
    if ts:
        m["ts"] = ts
    if source:
        m["source"] = source
    m.update(extra)
    return m


def _write_jsonl(rows):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as h:
        for r in rows:
            h.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def main():
    # ---- detection -------------------------------------------------
    native = _write_jsonl([
        _row("hey", "2026-06-06T00:33:20", source="telegram:123"),
        _row("hey ther", "2026-06-06T00:33:40", role="assistant"),
    ])
    check("detects our own conversation.jsonl",
          hearthkin_jsonl.detect(open(native, encoding="utf-8").read()))

    check("rejects rows with an unknown role",
          not hearthkin_jsonl.detect('{"role": "narrator", "content": "x"}'))
    check("rejects rows with no content field",
          not hearthkin_jsonl.detect('{"role": "user", "ts": "2026-01-01T00:00:00"}'))
    check("rejects plain text", not hearthkin_jsonl.detect("hello\nthere\n"))
    check("rejects an empty file", not hearthkin_jsonl.detect(""))

    # ---- parse keeps rows verbatim ---------------------------------
    parsed = hearthkin_jsonl.parse(native)
    check("parse returns every row", len(parsed) == 2)
    check("parse keeps the original source",
          parsed[0].get("source") == "telegram:123")
    os.remove(native)

    # A half-written tail line (app died mid-append) is skipped, not fatal.
    fd, torn = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as h:
        h.write(json.dumps(_row("ok", "2026-06-06T00:00:00")) + "\n")
        h.write('{"role": "user", "cont')
    check("a torn final line doesn't lose the whole file",
          len(hearthkin_jsonl.parse(torn)) == 1)
    os.remove(torn)

    # ---- the wrapped container reads the same as the line one ------
    # Snapshots ({agent_name, snapshotted_at, source, messages}) and room
    # files ({saved_at, messages}) hold the identical turns in a messages
    # array. Same rows, different wrapper — both must read.
    inner = [_row("hey", "2026-06-25T10:00:00", source="telegram:1"),
             _row("hey ther", "2026-06-25T10:00:11", role="assistant")]
    for wrapper, label in (
        ({"agent_name": "K", "snapshotted_at": "x", "source": "y",
          "messages": inner}, "snapshot"),
        ({"saved_at": "x", "messages": inner}, "room file"),
        (inner, "bare array"),
    ):
        fd, wp = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as h:
            json.dump(wrapper, h)
        text = open(wp, encoding="utf-8").read()
        check("detects a %s" % label, hearthkin_jsonl.detect(text))
        got = hearthkin_jsonl.parse(wp)
        check("%s yields its turns" % label, len(got) == 2)
        check("%s keeps source" % label, got[0].get("source") == "telegram:1")
        os.remove(wp)

    # A JSON object that isn't ours stays rejected — a `messages` array of
    # foreign-shaped rows must not be mistaken for a hearthkin file.
    check("rejects a wrapper whose rows aren't ours",
          not hearthkin_jsonl.detect(
              json.dumps({"messages": [{"from": "someone", "text": "hi"}]})))
    check("rejects a JSON object with no messages array",
          not hearthkin_jsonl.detect(json.dumps({"saved_at": "x"})))

    # ---- THE invariant: provenance survives ------------------------
    incoming = [
        _row("a", "2026-06-06T00:00:00", source="telegram:group:-99"),
        _row("b", "2026-06-06T00:01:00", role="assistant", source=None,
             speaker="ExampleKin", model="some-model:7b"),
    ]
    rows, stats = restore_rows([], incoming)
    check("restore keeps source untouched",
          rows[0].get("source") == "telegram:group:-99")
    check("restore never stamps an import: source",
          not any(str(r.get("source", "")).startswith("import:") for r in rows))
    check("restore keeps speaker and model",
          rows[1].get("speaker") == "ExampleKin"
          and rows[1].get("model") == "some-model:7b")
    check("restore invents no marker",
          not any("[hearthkin:" in (r.get("content") or "") for r in rows))
    check("restore counts what it wrote", stats["restored"] == 2)

    # ---- duplicates don't double up --------------------------------
    existing = [_row("a", "2026-06-06T00:00:00", source="telegram:group:-99")]
    rows, stats = restore_rows(existing, incoming)
    check("an overlapping turn is skipped", stats["skipped_duplicates"] == 1)
    check("the fresh turn still lands", stats["restored"] == 1)
    check("no duplicate row in the result",
          [r.get("content") for r in rows].count("a") == 1)

    # ---- untimestamped turns are never matched as duplicates -------
    # _clean_chat_message normalises content:None to "" on assistant
    # tool-call turns, so every untimestamped one collapses to the same
    # ("assistant", "", None). Keying on that deletes real turns — and
    # deleting an assistant tool-call turn orphans the tool result that
    # answered it, which is the shape providers reject outright.
    def _call(cid):
        return {"role": "assistant", "content": "",
                "tool_calls": [{"id": cid,
                                "function": {"name": "f", "arguments": "{}"}}]}

    def _result(cid):
        # Distinct content per result, matching real data: tool results say
        # different things, while the assistant turns that called them are
        # all content="". So a content-based key collapses the calls but not
        # the results, and the results are left pointing at nothing.
        return {"role": "tool", "content": "done " + cid, "tool_call_id": cid}

    convo = []
    for cid in ("a1", "a2", "a3", "a4"):
        convo.append(_call(cid))
        convo.append(_result(cid))
    rows, stats = restore_rows([], convo)
    check("identical-looking untimestamped turns are all kept",
          stats["skipped_duplicates"] == 0)
    check("every untimestamped turn survives",
          len(rows) == len(convo))

    ids = {tc["id"] for r in rows for tc in (r.get("tool_calls") or [])}
    orphans = [r for r in rows
               if r.get("tool_call_id") and r["tool_call_id"] not in ids]
    check("no tool result is left without its call", not orphans)

    # The same shape WITH timestamps still dedupes against what's there.
    stamped = dict(_call("b1"), ts="2026-06-06T00:00:00")
    rows, stats = restore_rows([stamped], [stamped])
    check("a genuinely repeated timestamped turn is still caught",
          stats["skipped_duplicates"] == 1)

    # Two real tool turns sharing a timestamp stay distinct.
    t = "2026-06-06T00:00:00"
    rows, stats = restore_rows(
        [], [dict(_call("c1"), ts=t), dict(_call("c2"), ts=t)])
    check("tool turns sharing a timestamp aren't collapsed",
          stats["skipped_duplicates"] == 0 and len(rows) == 2)

    # ---- merge weaves by ts, existing order untouched --------------
    existing = [
        _row("E1", "2026-07-10T00:00:00"),
        _row("E2", "2026-07-01T00:00:00"),   # non-monotonic on purpose
    ]
    rows, _ = restore_rows(existing, [_row("R1", "2026-06-01T00:00:00")])
    order = [r.get("content") for r in rows]
    check("older restored turns prepend", order[0] == "R1")
    check("existing order is preserved even when its ts runs backwards",
          order.index("E1") < order.index("E2"))

    # ---- unstamped restored turns stay where they sat --------------
    # A restored conversation legitimately contains turns with no ts.
    # Sorting those on `ts or ""` drops every one to the very front,
    # because "" sorts before every real timestamp — which would rebuild
    # the kin's history opening with a scrambled block. They must stay
    # adjacent to the stamped turn they followed.
    existing = [_row("LIVE", "2026-07-03T00:00:00")]
    fresh = [
        _row("R1", "2026-06-01T00:00:00"),
        _row("R2-nots"),                       # no ts — follows R1
        _row("R3", "2026-06-02T00:00:00"),
        _row("R4-nots"),                       # no ts — follows R3
    ]
    order = [r.get("content") for r in restore_rows(existing, fresh)[0]]
    check("unstamped restored turns don't float to the front",
          order[0] == "R1")
    check("an unstamped turn stays with the turn it followed",
          order == ["R1", "R2-nots", "R3", "R4-nots", "LIVE"])

    # ---- replace drops what was there ------------------------------
    rows, _ = restore_rows(existing, [_row("R1", "2026-06-01T00:00:00")],
                           mode="replace")
    check("replace keeps only the restored turns",
          [r.get("content") for r in rows] == ["R1"])

    # ---- malformed rows are dropped, not fatal ---------------------
    rows, stats = restore_rows([], [_row("ok", "2026-06-06T00:00:00"),
                                    {"role": "wat", "content": "x"}])
    check("a malformed row is dropped and counted",
          stats["dropped"] == 1 and stats["restored"] == 1)

    # ---- backups land in a subfolder, not beside the real file -----
    # A loose conversation.jsonl.bak.<stamp> sits in the same file picker
    # as the conversation itself, and restore duly offered one as a
    # source. Restoring a backup of the file you're restoring into
    # re-adds every unmatched turn. Keep the undo history out of the way.
    import shutil as _shutil
    import importers._canonical as C

    kin_dir = tempfile.mkdtemp()
    conv = os.path.join(kin_dir, "conversation.jsonl")
    with open(conv, "w", encoding="utf-8") as h:
        h.write(json.dumps(_row("hi", "2026-06-06T00:00:00")) + "\n")

    real_agent_dir, real_conv_path = C.agent_dir, C._conversation_jsonl_path
    import pathlib
    C.agent_dir = lambda name: pathlib.Path(kin_dir)
    C._conversation_jsonl_path = lambda name: pathlib.Path(conv)
    try:
        made = C._backup_conversation("AnyKin")
        check("a backup is written", made is not None and os.path.exists(made))
        check("the backup goes in backups/",
              os.path.basename(os.path.dirname(str(made))) == "backups")
        check("no loose .bak beside the conversation",
              not [f for f in os.listdir(kin_dir) if ".bak" in f])
        open(conv, "w", encoding="utf-8").close()      # now empty
        check("an empty conversation backs up to nothing",
              C._backup_conversation("AnyKin") is None)
        C._conversation_jsonl_path = lambda name: pathlib.Path(
            os.path.join(kin_dir, "not-here.jsonl"))
        check("a missing conversation backs up to nothing",
              C._backup_conversation("AnyKin") is None)
    finally:
        C.agent_dir, C._conversation_jsonl_path = real_agent_dir, real_conv_path
        _shutil.rmtree(kin_dir, ignore_errors=True)

    # ---- the dispatcher refuses our own format ---------------------
    from importers import ImportError as HkImportError, parse_history
    native = _write_jsonl([_row("hey", "2026-06-06T00:33:20")])
    try:
        parse_history(native, "SomeKin")
        check("parse_history refuses a hearthkin conversation.jsonl", False)
    except HkImportError as e:
        check("parse_history refuses a hearthkin conversation.jsonl",
              "restore_from_file" in str(e))
    except Exception as e:  # noqa: BLE001
        check("parse_history refuses a hearthkin conversation.jsonl "
              "(got %s)" % type(e).__name__, False)
    os.remove(native)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), "; ".join(_fails)))
        return 1
    print("all restore checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
