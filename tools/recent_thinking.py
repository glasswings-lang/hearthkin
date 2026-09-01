# SPDX-License-Identifier: CC0-1.0

"""Surface the calling kin's recent thinking blocks as plain content.

Reasoning models emit a `thinking` field alongside `content` on each
assistant turn. Hearthkin persists both. Whether the model sees its
own prior thinking on subsequent turns depends on:

  - the per-kin `feed_thinking` config (off by default — even when
    we store the thinking, we don't ship it back to the provider
    unless this is on);
  - whether the provider preserves the reasoning channel as the
    model's own (Anthropic does, OpenAI's o-series doesn't even
    expose it, smaller distilled R1-like models may not recognize
    a reasoning field as theirs).

This tool sidesteps the provider quirks by reading the kin's own
conversation.jsonl and returning the most recent N thinking blocks
as plain text — the model definitely sees it because it lands in
the tool-result content. Useful when the kin is unsure whether their
prior reasoning is in context and wants to recall it directly.

`agent_name` is injected by the framework; the schema-builder hides
it from the model-facing schema.
"""

import json
from pathlib import Path

from hearthkin_paths import kin_dir as kin_folder


def _read_jsonl_messages(path):
    """Yield message dicts from a conversation.jsonl, skipping any
    malformed lines. The on-disk format is one JSON object per line."""
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except Exception:
        return


def recent_thinking(n: int = 3, agent_name: str = "") -> str:
    """Return your most recent N thinking blocks as plain text — the reasoning you produced on prior assistant turns, regardless of whether the provider currently shows it to you in context. Useful when you're not sure whether you can still see your earlier reasoning and want to recall it. Default 3; pass a different number to look further back. No effect on the conversation itself — this is a read-only lookup.

    Returns one block per past turn, newest first, with a short header
    showing the timestamp when available. If you haven't produced any
    thinking yet, or your model doesn't emit a reasoning channel,
    you'll get a clear "no thinking blocks found" message.
    """
    if not agent_name:
        return "recent_thinking: no kin context (framework bug)."
    try:
        n = max(1, min(50, int(n)))
    except (TypeError, ValueError):
        n = 3

    kin_dir = kin_folder(agent_name)
    convo_path = kin_dir / "conversation.jsonl"
    if not convo_path.exists():
        # Legacy kin haven't been migrated yet; fall back to the
        # old single-file format so they aren't blind to this tool.
        legacy = kin_dir / "conversation.json"
        if not legacy.exists():
            return "recent_thinking: no conversation file on disk yet."
        try:
            raw = json.loads(legacy.read_text(encoding="utf-8"))
        except Exception:
            return "recent_thinking: couldn't read legacy conversation file."
        messages = (raw.get("messages") if isinstance(raw, dict) else raw) or []
    else:
        messages = list(_read_jsonl_messages(convo_path))

    # Walk backwards collecting thinking blocks from assistant turns.
    found = []
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        thinking = msg.get("thinking")
        if not isinstance(thinking, str) or not thinking.strip():
            continue
        found.append({
            "thinking": thinking.strip(),
            "ts": msg.get("ts") or "",
            "content_preview": (msg.get("content") or "")[:120].replace("\n", " "),
        })
        if len(found) >= n:
            break

    if not found:
        return (
            "No thinking blocks found in your recent history. Either you "
            "haven't produced any (your model doesn't emit a reasoning "
            "channel, or thinking is off in your config), or none of your "
            "recent assistant turns had non-empty reasoning. Check Settings "
            "→ Model & generation → Thinking effort if you expected to see "
            "reasoning here."
        )

    lines = [f"Your {len(found)} most recent thinking block(s), newest first:\n"]
    for i, entry in enumerate(found, 1):
        ts = entry["ts"]
        preview = entry["content_preview"]
        header = f"--- Block {i}"
        if ts:
            header += f"  (turn at {ts})"
        if preview:
            header += f"\n    reply began: {preview!r}"
        lines.append(header)
        lines.append(entry["thinking"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
