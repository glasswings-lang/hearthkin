# SPDX-License-Identifier: CC0-1.0
"""Send a kin's desktop conversation to people's Discord direct messages.

Telegram has long had "Mirror desktop messages to my Telegram chat": every
desktop message and reply for a kin is also sent to the people who have it
on, and so are scheduled wake-up replies and wake-up failure notices. Discord
had no such thing. Since 12 Nov 2026 the shared person window offers the same
box on both surfaces, and this module is Discord's half of it.

It talks to Discord's web API directly with the kin's bot token, the way the
Telegram mirror uses Telegram's, so it works from the desktop app AND from
the separate process a scheduled wake-up runs in, whether or not the Discord
bot is connected at that moment.

Failures go to discord_failures.log and never raise: a mirror is a copy, and
nothing that matters may depend on it arriving.
"""

import json
import urllib.request

_API = "https://discord.com/api/v10"
# Discord's per-message limit is 2000 characters; leave headroom.
_CHUNK = 1900


def mirror_targets(cfg):
    """(bot_token, [user ids]) for a kin's Discord mirror, or ("", []) when
    Discord isn't set up or nobody has mirroring on. "*" is never a target:
    a DM needs one real person."""
    dc = (cfg or {}).get("discord") or {}
    if not isinstance(dc, dict) or not dc.get("enabled"):
        return "", []
    token = (dc.get("bot_token") or "").strip()
    m = dc.get("user_mirror_desktop") or {}
    if not token or not isinstance(m, dict):
        return "", []
    allow = {str(x) for x in (dc.get("allow_from") or [])}
    ids = [str(u) for u, on in m.items()
           if on and str(u) != "*" and str(u).isdigit() and str(u) in allow]
    return token, ids


def _call(token, method, path, body, timeout):
    req = urllib.request.Request(
        _API + path, method=method,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bot {token}",
                 "Content-Type": "application/json",
                 "User-Agent": "DiscordBot (hearthkin, 1)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def send_dm(token, user_id, text, kin_name="", timeout=15, _call=_call):
    """Send `text` to one person's Discord DMs, split to fit. Returns True
    when every piece was sent. Never raises."""
    if not (token and user_id and text):
        return False
    try:
        ch = _call(token, "POST", "/users/@me/channels",
                   {"recipient_id": str(user_id)}, timeout)
        channel_id = ch.get("id")
        if not channel_id:
            raise RuntimeError("Discord didn't open a DM channel")
        _post_pieces(token, channel_id, text, timeout, _call)
        return True
    except Exception as e:
        _log(kin_name, f"DM user_id={user_id}", e)
        return False


def send_to_channel(token, channel_id, text, kin_name="", timeout=15,
                    _call=_call):
    """Send `text` to one Discord server channel, split to fit. Returns True
    when every piece was sent. Never raises. Used by reach_places for a kin
    reaching out, or a wake-up sent, to a channel the operator opened."""
    if not (token and channel_id and text):
        return False
    try:
        _post_pieces(token, channel_id, text, timeout, _call)
        return True
    except Exception as e:
        _log(kin_name, f"channel_id={channel_id}", e)
        return False


def _post_pieces(token, channel_id, text, timeout, _call):
    for i in range(0, len(text), _CHUNK):
        _call(token, "POST", f"/channels/{channel_id}/messages",
              {"content": text[i:i + _CHUNK]}, timeout)


def _log(kin_name, context, e):
    try:
        from kin_persistence import append_failure_log
        append_failure_log("discord_failures.log", kin_name, context, e)
    except Exception:
        pass


def mirror(cfg, kin_name, texts, timeout=15):
    """Send each of `texts` to everyone with the Discord mirror on. Blocking;
    callers on the UI thread run it on a worker. Returns how many people got
    everything."""
    token, ids = mirror_targets(cfg)
    ok = 0
    for uid in ids:
        if all(send_dm(token, uid, t, kin_name, timeout) for t in texts if t):
            ok += 1
    return ok
