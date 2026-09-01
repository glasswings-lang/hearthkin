# SPDX-License-Identifier: CC0-1.0

"""Extract plain text from Microsoft Word .docx files.

A .docx is a zip archive; the readable text lives in word/document.xml as a
tree of <w:p> (paragraph) and <w:t> (text run) elements. This uses only the
standard library (zipfile + a tolerant tag-strip) rather than adding
python-docx as a dependency — the need here is "get the readable text out
for a kin to read", not full document-model access (styles, tables as
objects, comments, tracked changes), so the heavier dependency isn't worth
the bundle-size cost. See requirements.txt for the project's general stance
on optional/heavy deps (trafilatura, rank_bm25) — same tradeoff here.

Legacy .doc (pre-2007 binary/OLE format) is deliberately NOT handled here —
there's no practical stdlib path to its text, and attempting robust_decode
on it would silently return zip-header/binary garbage dressed up as
content. Callers should report it as unsupported instead.
"""

import html
import io
import re
import zipfile
from pathlib import Path

DOCX_EXTS = frozenset((".docx",))

# Word closes every paragraph with </w:p>; turning that into a newline
# before stripping tags is what keeps extracted text readable as
# paragraphs instead of one run-on line with no breaks at all.
_PARA_END_RE = re.compile(r"</w:p>")
_TAG_RE = re.compile(r"<[^>]+>")
# A table/section-break run can emit several empty paragraphs in a row;
# collapse those down without erasing intentional single-blank-line spacing.
_EXTRA_BLANKS_RE = re.compile(r"\n{3,}")


class DocxExtractionError(Exception):
    """Raised when input can't be parsed as a valid .docx. The message is
    written to be shown directly to a kin/operator, not just logged."""


def extract_docx_text(path_or_bytes):
    """Return the plain text of a .docx file's main body.

    Accepts a path (str/Path, read from disk) or raw bytes (for attachment
    uploads that never touch disk — e.g. a Telegram/Discord upload handled
    in memory). Raises DocxExtractionError on anything that isn't a valid
    docx, so callers can show a clear reason instead of silently returning
    garbage decoded from zip/binary bytes.
    """
    source = (io.BytesIO(path_or_bytes)
              if isinstance(path_or_bytes, (bytes, bytearray))
              else Path(path_or_bytes))
    try:
        zf = zipfile.ZipFile(source)
    except zipfile.BadZipFile as e:
        raise DocxExtractionError(
            f"not a valid .docx (not a zip archive): {e}") from e
    except FileNotFoundError as e:
        raise DocxExtractionError(f"file not found: {e}") from e

    with zf:
        try:
            xml_bytes = zf.read("word/document.xml")
        except KeyError as e:
            raise DocxExtractionError(
                "not a valid .docx (missing word/document.xml — this may "
                "be a legacy .doc file renamed with a .docx extension, or "
                "a different zip-based format entirely)") from e

    xml_text = xml_bytes.decode("utf-8", errors="replace")
    with_breaks = _PARA_END_RE.sub("\n", xml_text)
    stripped = _TAG_RE.sub("", with_breaks)
    # document.xml only ever emits the five predefined XML entities
    # (&amp; &lt; &gt; &quot; &apos;) plus occasional numeric entities for
    # curly quotes/em-dashes; html.unescape covers all of that without
    # pulling in a full XML parser just to get plain text back out.
    text = html.unescape(stripped)
    text = _EXTRA_BLANKS_RE.sub("\n\n", text)
    return text.strip()
