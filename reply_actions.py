# SPDX-License-Identifier: CC0-1.0
"""The two ends of a turn every surface shares for the blocks kin write to
each other and ask the manual with (22 Nov 2026).

before_message: what's put in front of a kin before the message it's about
to answer -- parts of the kin manual it asked for (manual_request), and, in
the person's own chat, messages other kin wrote to it (kin_mail).

after_reply: what's done with the blocks in its reply -- ```to:Name sends
to another kin (where that's allowed), ```manual asks for a part of the
manual -- and the notes that tell the kin what happened.

One place, so a surface can't act on a block without the others doing the
same; block_hints.py is the matching one place for teaching them.
"""


def before_message(kin, *, mail_in_chat=False):
    """Text to put just before the message the kin is answering ("" if
    nothing). `mail_in_chat`: also hand over messages from other kin (the
    person's own Telegram or Discord DM; the desktop adds them as rows of its
    conversation instead, and wake-ups and heartbeats at their start)."""
    parts = []
    if mail_in_chat:
        try:
            import kin_mail
            m = kin_mail.waiting_block(kin, in_chat=True)
            if m:
                parts.append(m)
        except Exception as e:
            _log(kin, "deliver", e)
    try:
        import manual_request
        g = manual_request.given_block(kin)
        if g:
            parts.append(g)
    except Exception as e:
        _log(kin, "manual", e)
    return "\n\n".join(parts)


def after_reply(kin, reply_text, *, kin_mail_ok=False, prompted=False):
    """Act on the blocks in a reply. Returns (notes, messages_sent): notes
    for the kin's history, and how many messages went to other kin.
    `kin_mail_ok`: ```to: blocks are acted on here (wake-ups, heartbeats,
    and the person's own chat); `prompted`: the person is there."""
    notes, sent = [], 0
    if kin_mail_ok:
        try:
            import kin_mail
            note, sent = kin_mail.handle_reply(kin, reply_text,
                                               prompted=prompted)
            if note:
                notes.append(note)
        except Exception as e:
            _log(kin, "to: block", e)
    try:
        import manual_request
        note = manual_request.note_requests(kin, reply_text)
        if note:
            notes.append(note)
    except Exception as e:
        _log(kin, "manual block", e)
    return notes, sent


def _log(kin, action, e):
    try:
        from kin_persistence import append_failure_log
        append_failure_log("kin_messages.log", kin, action, e)
    except Exception:
        pass
