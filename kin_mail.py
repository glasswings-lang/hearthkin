# SPDX-License-Identifier: CC0-1.0
"""Kin writing to each other (22 Nov 2026).

A kin on a heartbeat or a wake-up can write to another kin in this house:
with the `message_kin` tool, or, for a kin without tools, by writing a block
that opens ```to:Name. The other kin is given the message at its own next
heartbeat or wake-up, and may answer.

Why it exists: Kimi wrote to Opal during a heartbeat with the only tool it
had, reach_out, which went to its default place -- a Telegram group -- so a
message for Opal landed in front of people Opal has never met. Nothing
anywhere let one kin reach another.

The person's rule for this (docs/design/ai-to-ai-async.md): they must be
able to read every message between kin, easily, at any time. So every
message is written in plain text to one file per pair of kin, in
~/.hearthkin/kin_messages/, and Kin > "Messages between kin..." opens it.

A limit per day: each kin may write so many messages to each other kin a
day, its own setting ("Messages it may write to each other kin a day", in
its heartbeat settings), 1 unless the person changed it -- so by default a
message and its answer, then the pair is quiet until tomorrow. Two kin
answering each other on every wake could otherwise run all day, and nobody
would be reading it live. 0 turns a kin's writing off.

Matching is by the kin's folder name, ignoring case. Never by guessing from
prose: small models were tested asking "was that for Opal?" and answered
"Opal" for a journal note ABOUT Opal as readily as for one TO him.
"""

import datetime
import json
import re

FENCE_RE = re.compile(
    r"```[ \t]*to[ \t]*[:\s][ \t]*([^\n`]+?)[ \t]*\n(.*?)```",
    re.IGNORECASE | re.DOTALL)


def _dir():
    from hearthkin_paths import config_dir
    d = config_dir() / "kin_messages"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_path():
    return _dir() / "state.json"


def _load_state():
    p = _state_path()
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


def _save_state(state):
    from kin_persistence import atomic_write_json
    atomic_write_json(_state_path(), state)


def _pair_key(a, b):
    x, y = sorted([a, b], key=str.casefold)
    return f"{x}__{y}"


def pair_file(a, b):
    """The plain-text record of everything two kin have said to each other."""
    return _dir() / f"{_pair_key(a, b)}.md"


def resolve_kin(name, sender=""):
    """The real folder name of another kin in this house, or None."""
    # A model writes "to:Kimi." or "to: 'Kimi'" as often as "to:Kimi".
    want = str(name or "").strip().strip("\"'.,:;!?").strip().casefold()
    if not want:
        return None
    try:
        from kin_persistence import list_agents
        kin = list_agents()
    except Exception:
        return None
    for k in kin:
        if k.casefold() == want and k.casefold() != str(sender).casefold():
            return k
    return None


def other_kin(kin_name):
    try:
        from kin_persistence import list_agents
        return [k for k in list_agents()
                if k.casefold() != str(kin_name or "").casefold()]
    except Exception:
        return []


def _today(now=None):
    return (now or datetime.datetime.now()).date().isoformat()


def per_day(kin):
    """How many messages this kin may write to each other kin a day: its own
    setting ("Messages it may write to each other kin a day", in its
    heartbeat settings), 1 unless the person changed it. 0 means none."""
    try:
        from kin_persistence import load_agent_config
        hb = (load_agent_config(kin) or {}).get("heartbeat") or {}
        return max(0, int(hb.get("kin_messages_per_day", 1)))
    except (TypeError, ValueError):
        return 1
    except Exception:
        return 1


def can_send(sender, to, now=None):
    """(True, "") or (False, the reason in plain words)."""
    limit = per_day(sender)
    if limit <= 0:
        return False, ("your person hasn't set you up to write to other kin "
                       "(you may write 0 messages a day).")
    st = _load_state().get(_pair_key(sender, to)) or {}
    sent = 0
    if st.get("day") == _today(now):
        sent = sum(1 for s in st.get("sent_by") or []
                   if s.casefold() == sender.casefold())
    if sent >= limit:
        return False, (
            f"you've already written to {to} "
            + ("once" if limit == 1 else f"{limit} times")
            + " today, which is as many as your person has set, so this can "
              "wait until tomorrow.")
    return True, ""


def send(sender, to_name, text, now=None):
    """Send one message. Returns (ok, what to tell the sender)."""
    now = now or datetime.datetime.now()
    body = str(text or "").strip()
    to = resolve_kin(to_name, sender)
    if not to:
        names = other_kin(sender)
        if not names:
            return False, "there's no other kin in this house to write to."
        return False, (f"there's no kin here called {str(to_name).strip()!r}. "
                       "The others in this house are: " + ", ".join(names) + ".")
    if not body:
        return False, "the message was empty, so nothing was sent."
    ok, why = can_send(sender, to, now)
    if not ok:
        return False, why
    stamp = now.strftime("%Y-%m-%d %H:%M")
    try:
        f = pair_file(sender, to)
        head = "" if f.exists() else (
            f"# {sender} and {to}\n\nEverything these two kin have written to "
            "each other, oldest first.\n\n")
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(f"{head}## {stamp}, {sender} to {to}\n\n{body}\n\n")
        state = _load_state()
        key = _pair_key(sender, to)
        st = state.get(key) or {}
        if st.get("day") != _today(now):
            st = {"day": _today(now), "sent_by": []}
        st["sent_by"] = list(st.get("sent_by") or []) + [sender]
        state[key] = st
        waiting = state.setdefault("waiting", [])
        waiting.append({"from": sender, "to": to, "at": stamp, "text": body})
        _save_state(state)
    except Exception as e:
        _log(sender, "send", e)
        return False, f"it couldn't be saved ({e}), so nothing was sent."
    _record(sender, {"role": "assistant", "content": body,
                     "ts": now.isoformat(timespec="seconds"),
                     "source": f"kin_message:{to}"})
    return True, (f"your message is with {to}. They'll be given it at their "
                  f"next heartbeat or wake-up, and can answer.")


def take_waiting(kin_name):
    """Messages waiting for this kin, removed from the queue. Each is also
    recorded in the kin's own conversation, so it's remembered even where the
    turn that delivers it isn't kept (a heartbeat)."""
    try:
        state = _load_state()
    except Exception:
        return []
    waiting = state.get("waiting") or []
    mine = [w for w in waiting
            if str(w.get("to", "")).casefold() == str(kin_name).casefold()]
    if not mine:
        return []
    state["waiting"] = [w for w in waiting if w not in mine]
    try:
        _save_state(state)
    except Exception as e:
        _log(kin_name, "deliver", e)
        return []
    for w in mine:
        _record(kin_name, {"role": "user", "content": w["text"],
                           "ts": datetime.datetime.now().isoformat(
                               timespec="seconds"),
                           "sender_name": w["from"],
                           "source": f"kin_message:{w['from']}"})
    return mine


# Who handed this kin a message on the turn now running: {kin: {sender}}.
# Set when the message is given (waiting_block), used and cleared when the
# reply comes back (send_fences). Heartbeats and wake-ups frame and finish a
# turn in the same process, so the turn's own memory is enough.
_DELIVERED = {}


def waiting_block(kin_name):
    """The note put at the start of a heartbeat or wake-up when another kin
    has written. "" when nothing is waiting."""
    mine = take_waiting(kin_name)
    if not mine:
        return ""
    _DELIVERED[str(kin_name).casefold()] = {w["from"] for w in mine}
    parts = []
    for w in mine:
        frm = w["from"]
        can, _ = can_send(kin_name, frm)
        how = (f"You can answer {frm}: message_kin with "
               f"to=\"{frm}\", or a block that opens ```to:{frm}."
               if can else f"You've already written to {frm} today.")
        parts.append(f"[hearthkin: {frm}, another kin in this house, wrote to "
                     f"you ({w['at']}):]\n\n{w['text']}\n\n[hearthkin: {how}]")
    return "\n\n".join(parts)


def send_fences(sender, reply_text, now=None):
    """Send every ```to:Name block in a reply. Returns a plain note for the
    kin's history saying what went and what didn't, or "" when the reply had
    none. The same shape works for a kin with tools or without."""
    found = FENCE_RE.findall(reply_text or "")
    wrote_to_me = _DELIVERED.pop(str(sender).casefold(), set())
    if not found:
        # An answer in plain words. Tested on small models (22 Nov 2026):
        # handed a message from Kimi, most answered "Dear Kimi, ..." in prose
        # and never used the block. Asked in general "was that for Opal?",
        # they couldn't tell a note ABOUT Opal from one TO him -- but here
        # there is nothing to tell apart: this kin was just written to by
        # Kimi, and its reply opens by speaking to Kimi. So it goes, once,
        # and only to the kin who wrote this turn.
        to = addressed_kin(reply_text, sender)
        if to and to in wrote_to_me and can_send(sender, to, now)[0]:
            ok, said = send(sender, to, reply_text, now)
            if ok:
                return (f"[hearthkin: your reply spoke to {to}, who had just "
                        f"written to you, so it was sent to {to}. {said}]")
        return ""
    bits = []
    for name, body in found:
        ok, said = send(sender, name, body, now)
        who = resolve_kin(name, sender) or str(name).strip()
        bits.append(f"to {who}: " + ("sent; " if ok else "NOT sent; ") + said)
    return "[hearthkin: from your reply, " + " | ".join(bits) + "]"


def addressed_kin(text, sender):
    """The kin a message opens by speaking to ("Opal --", "Opal,", "Dear
    Opal"), or None. Used only to HOLD a reach_out that would otherwise go
    somewhere that kin isn't; never to send anything by guesswork."""
    first = (str(text or "").lstrip().split("\n", 1) or [""])[0].strip()
    first = re.sub(r"^(dear|hey|hi|hello)\s+", "", first, flags=re.I)
    for k in other_kin(sender):
        m = re.match(re.escape(k) + r"(\s*[—–,:!]|\s+-\s|\s*$)",
                     first, re.IGNORECASE)
        if m:
            return k
    return None


def _record(kin, row):
    try:
        from kin_persistence import append_agent_conversation_turn
        append_agent_conversation_turn(kin, row)
    except Exception as e:
        _log(kin, "record", e)


def _log(kin, action, e):
    try:
        from kin_persistence import append_failure_log
        append_failure_log("kin_messages.log", kin, action, e)
    except Exception:
        pass
