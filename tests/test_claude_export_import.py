# SPDX-License-Identifier: CC0-1.0
"""A claude.ai export sets a kin up: history from the zip, memory as a LOG.

Two things this pins, both of which were missing.

**The download is a .zip.** The importer accepted only the conversations.json
from inside it, so the first step of every import was a manual unpack that
nothing mentioned, and the error you got for pointing at the zip talked about
JSON.

**The export carries the other assistant's memory, and nothing read it.** A
`memories.json` sits beside the conversations holding what that assistant had
come to know about the person. Importing a history brought the conversations
and left behind the one file that was already a summary of them.

Where that memory lands is the load-bearing part. It goes in a DEPTH LOG and
never in memory.md, because memory.md is in the system prompt on every turn
and this text is third-person prose about the person written by somebody else.
A kin handed a clinical summary of someone writes clinical summaries back --
the voice erosion the distillation work exists to undo. The test asserts the
placement, not just that the import happened.

Run: python tests/test_claude_export_import.py
"""

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HEARTHKIN_HOME", tempfile.mkdtemp(prefix="hk_clexp_"))
os.environ.setdefault("HEARTHKIN_SILENT", "1")

import kin_persistence as KP
from importers import claude_json as CJ

_fails = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _fails.append(label)


# ── a stand-in export, shaped like the real one ──────────────────────────────

CONVOS = [
    {
        "uuid": "11111111-1111-1111-1111-111111111111",
        "name": "A thread with words",
        "created_at": "2026-01-02T10:00:00Z",
        "chat_messages": [
            {"sender": "human", "created_at": "2026-01-02T10:00:00Z",
             "content": [{"type": "text", "text": "hello there"}]},
            {"sender": "assistant", "created_at": "2026-01-02T10:00:05Z",
             "content": [{"type": "text", "text": "hello yourself"}]},
        ],
    },
    {
        # The hollow shape a real export produces: envelopes, no bodies.
        "uuid": "22222222-2222-2222-2222-222222222222",
        "name": "",
        "created_at": "2025-11-18T02:38:00Z",
        "chat_messages": [
            {"sender": "human", "created_at": "2025-11-18T02:38:00Z",
             "text": "", "content": []},
            {"sender": "assistant", "created_at": "2025-11-18T02:39:00Z",
             "text": "", "content": []},
        ],
    },
    {
        # The OLD shape: no content list at all, words on top-level `text`.
        "uuid": "33333333-3333-3333-3333-333333333333",
        "name": "An older thread",
        "created_at": "2025-04-01T09:00:00Z",
        "chat_messages": [
            {"sender": "human", "created_at": "2025-04-01T09:00:00Z",
             "text": "written the old way"},
        ],
    },
]

MEMORY_TEXT = ("**Work context**\n\nThe person builds things and works "
               "non-visually. Their projects include a companion framework.")

work = Path(tempfile.mkdtemp(prefix="hk_clexp_src_"))
loose = work / "conversations.json"
loose.write_text(json.dumps(CONVOS), encoding="utf-8")

zip_path = work / "data-abc-batch-0000.zip"
with zipfile.ZipFile(zip_path, "w") as zf:
    zf.writestr("conversations.json", json.dumps(CONVOS))
    zf.writestr("memories.json", json.dumps(
        [{"account_uuid": "x", "conversations_memory": MEMORY_TEXT}]))
    zf.writestr("users.json", json.dumps([{"full_name": "Someone"}]))

decoy = work / "holiday.zip"
with zipfile.ZipFile(decoy, "w") as zf:
    zf.writestr("photos/readme.txt", "not an export")

print("\n-- the download is a .zip, and that is what people have --")

check(CJ.detect_path(str(zip_path)), "an export .zip is recognised")
check(CJ.detect_path(str(loose)), "the loose conversations.json still is too")
check(not CJ.detect_path(str(decoy)),
      "a zip that is not an export is refused, not half-parsed")

from_zip, _, fmt_z = CJ.parse(str(zip_path), "Claude")
from_json, _, fmt_j = CJ.parse(str(loose), "Claude")
check(from_zip == from_json,
      "parsing the zip gives exactly what parsing the json gives")
check(fmt_z == "claude_json", "and reports the same format")

print("\n-- what comes through, and what honestly cannot --")

roles = [m["role"] for m in from_zip]
check(roles.count("user") == 2 and roles.count("assistant") == 1,
      "both message shapes are read: the new `content` list AND old `text`")
check(any("An older thread" in (m.get("content") or "") for m in from_zip),
      "each thread keeps its header so they don't run together")
check(not any("2222" in (m.get("content") or "") for m in from_zip),
      "a thread whose messages have no text at all yields nothing")

print("\n-- the memory comes over, which nothing used to do --")

check(CJ.export_memory(str(zip_path)) == MEMORY_TEXT,
      "memories.json is read out of the zip")
check(CJ.export_memory(str(loose)) == "",
      "...and its absence is simply nothing, never an error")

print("\n-- WHERE it lands is the point --")

KP.create_agent("Importee")
written = KP.write_imported_memory_log("Importee", MEMORY_TEXT)
check(written is not None, "the memory is written")
check(written.name == KP.IMPORTED_MEMORY_LOG,
      "as a depth log, under the kin's memory/ folder")
check(written.parent.name == "memory", "...in memory/, not the kin root")

body = written.read_text(encoding="utf-8")
check(MEMORY_TEXT in body, "the material is complete, not summarised")
check(body.lstrip().startswith("#"),
      "it opens with a heading, so the code-built index can label it")

mem_md = KP.agent_dir("Importee") / "memory.md"
main_memory = mem_md.read_text(encoding="utf-8") if mem_md.exists() else ""
check(MEMORY_TEXT not in main_memory,
      "NOT in memory.md -- that sits in the system prompt on every turn")

print("\n-- the code-owned index finds it without being told --")

indexed = KP.apply_memory_log_index(main_memory, "Importee")
check(KP.IMPORTED_MEMORY_LOG in indexed,
      "writing the file IS registering it; the index is rebuilt from disk")

print("\n-- a kin's own writing is never overruled --")

written.write_text("# my own notes\n\nthings I decided myself.\n",
                   encoding="utf-8")
again = KP.write_imported_memory_log("Importee", MEMORY_TEXT)
check(again is None, "a second import does not overwrite an existing log")
check("things I decided myself" in written.read_text(encoding="utf-8"),
      "...and what the kin wrote is still there, untouched")
check(KP.write_imported_memory_log("Importee", MEMORY_TEXT, overwrite=True)
      is not None,
      "overwrite=True is available for a caller that means it")

print("\n-- nothing to write is never an error --")

check(KP.write_imported_memory_log("Importee", "") is None,
      "empty memory writes nothing")
check(KP.write_imported_memory_log("Importee", "   \n  ") is None,
      "...and neither does whitespace")
check(KP.write_imported_memory_log("", MEMORY_TEXT) is None,
      "no kin named, nothing written, no exception")

print("\n-- the dialog actually calls it (a writer nobody calls is no fix) --")

import ast
src = (ROOT / "dialogs" / "import_history.py").read_text(encoding="utf-8")
tree = ast.parse(src)
called = False
after_history = False
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_maybe_import_claude_memory":
        called = any(isinstance(c, ast.Call)
                     and getattr(c.func, "id", getattr(c.func, "attr", "")) ==
                     "write_imported_memory_log"
                     for c in ast.walk(node))
    if isinstance(node, ast.FunctionDef) and node.name == "_on_import":
        after_history = any(
            isinstance(c, ast.Call)
            and getattr(c.func, "attr", "") == "_maybe_import_claude_memory"
            for c in ast.walk(node))
check(called, "the dialog's memory step writes the log")
check(after_history, "and _on_import invokes it")

if _fails:
    print(f"\nFAILED: {len(_fails)} check(s): {_fails}")
    sys.exit(1)
print("\nALL CHECKS PASSED -- an export brings history AND memory, placed right.")
