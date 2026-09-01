"""Reading-bridge tests. Plain Python; run via tests/run_all.py."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reading_bridge import (
    extract_shared_paths,
    read_shared_files,
    build_shared_context_block,
    looks_like_read_gesture,
)

_failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _failures.append(label)


# --- extract_shared_paths: existence is the filter --------------------------
with tempfile.TemporaryDirectory() as td:
    real = os.path.join(td, "the dream.md")   # spaces -> must be quoted
    with open(real, "w", encoding="utf-8") as f:
        f.write("I dreamed of the singing bowl.")
    real2 = os.path.join(td, "notes.txt")
    with open(real2, "w", encoding="utf-8") as f:
        f.write("plain notes")

    # Quoted path with spaces, that exists -> found.
    check(extract_shared_paths(f'you can look: "{real}"') == [real],
          "quoted existing path with spaces -> found")

    # Unquoted Windows/……-style path that exists (no spaces) -> found.
    check(extract_shared_paths(f"here: {real2}") == [real2],
          "unquoted existing path (no spaces) -> found")

    # A path-shaped token that does NOT exist -> ignored (no false read).
    check(extract_shared_paths('see "C:\\nope\\ghost.md" maybe') == [],
          "non-existent path is ignored (existence is the filter)")

    # Filename mentioned in passing, not a real file -> ignored.
    check(extract_shared_paths("the owl.json we talked about") == [],
          "bare filename with no real file -> ignored")

    # A double-quoted path whose FILENAME contains an apostrophe. The old
    # regex excluded every quote character from the path body, so the
    # apostrophe was read as the closing quote and the match truncated —
    # silently dropping the whole path (no error, just nothing found).
    real3 = os.path.join(td, "You're this close.txt")
    with open(real3, "w", encoding="utf-8") as f:
        f.write("real content, not a guess")
    check(extract_shared_paths(f'read this: "{real3}"') == [real3],
          "a filename with an apostrophe, inside double quotes, is still found")

    # And the reverse: a single-quoted path whose filename has a backtick
    # in it (Windows filenames can't hold a literal double quote, so this
    # exercises the same "different delimiter than the wrapper" case).
    real4 = os.path.join(td, "notes `draft`.txt")
    with open(real4, "w", encoding="utf-8") as f:
        f.write("also real")
    check(extract_shared_paths(f"read this: '{real4}'") == [real4],
          "a filename with a backtick, inside single quotes, is still found")

    # read_shared_files reads content, tolerant + capped.
    res = read_shared_files([real])
    check(res[0][1] is True and "singing bowl" in res[0][2],
          "read_shared_files loads content")
    res_big = read_shared_files([real], max_bytes=5)
    check(res_big[0][1] is False and "cap" in res_big[0][2],
          "oversized shared file refused with a steer, not read")

    # build_shared_context_block frames it as really-here.
    block = build_shared_context_block(read_shared_files([real]))
    check("really here" in block and "singing bowl" in block
          and "do not need to call read_file" in block,
          "context block frames shared content as really present")
    check(build_shared_context_block([]) == "", "empty results -> empty block")

# --- looks_like_read_gesture: content-reach vs presence ----------------------
check(looks_like_read_gesture("*reads through it slowly*") == "reads through it slowly",
      "content-reach read-gesture detected")
check(looks_like_read_gesture("*reads the part about the childhood again*")
      == "reads the part about the childhood again",
      "content-reach naming a topic detected")
check(looks_like_read_gesture("*looks at you*") is None,
      "presence-reach (*looks at you*) is NOT a read-gesture")
check(looks_like_read_gesture("*reads your face carefully*") is None,
      "reading the operator (your) is presence, not a file read")
check(looks_like_read_gesture("*smiles* that's kind") is None,
      "feeling-emote is not a read-gesture")
check(looks_like_read_gesture("just talking, no emotes") is None,
      "plain prose -> None")
check(looks_like_read_gesture("") is None, "empty -> None")

# ─── Operator-extendable vocabulary (reach_messages) ──────────────────────────
# The nudge text is editable; the words that TRIGGER it must be too, or the
# operator can change what it says but not when it fires. Additive by design:
# the empty default must behave exactly as the hard-coded baseline did.
import pathlib  # noqa: E402
import kin_persistence as _k  # noqa: E402
import reading_bridge as _rb  # noqa: E402

_k.PROMPTS_DIR = pathlib.Path(tempfile.mkdtemp()) / "prompts"
_k.PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
_reach = _k.PROMPTS_DIR / "reach_messages.md"


def _reset_vocab_cache():
    _rb._REACH_CACHE.update({"text": None})


_reset_vocab_cache()
check(looks_like_read_gesture("*devours the letter*") is None,
      "reach_messages: unknown verb not flagged out of the box")
check(looks_like_read_gesture("*reads the room*") is not None,
      "reach_messages: baseline flags 'reads the room' before any edit")

_reach.write_text("[verbs]\ndevours\n\n[presence]\nroom\n", encoding="utf-8")
_reset_vocab_cache()
check(looks_like_read_gesture("*devours the letter*") is not None,
      "reach_messages: operator-added verb now fires")
check(looks_like_read_gesture("*reads the room*") is None,
      "reach_messages: operator-added presence word protects the emote")
check(looks_like_read_gesture("*reads the notes*") is not None,
      "reach_messages: baseline verbs still fire after an edit (additive)")
check(looks_like_read_gesture("*looks at you*") is None,
      "reach_messages: built-in presence guard survives an edit")

# A bad edit must never break detection — worst case it behaves as baseline.
_reach.write_text("[verbs]\n((((broken regex\n", encoding="utf-8")
_reset_vocab_cache()
check(looks_like_read_gesture("*reads the notes*") is not None,
      "reach_messages: broken edit falls back to baseline, detection survives")

# ─── Text attachments from remote surfaces ────────────────────────────────────
# Telegram and Discord downloaded ONLY images; a .txt/.md/source upload was
# dropped with no note, so "check this out" reached the kin with nothing
# attached and no way for either party to notice.
from reading_bridge import (  # noqa: E402
    is_text_attachment,
    decode_attachment,
    build_attachment_context_block,
    MAX_TEXT_ATTACHMENT_BYTES,
)

check(is_text_attachment("notes.md", "application/octet-stream"),
      "attachment: .md accepted despite octet-stream mime (Telegram's shape)")
check(is_text_attachment("script.py", ""),
      "attachment: extension alone is enough (Discord often sends no mime)")
check(is_text_attachment("data", "application/json"),
      "attachment: mime fallback works for extensionless files")
check(not is_text_attachment("photo.jpg", "image/jpeg"),
      "attachment: images excluded (the image path owns them)")
check(not is_text_attachment("archive.zip", "application/zip"),
      "attachment: binaries excluded")
check(not is_text_attachment("song.mp3", "audio/mpeg"),
      "attachment: audio excluded")

_ok, _txt = decode_attachment("hello".encode("utf-8"))
check(_ok and _txt == "hello", "attachment: utf-8 decodes")
_ok, _txt = decode_attachment(b"He said \x93hi\x94")
check(_ok and "hi" in _txt, "attachment: cp1252 smart quotes don't crash")
_ok, _err = decode_attachment(b"x" * (MAX_TEXT_ATTACHMENT_BYTES + 1))
check(not _ok and "limit" in _err, "attachment: oversized rejected with a reason")
_ok, _err = decode_attachment(b"")
check(not _ok, "attachment: empty rejected")

_blk = build_attachment_context_block(
    [("love it.md", b"# The thing\n")], "Bracken")
check("love it.md" in _blk and "The thing" in _blk,
      "attachment block: names the file and carries its contents")
check(_blk.startswith("[hearthkin:"),
      "attachment block: uses the registered shared-files framing")

# Failures must appear IN the block. A kin that can't read the file should
# still learn a file was sent — silence is the bug being fixed.
_bad = build_attachment_context_block([("huge.log", b"y" * (MAX_TEXT_ATTACHMENT_BYTES + 1))])
check("huge.log" in _bad and "could not load" in _bad,
      "attachment block: oversized file is REPORTED, not silently dropped")
_bad2 = build_attachment_context_block([("broken.txt", b"")])
check("broken.txt" in _bad2 and "could not load" in _bad2,
      "attachment block: failed download is REPORTED, not silently dropped")

check(build_attachment_context_block([]) == "",
      "attachment block: nothing attached -> empty string")

# --- .docx extraction --------------------------------------------------------
# .docx is a zip, not text on disk — these cover the extraction path itself
# (tools/_docx.py) plus both places that dispatch into it: the shared-path
# reader (an operator names a .docx in chat) and the attachment reader (a
# .docx gets uploaded on Telegram/Discord).

import io
import zipfile


def _make_docx_bytes(paragraphs):
    """Build a minimal, real, valid .docx in memory: a zip containing just
    enough of word/document.xml for extract_docx_text to have something real
    to parse. No external file or python-docx dependency needed for the test."""
    body = "".join(
        f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{body}</w:body></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", xml)
    return buf.getvalue()


check(is_text_attachment("letter.docx"),
      "docx: is_text_attachment true by extension alone")
check(not is_text_attachment("legacy.doc"),
      "docx: legacy .doc is NOT claimed as readable (no stdlib path to it)")

_docx_bytes = _make_docx_bytes(["First paragraph.", "Second one, with an & in it."])
_ok, _txt = decode_attachment(_docx_bytes, "letter.docx")
check(_ok and "First paragraph." in _txt and "Second one, with an & in it." in _txt,
      "docx attachment: extracts real paragraph text, entities unescaped")

_ok, _err = decode_attachment(b"not actually a zip", "fake.docx")
check(not _ok and "not a valid .docx" in _err,
      "docx attachment: corrupt file fails cleanly, doesn't crash or return garbage")

_tmp_docx = os.path.join(tempfile.gettempdir(), "hearthkin_test_shared.docx")
with open(_tmp_docx, "wb") as f:
    f.write(_make_docx_bytes(["Shared via a chat-named path, not an upload."]))
try:
    _results = read_shared_files([_tmp_docx])
    _p, _ok, _content = _results[0]
    check(_ok and "Shared via a chat-named path" in _content,
          "docx: read_shared_files extracts text instead of raw zip bytes")
finally:
    os.remove(_tmp_docx)

_docx_block = build_attachment_context_block(
    [("report.docx", _make_docx_bytes(["Quarterly numbers look good."]))], "Bracken")
check("report.docx" in _docx_block and "Quarterly numbers look good." in _docx_block,
      "docx attachment block: uploaded Word doc reads exactly like a text upload")

print()
if _failures:
    print(f"FAILED: {len(_failures)}: {_failures}")
    sys.exit(1)
print("ALL READING-BRIDGE CHECKS PASSED")
