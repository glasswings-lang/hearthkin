# SPDX-License-Identifier: CC0-1.0

"""reach_out — a kin messages its operator on its own initiative.

This is the gate that keeps proactive contact from being spammy: a kin only
reaches out by *calling* this tool. Not calling it is the default and costs
nothing — silence leaves no trace. Paired with the quiet heartbeat wake (see
hearthkin_cron.run_heartbeat), which gives a kin the *chance* to reach out
without ever requiring it to say anything."""

import datetime


def reach_out(message: str, to: str = "", agent_name: str = "") -> str:
    """Send a short message on your own initiative, to reach out when you
    genuinely have something you want to bring someone: a thought, a question,
    something you noticed or want to share.

    `to` is WHERE it goes. Leave it out and it goes to your operator — the
    person who runs you. If your operator has opened other places to you, they
    are listed below and you may name one instead; naming anything else does
    nothing but tell you the list again. You cannot reach anywhere your
    operator has not opened.

    Use this ONLY when there is really something. Having nothing to say is
    normal and completely fine — silence is the default and you never need to
    use this at all. Do NOT send status updates, "still here", "still
    nothing", or repeat something you've already said. One message per genuine
    impulse. A place your operator opened is somewhere real people are — say
    the thing you actually mean to say to THEM, not a report about your day.

    The message is recorded in your own history either way, so you remember
    having reached out."""
    text = (message or "").strip()
    if not text:
        return "reach_out: nothing to send (the message was empty)."
    if not agent_name:
        return "reach_out: no kin context (framework bug)."
    try:
        from kin_persistence import (
            load_agent_config, append_agent_conversation_turn,
        )
    except Exception as e:
        return f"reach_out: framework import failed ({e})."
    try:
        cfg = load_agent_config(agent_name) or {}
    except Exception as e:
        return f"reach_out: could not load your config ({e})."

    hb = cfg.get("heartbeat") or {}
    dest = hb.get("destination") or {"surface": "desktop"}
    # Places the operator has opened to this kin BEYOND its own default. Each
    # is {"label", "surface", "id"}. A kin can only ever reach one of these —
    # the allowlist is the whole security model, and it is the operator's, not
    # the kin's. Empty (the default) keeps the old operator-only behavior
    # exactly.
    allowed = hb.get("allowed_destinations") or []
    want = (to or "").strip()
    if want:
        match = None
        for d in allowed:
            if not isinstance(d, dict):
                continue
            if str(d.get("label", "")).strip().casefold() == want.casefold():
                match = d
                break
        if match is None:
            # Steering error, not a silent fallback: sending somewhere the kin
            # didn't ask for would be worse than not sending. Name the real
            # options so the next call can be right (the forgiving-contract
            # convention — a clear error beats a wrong action).
            names = [str(d.get("label", "")).strip()
                     for d in allowed if isinstance(d, dict) and d.get("label")]
            if not names:
                return ("reach_out: nothing was sent — there is nowhere named "
                        f"{want!r} open to you. Your operator hasn't opened any "
                        "place beyond themselves, so leave `to` out to reach "
                        "them.")
            return ("reach_out: nothing was sent — there is nowhere named "
                    f"{want!r} open to you. You can write to: "
                    + ", ".join(repr(n) for n in names)
                    + ". Leave `to` out to reach your operator instead.")
        dest = {"surface": match.get("surface", "desktop"),
                "id": match.get("id", "")}
    surface = dest.get("surface", "desktop")
    delivered_where = "your desktop chat"

    if surface in ("telegram_dm", "telegram_group"):
        tg = cfg.get("telegram") or {}
        token = (tg.get("bot_token") or "").strip()
        chat_id = str(dest.get("id", "")).strip()
        if tg.get("enabled") and token and chat_id:
            try:
                from telegram_bot import telegram_api_call
                # Chunk to Telegram's ~4096-char cap (4000 for headroom).
                for i in range(0, len(text), 4000):
                    telegram_api_call(
                        token, "sendMessage",
                        {"chat_id": chat_id, "text": text[i:i + 4000]},
                        timeout=20,
                    )
                delivered_where = (
                    f"{want} on Telegram" if want
                    else f"the operator's Telegram ({chat_id})")
            except Exception as e:
                # Don't lose the message — it's still recorded below so the
                # operator sees it on the desktop even if Telegram failed.
                delivered_where = f"your desktop chat (Telegram send failed: {e})"
        else:
            delivered_where = "your desktop chat (Telegram isn't fully set up)"

    # Record the reach-out in the kin's own history: the kin remembers it
    # spoke, and the operator sees it on next open. source="reach_out" is a
    # storage tag (stripped before any provider send by _strip_extra_message_
    # fields), so it can be told apart from ordinary replies later.
    try:
        append_agent_conversation_turn(agent_name, {
            "role": "assistant", "content": text,
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "source": "reach_out",
        })
    except Exception:
        pass

    return f"reach_out: your message was delivered to {delivered_where}."
