# SPDX-License-Identifier: CC0-1.0

"""reach_out — a kin messages its operator on its own initiative.

This is the gate that keeps proactive contact from being spammy: a kin only
reaches out by *calling* this tool. Not calling it is the default and sends
nothing. Paired with the quiet heartbeat wake (see
hearthkin_cron.run_heartbeat), which gives a kin the *chance* to reach out
without ever requiring it to say anything. Not calling it is not always
traceless, though: a substantial reply written without calling it is asked
about once, and words never asked about are kept in heartbeat_unsent.log (see
turn_steering.log_unsent_reach for which case keeps the text)."""

import datetime

# The last message held per kin (see the kin check in reach_out): sending the
# same one again means the kin meant it.
_HELD = {}


def _where_it_goes(cfg, to=""):
    """Where a reach_out would land, in the words the kin should hear: the
    person's own name for the place, and which app."""
    hb = (cfg or {}).get("heartbeat") or {}
    want = str(to or "").strip()
    if want:
        for d in hb.get("allowed_destinations") or []:
            if isinstance(d, dict) and str(d.get("label", "")).strip(
                    ).casefold() == want.casefold():
                import reach_places
                return reach_places.where_words(d.get("surface", ""), want)
        return repr(want)
    dest = hb.get("destination") or {"surface": "desktop"}
    surface = dest.get("surface", "desktop")
    if surface == "desktop":
        return "your person's desktop chat"
    try:
        import reach_places
        for r in reach_places.place_rows(cfg):
            if r["surface"] == surface and str(r["id"]) == str(dest.get("id", "")):
                return reach_places.where_words(surface, r["label"])
        return f"your person on {reach_places.where_words(surface)}"
    except Exception:
        return "your person"


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

    # A message that opens by speaking to another kin ("Opal --", "Dear
    # Opal,") is held once, wherever it was going (22 Nov 2026). Kimi wrote
    # to Opal on a heartbeat and sent it to a Telegram group, the one place
    # it had been given, with no way to know Opal wasn't there. Kin reach
    # each other with message_kin. Sending the same message again goes
    # through, so a kin that really does mean that place isn't stuck.
    try:
        import kin_mail
        addressed = kin_mail.addressed_kin(text, agent_name)
    except Exception:
        addressed = None
    if addressed and _HELD.get(agent_name) != (text, (to or "").strip()):
        _HELD[agent_name] = (text, (to or "").strip())
        from kin_persistence import load_app_prompt
        return (load_app_prompt("kin_message_held", agent_name)
                .replace("{kin}", addressed)
                .replace("{where}", _where_it_goes(cfg, to)))
    _HELD.pop(agent_name, None)

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

    if surface != "desktop":
        # Telegram and Discord both, through the one sender (reach_places).
        import reach_places
        place_id = str(dest.get("id", "")).strip()
        sent, why = reach_places.deliver(agent_name, cfg, surface, place_id,
                                         text)
        app = reach_places.where_words(surface)
        if sent:
            delivered_where = (
                reach_places.where_words(surface, want) if want
                else f"the operator's {app} ({place_id})")
        else:
            # Don't lose the message — it's still recorded below so the
            # operator sees it on the desktop even if the send failed.
            delivered_where = f"your desktop chat ({why})"

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
