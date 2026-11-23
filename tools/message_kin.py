# SPDX-License-Identifier: CC0-1.0

"""message_kin — write to another kin in this house (22 Nov 2026).

Offered on heartbeats and wake-ups, like reach_out. See kin_mail.py for how
messages are kept (one plain file per pair, readable by the person at any
time) and the one-exchange-a-day limit."""


def message_kin(to: str, message: str, agent_name: str = "",
                kin_mail_prompted: bool = False) -> str:
    """Write to another kin who lives in this house with you: a thought, a
    question, something you noticed that they'd want to know.

    `to` is the other kin's name, exactly as it's written in the list below.
    They're given your message at their own next heartbeat or wake-up, and can
    answer you. Your person sets how many messages you may write to each kin
    a day (usually one, so your message and their answer). Everything kin
    write to each other is kept where your person can read it.

    This is only for other kin. To reach your person or a place they've opened
    to you, use reach_out."""
    if not agent_name:
        return "message_kin: no kin context (framework bug)."
    import kin_mail
    # kin_mail_prompted is set by the framework, never the model: true in a
    # conversation with the person there (see tools.load_tools person_present).
    ok, said = kin_mail.send(agent_name, to, message,
                             prompted=bool(kin_mail_prompted))
    return ("message_kin: " if ok else "message_kin: nothing was sent: ") + said
