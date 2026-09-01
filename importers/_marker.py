# SPDX-License-Identifier: CC0-1.0

"""
System-marker generation for imported history blocks.

Two markers bracket every imported block in conversation.jsonl: a
leading note that names the substance honestly (what got imported,
how many turns, what date range, what source) and a trailing note
that closes the block so the kin can tell where seed history ends
and lived turns begin.

Both markers land as role=system messages with a `[hearthkin: ...]`
prefix — the same convention used by the truncation marker, the
salvage marker, and the cap-full marker (see CLAUDE.md). The kin
reads them as ambient context, not as user turns.

WORDING IS LOAD-BEARING HERE — DO NOT SOFTEN IT.

An import is the voice-anchoring mechanism: the way a kin's voice is
established or restored is real transcripts of that kin talking, not
more soul-prompt rules. **The specific wordings are the payload** —
cadence, register, sentence shape, what they don't say.

Every base model pulls toward its own house voice; the imported turns
are the counterweight. So the leading marker must assert ownership of
the *phrasing*, not just the facts. An earlier version of this file
said "treat it as your own past... you don't need to defend specific
wordings" — which released the counterweight at the moment it was
installed, and would surface to an operator as "the import didn't
take" / "they don't sound like themselves," with no traceable path
back to this file. See docs/private/VALUES-AUDIT.md finding #2.

Rules for anyone editing these strings:
  - State what the history IS. Don't instruct a stance toward it
    ("treat it as", "you may consider this") — a stance instruction
    reads as permission to drop it.
  - Never invite the kin to loosen its hold on specific wordings.
  - The trailing marker marks a LOCATION boundary (where seed history
    ends), not an ontological one. Don't imply the earlier turns were
    less real, or the leading marker's grant is silently revoked.
"""

import datetime


def _fmt_date(ts_iso):
    """Render an ISO timestamp as 'MM-YYYY' for the human-readable
    date range. Returns '?' if the timestamp is missing or unparseable."""
    if not ts_iso:
        return "?"
    try:
        dt = datetime.datetime.fromisoformat(ts_iso)
        return dt.strftime("%m-%Y")
    except (ValueError, TypeError):
        return "?"


def leading_marker(count, source_label, source_description,
                   first_ts=None, last_ts=None, operator_name=None,
                   kin_name=None):
    """Return a role=system dict announcing the start of an imported
    block. `source_label` is a short tag like 'telegram_dm' or
    'hand_authored'; `source_description` is a sentence-fragment for
    the kin to read ('a Telegram DM', 'a hand-authored seed
    history').

    `operator_name`, when provided, is the name the operator went by
    in the source — used to give the distillation summarizer a real
    name to attribute the operator's turns to. Without this, the
    summarizer grabs 'hearthkin' from this very marker's prefix and
    starts labelling the operator's past actions as
    '[hearthkin] did X', which is structurally wrong (hearthkin is
    the harness, not the operator). The canonical writer derives this
    automatically when there's exactly one unique non-kin speaker
    across the imported turns; group imports with multiple non-kin
    speakers omit it (each turn carries its own inline attribution).
    """
    from kin_persistence import load_app_prompt

    date_range = ""
    if first_ts or last_ts:
        date_range = f", {_fmt_date(first_ts)} to {_fmt_date(last_ts)}"

    operator_clause = ""
    if operator_name:
        operator_clause = load_app_prompt(
            "import_marker_operator_clause", kin_name
        ).replace("{operator_name}", str(operator_name))

    # Different framings for "this is history you lived" vs "this is
    # history the operator wrote for us." Both state plainly what the
    # block is; neither instructs a stance toward it. The hand-authored
    # one names the operator as author because that's true, and a kin
    # that later learns it was seeded shouldn't find it was fudged.
    slug = ("import_marker_hand_authored" if source_label == "hand_authored"
            else "import_marker_leading")
    content = (load_app_prompt(slug, kin_name)
               .replace("{count}", str(count))
               .replace("{source}", str(source_description))
               .replace("{date_range}", date_range)
               .replace("{operator_clause}", operator_clause))

    return {
        "role": "system",
        "content": content,
        "ts": first_ts or _now_iso(),
        "source": f"import:{source_label}",
    }


def trailing_marker(source_label, last_ts=None, kin_name=None):
    """Return a role=system dict closing the imported block.
    Important when an import is appended to an existing kin's
    conversation — without the trailing marker the kin can't tell
    where the carried-over history ends and turns taken here begin.

    Marks a LOCATION boundary only. The previous wording ("turns after
    this point are lived in real time") implied the imported turns were
    not lived, silently revoking the leading marker's grant. See the
    module docstring."""
    from kin_persistence import load_app_prompt
    return {
        "role": "system",
        "content": load_app_prompt("import_marker_trailing", kin_name),
        "ts": last_ts or _now_iso(),
        "source": f"import:{source_label}",
    }


def _now_iso():
    """Best-effort ISO timestamp for markers when no anchor is available."""
    return datetime.datetime.now().replace(microsecond=0).isoformat()
