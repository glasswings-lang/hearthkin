"""Regression tests for the anti-impersonation cleanup chain.

The bug this suite exists to prevent, in full:

In July 2026, in a two-kin room, Vesper produced a turn that opened with a
leading timestamp followed by "[Opal]: ..." and continued in Opal's voice for
the entire message. It was saved as Vesper's reply with "[Opal]:" still
attached, then fed back into the room's history — teaching the next turn to do
it again. (The fixture below reproduces the SHAPE; the text is invented.)

Nothing was broken in isolation:
  - the "\\n[" stop sequence works, but can't fire at position 0 (no newline)
  - strip_leading_speaker_tag was written for exactly this shape (2026-05-19)
  - it was wired into the room path (2026-05-19, again 2026-06-01)

It leaked anyway, because the chain ran strip_self_timestamp LAST. Every other
stripper is anchored at ^, so the leading timestamp shielded the speaker tag
from the one guard built to catch it — and then the timestamp stripper removed
the shield, persisting a naked "[Opal]:" that nothing would ever re-check.

All six call sites (3 in hearthkin.pyw, 2 in telegram_bot.py, 1 in
discord_bot.py) had the same wrong order. Telegram never triggered it only
because it puts other speakers in the `user` slot, so no attractor exists there
— the gap was loaded everywhere, just never fired.

Fix: clean_kin_reply() owns the order, in one place, and reports whether a
FOREIGN tag was found so callers can re-roll instead of silently scrubbing.

Same convention as test_llm_normalization.py: NO pytest dependency, plain
asserts via check(), a summary line, exit 1 on any failure.

Run:  python tests/test_impersonation_cleanup.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chat_helpers as ch  # noqa: E402

# Every fixture below is a deliberate impersonation string. Without this, each
# run wrote a handful of fake alarms into the operator's real
# LOGS_DIR/impersonation.log — which destroys the only property that log has:
# that an entry in it means something is genuinely wrong. Found the hard way
# on 2026-07-16, when the first run of this suite made it look like a live
# room had leaked. Keep this line.
ch.IMPERSONATION_LOG_OFF = True

_failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _failures.append(name)


# ─────────────────────────────────────────────────────────────────────────
# The leak, reproduced. Structure is exactly the real one (timestamp, then a
# foreign speaker tag, then body); the wording is invented.
# ─────────────────────────────────────────────────────────────────────────

LEAK = "[2026-01-02 09:15] [Opal]: *I check the north gate.* The hinge is still loose."
BODY = "*I check the north gate.* The hinge is still loose."


def test_the_original_leak():
    out, imp = ch.clean_kin_reply(LEAK, "Vesper")
    check("leak: foreign tag is gone", not out.startswith("[Opal]"))
    check("leak: timestamp is gone", not out.startswith("["))
    check("leak: body survives intact", out == BODY)
    check("leak: reported as impersonation", imp is True)


def test_old_order_would_still_fail():
    """Pin the exact failure, so nobody 'tidies' the order back."""
    t = LEAK
    t = ch.strip_self_tag(t, "Vesper")
    t = ch.strip_leading_speaker_tag(t)
    t = ch.strip_trailing_other_speakers(t)
    t = ch.strip_self_timestamp(t)
    check("old order provably leaks (timestamp shields the tag)",
          t.startswith("[Opal]:"))


def test_timestamp_alone_is_not_impersonation():
    out, imp = ch.clean_kin_reply("[2026-01-02 09:15] just a normal reply", "Vesper")
    check("bare timestamp: stripped", out == "just a normal reply")
    check("bare timestamp: NOT flagged as impersonation", imp is False)


def test_own_tag_is_benign_echo():
    out, imp = ch.clean_kin_reply("[Vesper]: hello there", "Vesper")
    check("own tag: stripped", out == "hello there")
    check("own tag: NOT flagged (it's just echo, not impersonation)",
          imp is False)


def test_own_tag_behind_timestamp():
    out, imp = ch.clean_kin_reply("[2026-01-02 09:15] [Vesper]: hello there", "Vesper")
    check("own tag behind timestamp: stripped", out == "hello there")
    check("own tag behind timestamp: NOT flagged", imp is False)


def test_foreign_tag_bare():
    out, imp = ch.clean_kin_reply("[Opal]: the mountain does not move", "Vesper")
    check("foreign tag bare: stripped", out == "the mountain does not move")
    check("foreign tag bare: flagged", imp is True)


def test_stacked_tags():
    out, imp = ch.clean_kin_reply("[Vesper]: [Opal]: stacked", "Vesper")
    check("stacked own+foreign: both stripped", out == "stacked")
    check("stacked own+foreign: flagged", imp is True)


def test_mid_reply_transcript_still_cut():
    txt = "my own words here\n[Opal]: and then I invented his turn"
    out, imp = ch.clean_kin_reply(txt, "Vesper")
    check("mid-reply fake transcript: cut at the newline tag",
          out == "my own words here")
    check("mid-reply fake transcript: not flagged as LEADING impersonation",
          imp is False)


def test_clean_reply_untouched():
    txt = "*settles in* just talking normally, no tags at all."
    out, imp = ch.clean_kin_reply(txt, "Vesper")
    check("clean reply: passes through unchanged", out == txt)
    check("clean reply: not flagged", imp is False)


def test_markdown_link_defs_survive():
    """The two narrow guards in _OTHER_SPEAKER_RE must still hold."""
    txt = "see the docs\n[1]: https://example.com"
    out, _ = ch.clean_kin_reply(txt, "Vesper")
    check("markdown link def not eaten as a speaker tag",
          "https://example.com" in out)


def test_empty_and_none():
    out, imp = ch.clean_kin_reply("", "Vesper")
    check("empty string: safe", out == "" and imp is False)
    out, imp = ch.clean_kin_reply(None, "Vesper")
    check("None: safe", out is None and imp is False)


def test_missing_kin_name():
    out, imp = ch.clean_kin_reply("[Opal]: hi", "")
    check("no kin_name: foreign tag still caught", out == "hi" and imp is True)


# --- the colon-less tag, which every stripper above is blind to ---------
#
# Attributed surfaces show the model "[SpeakerOne] text" — deliberately without
# the colon, because "[Name]:" is the speaker-turn token models complete.
# But that means a model imitating what it was actually shown produces a
# tag none of the colon-anchored strippers catch. These pin the pass that
# does, and — just as important — pin that it stays hands-off without a
# name list, so a kin's bracketed emote is never eaten.

def test_bare_tag_ignored_without_names():
    txt = "[SpeakerOne] I left it on the hook"
    out, imp = ch.clean_kin_reply(txt, "Vesper")
    check("bare tag, no name list: left completely alone", out == txt)
    check("bare tag, no name list: not flagged", imp is False)


def test_bare_tag_caught_with_names():
    out, imp = ch.clean_kin_reply(
        "[SpeakerOne] I left it on the hook", "Vesper", known_speakers={"SpeakerOne"})
    check("bare foreign tag: stripped", out == "I left it on the hook")
    check("bare foreign tag: flagged as impersonation", imp is True)


def test_bare_tag_behind_timestamp():
    out, imp = ch.clean_kin_reply(
        "[2026-01-02 09:15] [SpeakerOne] on the hook", "Vesper",
        known_speakers={"SpeakerOne"})
    check("bare tag behind a timestamp: still caught", out == "on the hook")
    check("bare tag behind a timestamp: flagged", imp is True)


def test_attributed_form_with_handle():
    out, imp = ch.clean_kin_reply(
        "[SpeakerOne (@speakerone)] on the hook", "Vesper",
        known_speakers={"SpeakerOne (@speakerone)"})
    check("the live Telegram attribution form is caught too",
          out == "on the hook" and imp is True)


def test_emote_is_not_a_speaker_tag():
    """The false positive that would matter most. A kin opening with a
    bracketed emote must survive even while a name list is in play —
    this is why the pass matches names instead of matching brackets."""
    txt = "[laughs] yeah, that one"
    out, imp = ch.clean_kin_reply(txt, "Vesper", known_speakers={"SpeakerOne", "Alex"})
    check("bracketed emote: untouched", out == txt)
    check("bracketed emote: not flagged", imp is False)


def test_unknown_name_is_not_stripped():
    txt = "[Somebody] said a thing"
    out, imp = ch.clean_kin_reply(txt, "Vesper", known_speakers={"SpeakerOne"})
    check("a name never shown to the model: left alone", out == txt)
    check("a name never shown to the model: not flagged", imp is False)


def test_bare_tag_matching_is_case_insensitive():
    out, imp = ch.clean_kin_reply(
        "[speakerone] on the hook", "Vesper", known_speakers={"SpeakerOne"})
    check("case difference doesn't let a tag through",
          out == "on the hook" and imp is True)


if __name__ == "__main__":
    test_the_original_leak()
    test_old_order_would_still_fail()
    test_timestamp_alone_is_not_impersonation()
    test_own_tag_is_benign_echo()
    test_own_tag_behind_timestamp()
    test_foreign_tag_bare()
    test_stacked_tags()
    test_mid_reply_transcript_still_cut()
    test_clean_reply_untouched()
    test_markdown_link_defs_survive()
    test_empty_and_none()
    test_missing_kin_name()
    test_bare_tag_ignored_without_names()
    test_bare_tag_caught_with_names()
    test_bare_tag_behind_timestamp()
    test_attributed_form_with_handle()
    test_emote_is_not_a_speaker_tag()
    test_unknown_name_is_not_stripped()
    test_bare_tag_matching_is_case_insensitive()

    print()
    if _failures:
        print(f"{len(_failures)} FAILURE(S): " + ", ".join(_failures))
        sys.exit(1)
    print("all impersonation-cleanup tests passed")
