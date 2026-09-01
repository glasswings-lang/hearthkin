"""read_file's .docx path. Plain Python; run via tests/run_all.py.

Before this, read_file only had the plain-text decode path — a kin calling
read_file on a .docx it found itself (as opposed to being handed one via the
shared-files bridge, which already had this) got the file's raw zip bytes
decoded as if they were text: unreadable garbage that looked like a read
rather than announcing itself as one.
"""

import io
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.read_file import read_file

_failures = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        _failures.append(label)


def _make_docx_bytes(paragraphs):
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


with tempfile.TemporaryDirectory() as td:
    docx_path = os.path.join(td, "letter.docx")
    with open(docx_path, "wb") as f:
        f.write(_make_docx_bytes(
            ["First real paragraph.", "Second one, with an & in it."]))

    result = read_file(docx_path)
    check("First real paragraph." in result,
          "docx: read_file returns the real extracted text")
    check("Second one, with an & in it." in result,
          "docx: entities are unescaped, second paragraph present")
    check("PK" not in result.split("\n\n[read_file:")[0][:4],
          "docx: no raw zip-header bytes leaking into the result")
    check("extracted text" in result,
          "docx: the footer says what the byte count actually measures")

    # A .docx has no line-level structure worth exposing raw XML for, but
    # the extracted TEXT still has real newlines between paragraphs, and
    # start_line/line_count must slice that the same way a .txt file would.
    sliced = read_file(docx_path, start_line=2, line_count=1)
    check("Second one, with an & in it." in sliced
          and "First real paragraph." not in sliced,
          "docx: start_line/line_count slices the extracted text")

    # A corrupted / misnamed file must fail with a clear reason, never
    # silently return garbage decoded as if it were plain text.
    bad_path = os.path.join(td, "fake.docx")
    with open(bad_path, "wb") as f:
        f.write(b"not actually a zip")
    bad_result = read_file(bad_path)
    check(bad_result.startswith("read_file:") and "not a valid .docx" in bad_result,
          "docx: a corrupt file fails cleanly with a specific reason")

    # A plain .txt file must be completely unaffected by any of this.
    txt_path = os.path.join(td, "notes.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("plain text, unaffected")
    check("plain text, unaffected" in read_file(txt_path),
          "docx change doesn't touch the ordinary text-file path")

print()
if _failures:
    print(f"FAILED: {len(_failures)}: {_failures}")
    sys.exit(1)
print("ALL READ_FILE DOCX CHECKS PASSED")
