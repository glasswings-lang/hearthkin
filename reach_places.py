# SPDX-License-Identifier: CC0-1.0
"""The places outside the desktop a kin's words can be sent: listing them,
and sending to one.

Three things send a kin's words somewhere on its behalf: reach_out (the kin
starts a conversation), a wake-up's "Send this message to", and the
heartbeat's default place. Each used to list the places itself and send
itself, Telegram only, so a kin whose person was on Discord could reach
nobody there (surface check row `reach_out_delivery`). Since 12 Nov 2026 the
list and the sending live here, once, and Discord is in both.

Surfaces:
  telegram_dm     a Telegram person, by user ID
  telegram_group  a Telegram group, by chat ID
  discord_dm      a Discord person's direct messages, by user ID
  discord_channel a Discord server channel, by channel ID

The desktop isn't here: words for the desktop are recorded in the kin's own
conversation by whoever calls this, which is the desktop's record.
"""


def place_rows(cfg):
    """Every place this kin's settings name, in a fixed order: Telegram
    people, Telegram groups, Discord people, Discord channels. Each is a dict
    {"surface", "id", "label", "display"}: `label` is the operator's own name
    for it (what a kin is shown and must say back), `display` is the line a
    settings list shows. "*" is never a place: a message needs a real one."""
    rows = []
    cfg = cfg or {}
    tg = cfg.get("telegram") or {}
    if isinstance(tg, dict):
        labels = tg.get("user_labels") or {}
        for uid in (tg.get("allow_from") or []):
            s = str(uid).strip()
            if not s or s == "*" or not s.lstrip("-").isdigit():
                continue
            nm = str(labels.get(s) or "").strip()
            rows.append({"surface": "telegram_dm", "id": s, "label": nm or s,
                         "display": (f"Telegram DM: {nm} ({s})" if nm
                                     else f"Telegram DM: {s}")})
        for cid, g in (tg.get("groups") or {}).items():
            s = str(cid).strip()
            if not s:
                continue
            nm = (str(g.get("label") or "").strip()
                  if isinstance(g, dict) else "")
            rows.append({"surface": "telegram_group", "id": s,
                         "label": nm or s,
                         "display": (f"Telegram group: {nm} ({s})" if nm
                                     else f"Telegram group: {s}")})
    dc = cfg.get("discord") or {}
    if isinstance(dc, dict):
        labels = dc.get("user_labels") or {}
        for uid in (dc.get("allow_from") or []):
            s = str(uid).strip()
            if not s or not s.isdigit():
                continue
            nm = str(labels.get(s) or "").strip()
            rows.append({"surface": "discord_dm", "id": s, "label": nm or s,
                         "display": (f"Discord DM: {nm} ({s})" if nm
                                     else f"Discord DM: {s}")})
        for cid in (dc.get("channels") or []):
            s = str(cid).strip()
            if not s or not s.isdigit():
                continue
            rows.append({"surface": "discord_channel", "id": s, "label": s,
                         "display": f"Discord channel: {s}"})
    # A kin addresses a place by its label, so two places may never share
    # one: "Wren" on Telegram and "Wren" on Discord become "Wren on
    # Telegram" and "Wren on Discord". Without this the kin naming one
    # could only ever reach whichever came first.
    seen = {}
    for r in rows:
        seen[r["label"].casefold()] = seen.get(r["label"].casefold(), 0) + 1
    for r in rows:
        if seen[r["label"].casefold()] > 1:
            r["label"] = where_words(r["surface"], r["label"])
    # Two of the same name on the same app (two groups both called "Book
    # club"): the ID is the only thing left that tells them apart.
    seen = {}
    for r in rows:
        seen[r["label"].casefold()] = seen.get(r["label"].casefold(), 0) + 1
    for r in rows:
        if seen[r["label"].casefold()] > 1:
            r["label"] = f"{r['label']} ({r['id']})"
    return rows


def deliver(kin, cfg, surface, place_id, text, timeout=20):
    """Send `text` to one place. Returns (sent, why): `sent` is True when it
    went; `why` is a short plain reason when it didn't ("Telegram isn't fully
    set up", the error). Never raises. A surface it doesn't know is not sent,
    and says so."""
    cfg = cfg or {}
    pid = str(place_id or "").strip()
    text = (text or "").strip()
    if not text:
        return False, "there was nothing to send"
    if surface in ("telegram_dm", "telegram_group"):
        tg = cfg.get("telegram") or {}
        token = (tg.get("bot_token") or "").strip() if isinstance(tg, dict) else ""
        if not (isinstance(tg, dict) and tg.get("enabled") and token and pid):
            return False, "Telegram isn't fully set up"
        try:
            from telegram_bot import telegram_api_call
            # Telegram's cap is about 4096 characters; 4000 for headroom.
            for i in range(0, len(text), 4000):
                telegram_api_call(token, "sendMessage",
                                  {"chat_id": pid, "text": text[i:i + 4000]},
                                  timeout=timeout)
            return True, ""
        except Exception as e:
            _log("telegram_failures.log", kin, f"send {surface} {pid}", e)
            return False, f"Telegram send failed: {e}"
    if surface in ("discord_dm", "discord_channel"):
        dc = cfg.get("discord") or {}
        token = (dc.get("bot_token") or "").strip() if isinstance(dc, dict) else ""
        if not (isinstance(dc, dict) and dc.get("enabled") and token and pid):
            return False, "Discord isn't fully set up"
        import discord_mirror
        if surface == "discord_dm":
            ok = discord_mirror.send_dm(token, pid, text, kin, timeout)
        else:
            ok = discord_mirror.send_to_channel(token, pid, text, kin, timeout)
        return (True, "") if ok else (False, "Discord send failed (see "
                                             "discord_failures.log)")
    return False, f"nowhere called {surface!r} can be sent to"


def where_words(surface, label=""):
    """'Telegram' / 'Discord' plus the place, for telling the kin where its
    words went."""
    app = "Discord" if str(surface).startswith("discord") else "Telegram"
    return f"{label} on {app}" if label else app


def _log(name, kin, context, e):
    try:
        from kin_persistence import append_failure_log
        append_failure_log(name, kin, context, e)
    except Exception:
        pass
