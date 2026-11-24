# SPDX-License-Identifier: CC0-1.0
"""Distillation as bookmarks, checked by plain code (24 Nov 2026).

The distiller used to retell a stretch of conversation in its own words,
smaller. Measured on one kin's history: 22,455 messages from March to
November became 58 sections of about 800 words, a fiftieth of what happened,
in five minutes. It flattened, and it invented, and the person only caught it
by happening to read a depth log built from it.

Now the distiller writes bookmarks: which rows of the stretch a moment
covers, a line or two on what it was, and people's exact words. A bookmark
points at the real record instead of replacing it, so it can be coarse but
can't distort. And this module checks what a model can't be trusted to:

  * every quote is looked up in the rows its bookmark names; one that isn't
    there is flagged (never removed: flags, not filters);
  * the bookmarks cover the whole stretch, in order, no gaps; any rows left
    out get a bookmark of their own saying nothing there was summarised;
  * each bookmark's row numbers (which only mean something inside one
    distillation) are turned into dates, and into conversation.jsonl line
    numbers where the rows are stored there, so the kin can go and read them.

Pure functions, no model and no files except the optional line lookup.
"""

import json
import re

# "## #12-#40 · the color" (any dash, optional spaces, optional label).
_HEAD_RE = re.compile(
    r"^##[ \t]*#(\d+)[ \t]*(?:[-–—]|to)[ \t]*#?(\d+)[ \t]*(?:[·:|-][ \t]*)?(.*)$",
    re.MULTILINE)
_HEAD_ONE_RE = re.compile(r"^##[ \t]*#(\d+)(?![\d#\-–—])[ \t]*(?:[·:|-][ \t]*)?(.*)$",
                          re.MULTILINE)
# Text between double quotes, straight or curly.
_QUOTE_RE = re.compile(r"[\"“]([^\"“”\n]{3,}?)[\"”]")

# A quote shorter than this many words isn't checked: two words ("thank
# you") are everywhere, so finding them proves nothing.
MIN_QUOTE_WORDS = 3
# Rows either side of a bookmark's range that a quote may also come from,
# for a model that's one or two rows off.
SLACK_ROWS = 2

FLAG_QUOTE = ("[hearthkin: these words aren't in rows {a} to {b} as written. "
              "Check the record before trusting them.]")
GAP_BODY = ("[hearthkin: the distiller left these rows out. Nothing here was "
            "summarised; read them in the record if they matter.]")
FLAG_NOT_BOOKMARKS = ("[hearthkin: these notes aren't bookmarks, so nothing in "
                      "them could be checked against the record. Treat them as "
                      "a sketch, and read the record for anything that matters.]")


def _norm(text):
    t = (text or "").lower()
    t = (t.replace("’", "'").replace("‘", "'")
          .replace("“", '"').replace("”", '"')
          .replace("—", "-").replace("–", "-").replace("…", "..."))
    t = re.sub(r"[*_`]", "", t)
    return " ".join(t.split())


def parse(text):
    """The bookmarks in a distiller's reply, in the order written:
    a list of dicts {start, end, label, head_at, body_at, body_end}."""
    text = text or ""
    heads = []
    for m in _HEAD_RE.finditer(text):
        a, b = int(m.group(1)), int(m.group(2))
        heads.append((m.start(), m.end(), min(a, b), max(a, b), m.group(3).strip()))
    for m in _HEAD_ONE_RE.finditer(text):
        if any(h[0] == m.start() for h in heads):
            continue
        n = int(m.group(1))
        heads.append((m.start(), m.end(), n, n, m.group(2).strip()))
    heads.sort()
    out = []
    for i, (hs, he, a, b, label) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        out.append({"start": a, "end": b, "label": label,
                    "head_at": hs, "body_at": he, "body_end": end})
    return out


def gaps(marks, n_rows):
    """Row ranges (1-based, inclusive) that no bookmark covers."""
    covered = [False] * (n_rows + 1)
    for m in marks:
        for r in range(max(1, m["start"]), min(n_rows, m["end"]) + 1):
            covered[r] = True
    out, r = [], 1
    while r <= n_rows:
        if not covered[r]:
            s = r
            while r <= n_rows and not covered[r]:
                r += 1
            out.append((s, r - 1))
        else:
            r += 1
    return out


def _when(rows, a, b):
    ts = [str((rows[i - 1] or {}).get("ts") or "") for i in range(a, b + 1)
          if 1 <= i <= len(rows)]
    ts = [t for t in ts if t]
    if not ts:
        return ""
    first, last = ts[0][:16].replace("T", " "), ts[-1][:16].replace("T", " ")
    if first == last:
        return first
    if first[:10] == last[:10]:
        return f"{first} to {last[11:]}"
    return f"{first} to {last}"


def _lines(rows, a, b, line_of):
    if not line_of:
        return ""
    nums = []
    for i in range(a, b + 1):
        if 1 <= i <= len(rows):
            n = line_of(rows[i - 1])
            if n:
                nums.append(n)
    if not nums:
        return ""
    lo, hi = min(nums), max(nums)
    return f"conversation.jsonl line {lo}" if lo == hi else f"conversation.jsonl lines {lo} to {hi}"


def _where(rows, a, b, line_of):
    bits = [x for x in (_when(rows, a, b), _lines(rows, a, b, line_of)) if x]
    return " · ".join(bits)


def check(text, rows, line_of=None):
    """The distiller's reply, checked and made findable: flagged quotes,
    gap bookmarks for rows left out, and dates (and line numbers) in every
    heading. `rows` are the stretch's rows in the order they were numbered
    (#1 is rows[0]); `line_of(row)` gives a row's conversation.jsonl line
    number, or None. Returns (text, report) where report counts what was
    found: {bookmarks, quotes, unfound, gap_rows}. Never raises."""
    report = {"bookmarks": 0, "quotes": 0, "unfound": 0, "gap_rows": 0}
    try:
        text = (text or "").strip()
        n = len(rows or [])
        marks = parse(text)
        report["bookmarks"] = len(marks)
        if not text:
            return text, report
        if not marks:
            return FLAG_NOT_BOOKMARKS + "\n\n" + text, report

        normed = [_norm(str((r or {}).get("content") or "")) for r in rows]
        lead = text[:marks[0]["head_at"]].strip()
        placed = []  # (first row, text), sorted at the end
        for m in marks:
            a, b = m["start"], m["end"]
            where = _where(rows, a, b, line_of)
            head = f"## {m['label'] or 'rows ' + str(a) + ' to ' + str(b)}"
            head += f" ({where})" if where else f" (rows {a} to {b})"
            body = text[m["body_at"]:m["body_end"]].strip("\n")
            lo, hi = max(1, a - SLACK_ROWS), min(n, b + SLACK_ROWS)
            hay = " \n ".join(normed[lo - 1:hi])
            new_lines = []
            for line in body.split("\n"):
                new_lines.append(line)
                bad = False
                for q in _QUOTE_RE.findall(line):
                    if len(q.split()) < MIN_QUOTE_WORDS:
                        continue
                    report["quotes"] += 1
                    nq = _norm(q).strip(" .,!?;:'\"")
                    if nq and nq not in hay:
                        report["unfound"] += 1
                        bad = True
                if bad:
                    new_lines.append(FLAG_QUOTE.format(a=a, b=b))
            placed.append((a, head + "\n" + "\n".join(new_lines).strip("\n")))

        for a, b in gaps(marks, n):
            report["gap_rows"] += b - a + 1
            where = _where(rows, a, b, line_of)
            placed.append((a, f"## Not bookmarked ({where or f'rows {a} to {b}'})\n{GAP_BODY}"))
        placed.sort(key=lambda p: p[0])
        out = ([lead] if lead else []) + [t for _, t in placed]
        return "\n\n".join(x for x in out if x.strip()), report
    except Exception:
        return text, report


def line_finder(jsonl_path):
    """A line_of(row) for check(): the 1-based line in conversation.jsonl
    holding a row with the same time and words. None when the file can't be
    read. Read once per distillation."""
    try:
        index = {}
        with open(jsonl_path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                key = (str(r.get("ts") or ""), str(r.get("content") or "")[:200])
                index.setdefault(key, i)
    except Exception:
        return None

    def line_of(row):
        if not isinstance(row, dict):
            return None
        return index.get((str(row.get("ts") or ""), str(row.get("content") or "")[:200]))
    return line_of
