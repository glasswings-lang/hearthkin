"""Who's fronting, per conversation (21 Nov 2026).

A plural person can tell Hearthkin who of them is at the front, with
`/front <anything>` on Telegram or Discord, or Chat, Who's fronting on the
desktop. Whatever they type is kept exactly ("Juniper", "Fern and Sam", "a
little"). It's set PER CONVERSATION, because different ones of them can be
talking in different chats at once (each desktop kin, each Telegram DM or
group, each Discord DM or channel, each room); per kin falls out of that.
One switch, "same everywhere", makes a single setting apply to every
conversation instead.

It's stamped on each of that person's messages when they're saved
(row["fronting"]) and shown beside their name, so history stays true after
a switch. Only an account marked "This is me", and the desktop, can set it
or have it stamped: nobody else in a group can say who someone is.

Kept in its own file, written whole and atomically, because the bots and
the app both write it.
"""

import json
import threading

_LOCK = threading.Lock()
OFF_WORDS = ("off", "clear", "none", "nobody")


def _path():
    from hearthkin_paths import config_dir
    return config_dir() / "fronting.json"


def _load():
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    from kin_persistence import atomic_write_text
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, json.dumps(data, ensure_ascii=False, indent=1))


def _key(kin, place):
    return f"{kin}|{place}"


def everywhere():
    """True when one setting applies to every conversation."""
    return bool(_load().get("everywhere", False))


def set_everywhere(on):
    with _LOCK:
        data = _load()
        data["everywhere"] = bool(on)
        _save(data)


def get(kin, place):
    """Who's fronting in this conversation ("" for nobody set)."""
    data = _load()
    if data.get("everywhere"):
        return str(data.get("all") or "")
    places = data.get("places") or {}
    return str(places.get(_key(kin, place)) or "")


def set(kin, place, text):
    """Set (or, with "" or an off word, clear) who's fronting here; with
    "same everywhere" on, everywhere. Returns what's now set."""
    text = " ".join(str(text or "").split())
    if text.casefold() in OFF_WORDS:
        text = ""
    text = text[:80]
    with _LOCK:
        data = _load()
        if data.get("everywhere"):
            data["all"] = text
        else:
            places = dict(data.get("places") or {})
            if text:
                places[_key(kin, place)] = text
            else:
                places.pop(_key(kin, place), None)
            data["places"] = places
        _save(data)
    return text


def reply_line(arg, kin, place):
    """The /front command: with words, set it; alone, say who's set. The
    answer is a plain line from Hearthkin, not the kin's voice."""
    if str(arg or "").strip():
        now = set(kin, place, arg)
    else:
        now = get(kin, place)
    return f"Fronting: {now}." if now else "Fronting: nobody set."


def stamp(row, kin, place):
    """Put who's fronting on a person's saved message. Call only for the
    person's own account ("This is me") or the desktop."""
    try:
        who = get(kin, place)
        if who and isinstance(row, dict):
            row["fronting"] = who
    except Exception:
        pass
    return row


def proactive_line(kin):
    """For a heartbeat or a wake-up: who was last at the front in the chat a
    reach_out from this kin would land in, as the editable fronting_line says it.
    "" when nobody is set there (23 Nov 2026). Only the fact; what the kin does
    with it is its own."""
    try:
        from kin_persistence import (load_agent_config, load_app_prompt,
                                     DEFAULT_FRONTING_LINE)
        cfg = load_agent_config(kin) or {}
        dest = (cfg.get("heartbeat") or {}).get("destination") or {"surface": "desktop"}
        surface = str(dest.get("surface") or "desktop")
        # A reach_out place is "telegram_dm" / "telegram_group" / "discord_dm" /
        # "discord_channel" with the chat's id; fronting keys a chat as
        # "telegram:<chat id>" or "discord:<channel id>". A Telegram DM's chat id IS
        # the person's id, so those meet. A Discord DM's reach_out id is the person
        # and not the DM channel, so there it stays unsaid rather than guessed.
        app = surface.split("_")[0]
        place = "desktop" if app == "desktop" else f"{app}:{dest.get('id', '')}"
        who = get(kin, place)
        if not who:
            return ""
        where = {"telegram": "on Telegram", "discord": "on Discord"}.get(
            app, "in the desktop chat")
        line = load_app_prompt("fronting_line", kin) or DEFAULT_FRONTING_LINE
        return line.replace("{where}", where).replace("{who}", who)
    except Exception:
        return ""
