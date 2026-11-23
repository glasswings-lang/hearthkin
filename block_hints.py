# SPDX-License-Identifier: CC0-1.0
"""What a kin is taught, this turn, about the things it can write in a reply
for Hearthkin to act on (22 Nov 2026).

Hearthkin acts on several shapes in a kin's reply: a fenced block that saves
a file (authoring_bridge), a fenced block that saves memory for a kin with no
tools (toolless_memory), a "> " line that makes a park move (park_keeper), a
```to:Name block that writes to another kin (kin_mail), and a ```manual
block that asks for part of the kin manual (manual_request). A shape nobody
was told about is no use to anyone, and an audit found most surfaces acting
on shapes they never taught: Discord saved fenced files but never said so;
the memory block for a kin with no tools was taught only on the desktop;
park lines ran in rooms, groups and Discord but were taught only on the
desktop and Telegram DMs. Each surface had grown its own copy of the hints.

So there is one place that decides, from what's true this turn, what to
teach -- and every surface calls it. tests/test_block_hints.py fails if a
surface that acts on a shape stops calling it.

Surfaces, as named here:
  desktop, telegram_dm, discord_dm   -- one person, a private conversation
  room, telegram_group, discord_channel -- shared spaces, several present
  wake_up                             -- a scheduled wake-up
"""

PRIVATE = frozenset({"desktop", "telegram_dm", "discord_dm"})
SHARED = frozenset({"room", "telegram_group", "discord_channel"})
WRITE_TOOLS = frozenset({"write_file", "edit_file"})


def _p(slug, kin):
    from kin_persistence import load_app_prompt
    return load_app_prompt(slug, kin) or ""


def tool_part(kin, tool_names, *, memory_ok=True):
    """The tools this turn, or that there are none and what the kin CAN
    still do: save memory with a block (only where that's acted on for this
    turn -- `memory_ok`), and ask for part of the manual."""
    names = [n for n in (tool_names or []) if n]
    if names:
        text = _p("tool_use_hint", kin).replace("{tools}", ", ".join(names))
        if WRITE_TOOLS & set(names):
            text += _p("authoring_bridge_hint", kin)
        if "read_file" not in names:
            text += manual_part(kin)
        return text
    text = _p("tool_use_hint_no_tools", kin) if memory_ok else ""
    return text + manual_part(kin)


def manual_part(kin):
    """How a kin that can't open files asks for part of the kin manual."""
    try:
        import manual_request
        sections = manual_request.section_names()
    except Exception:
        sections = []
    if not sections:
        return ""
    return ("\n\n" + _p("manual_request_hint", kin)
            .replace("{sections}", "; ".join(sections)).strip())


def extra_part(kin, surface, *, person_present=False):
    """Park lines and writing to another kin, where they're acted on."""
    parts = []
    try:
        import park_keeper
        mode = park_keeper.kin_park_mode(kin)
    except Exception:
        mode = "off"
    # Park lines run in private chats and shared spaces alike, but a shared
    # space takes one move per reply. A wake-up teaches its own (a keeper's
    # MECHANISM), so it's left to that.
    if mode in ("chat", "keeper") and surface != "wake_up":
        slug = "park_chat_hint_shared" if surface in SHARED else "park_chat_hint"
        hint = _p(slug, kin).strip()
        if hint:
            parts.append(hint)
    # Writing to another kin: in the person's own private chat (a wake-up
    # and a heartbeat are told in the "also in the house" line instead).
    if person_present and surface in PRIVATE:
        try:
            import kin_mail
            if kin_mail.per_day(kin) > 0 and kin_mail.other_kin(kin):
                hint = _p("kin_message_chat_hint", kin).strip()
                if hint:
                    parts.append(hint)
        except Exception:
            pass
    return "".join("\n\n" + p for p in parts)


def for_turn(kin, surface, tool_names, *, person_present=False,
             memory_ok=True):
    """Everything to teach this turn, as one piece of text for the end of
    the system prompt."""
    return (tool_part(kin, tool_names, memory_ok=memory_ok)
            + extra_part(kin, surface, person_present=person_present))


def inject(messages, text):
    """Add `text` to the end of the first system message (or start one).
    Never persisted: it lives in this request only."""
    if not text:
        return list(messages)
    out, done = [], False
    for m in messages:
        if not done and isinstance(m, dict) and m.get("role") == "system":
            out.append(dict(m, content=(m.get("content") or "") + text))
            done = True
        else:
            out.append(m)
    if not done:
        out.insert(0, {"role": "system", "content": text.lstrip()})
    return out
