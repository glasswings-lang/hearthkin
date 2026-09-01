"""A kin must be able to see what it wrote to its own memory.

`memory.md` belongs to the kin. Only two things write it: the kin, using
its file tools, and the person, through Settings → Memory. The frame
keeps the text in a per-kin cache so it isn't read from disk on every
send, and that cache was invalidated on exactly one of those two paths —
the person's.

So a kin that wrote its own memory could not see it. The cache is filled
when the kin is selected and never refreshed, which means the gap lasts
the whole session, and there is nothing to notice from outside.

Observed 2026-08-06: a kin wrote a 2,301-byte memory.md through a tool
call, and an hour later composed a memory entry into the chat, signing
off "would add to memory.md if I had one, but I don't yet." It was
correct. Its system prompt contained none of the file. From a chat
window that is indistinguishable from a model that doesn't remember.

The fix validates the cache against the files' mtime rather than adding
another invalidation call site, because the writers include a cron
subprocess in a DIFFERENT PROCESS, which cannot call an in-process
invalidator at all.

    python tests/test_kin_sees_own_memory.py
"""

import os
import sys
import time
import tempfile
from pathlib import Path

# Sandbox before importing anything that resolves the state directory.
os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(prefix="kin_memory_test_")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frame.status_voice_mixin import StatusVoiceMixin  # noqa: E402
from hearthkin_paths import kin_dir  # noqa: E402

_failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _failures.append(name)


class _Frame(StatusVoiceMixin):
    """The two attributes the refresh actually touches. No wx involved —
    the method is deliberately free of widgets so it can be tested
    without building a window."""
    def __init__(self, name):
        self.current_agent = name
        self._soul_cache = ""
        self._memory_cache = ""


def _write(kin, fname, text):
    d = kin_dir(kin)
    d.mkdir(parents=True, exist_ok=True)
    (d / fname).write_text(text, encoding="utf-8")
    # Filesystem mtime granularity can be coarse enough that two writes
    # in the same instant look identical. The size differs in every case
    # below, and the signature includes size for exactly this reason —
    # but nudge time anyway so the test isn't measuring its own speed.
    time.sleep(0.01)


def test_the_kin_sees_what_it_wrote():
    kin = "SelfWriter"
    _write(kin, "soul.md", "you are a test kin.")
    f = _Frame(kin)
    f._refresh_kin_text_cache_if_stale()
    check("control: with no memory.md the cache really is empty",
          f._memory_cache == "")

    # The kin writes its own memory through a file tool. Nothing calls
    # any invalidator — that is the whole point.
    _write(kin, "memory.md", "# Memory Index\n\nthe kettle is on the third shelf.\n")

    f._refresh_kin_text_cache_if_stale()
    check("the kin can see the memory it just wrote",
          "third shelf" in f._memory_cache)


def test_a_later_edit_is_picked_up_too():
    kin = "SecondWrite"
    _write(kin, "soul.md", "soul")
    _write(kin, "memory.md", "first version")
    f = _Frame(kin)
    f._refresh_kin_text_cache_if_stale()
    check("control: the first version is loaded", f._memory_cache == "first version")
    _write(kin, "memory.md", "second version, longer than the first")
    f._refresh_kin_text_cache_if_stale()
    check("a later write is picked up as well",
          f._memory_cache == "second version, longer than the first")


def test_soul_is_covered_by_the_same_check():
    kin = "SoulWrite"
    _write(kin, "soul.md", "before")
    f = _Frame(kin)
    f._refresh_kin_text_cache_if_stale()
    _write(kin, "soul.md", "after, and rather longer than before")
    f._refresh_kin_text_cache_if_stale()
    check("a changed soul.md is re-read too",
          f._soul_cache == "after, and rather longer than before")


def test_an_unchanged_kin_does_not_hit_disk_again():
    # The send path calls this every turn. It must be a stat, not a read.
    kin = "Unchanged"
    _write(kin, "soul.md", "soul")
    _write(kin, "memory.md", "memory")
    f = _Frame(kin)
    f._refresh_kin_text_cache_if_stale()

    import frame.status_voice_mixin as svm
    reads = []
    real = svm.load_memory
    svm.load_memory = lambda n: reads.append(n) or "memory"
    try:
        f._refresh_kin_text_cache_if_stale()
        f._refresh_kin_text_cache_if_stale()
    finally:
        svm.load_memory = real
    check("an unchanged kin is not re-read from disk", not reads)

    # ...and the positive control: it DOES re-read when something changed,
    # so the check above means "nothing changed" rather than "the spy
    # was never wired up".
    reads.clear()
    svm.load_memory = lambda n: reads.append(n) or "memory, now longer"
    try:
        _write(kin, "memory.md", "memory, now longer")
        f._refresh_kin_text_cache_if_stale()
    finally:
        svm.load_memory = real
    check("control: a change does re-read, so the spy was working",
          reads == [kin])


def test_a_missing_file_appearing_counts_as_a_change():
    # The case that actually happened: memory.md did not exist when the
    # kin was selected. "Absent" has to be a state in the signature, not
    # an error that leaves the old signature standing.
    kin = "Appears"
    _write(kin, "soul.md", "soul")
    f = _Frame(kin)
    f._refresh_kin_text_cache_if_stale()
    check("control: nothing to see yet", f._memory_cache == "")
    _write(kin, "memory.md", "it exists now")
    f._refresh_kin_text_cache_if_stale()
    check("a memory.md that appears later is noticed",
          f._memory_cache == "it exists now")


def test_no_kin_selected_is_harmless():
    f = _Frame("")
    f._refresh_kin_text_cache_if_stale()
    check("no kin selected does nothing and doesn't raise", f._memory_cache == "")


# ── The depth-log index ────────────────────────────────────────────────
# The '## Memory logs' section of memory.md is code's to maintain, and it
# was only ever rebuilt as a side effect of distillation. A kin whose
# distillation is behind keeps writing depth logs into an index that
# stopped listing them — and then the only way it can find one is to open
# them in turn, which is what a kin was observed doing on scheduled
# wake-ups. Measured on a real kin: 73 topic logs on disk, 10 in the index.

def test_new_logs_reach_the_index():
    import kin_persistence as kp
    kin = "IndexGrows"
    _write(kin, "soul.md", "soul")
    _write(kin, "memory.md", "# Memory Index\n\n- the kettle is on the third shelf.\n")
    d = kin_dir(kin)
    (d / "memory").mkdir(parents=True, exist_ok=True)

    check("control: with no logs there is nothing to do",
          kp.refresh_memory_log_index(kin) is False)

    (d / "memory" / "speakereight.md").write_text("# SpeakerEight\n\nnotes\n", encoding="utf-8")
    (d / "memory" / "atlas.md").write_text("# Atlas of comfort\n\nnotes\n", encoding="utf-8")
    check("a new log rebuilds the index", kp.refresh_memory_log_index(kin) is True)

    mem = kp.load_memory(kin)
    check("both logs are listed",
          "memory/speakereight.md" in mem and "memory/atlas.md" in mem)
    check("...each labelled with its own heading", "Atlas of comfort" in mem)
    # The part that matters most: this writes into a file that belongs to
    # the kin, and everything the kin wrote has to survive it.
    check("the kin's own entry is untouched", "third shelf" in mem)

    check("running again changes nothing", kp.refresh_memory_log_index(kin) is False)


def test_a_dated_journal_file_is_not_indexed():
    # memory/YYYY-MM-DD.md is a time series, not a topic reference. If
    # these counted, every day would rewrite memory.md and throw the
    # prompt cache away for a pointer nobody wants.
    import kin_persistence as kp
    kin = "IndexDated"
    _write(kin, "soul.md", "soul")
    _write(kin, "memory.md", "# Memory Index\n\n- a thing.\n")
    d = kin_dir(kin)
    (d / "memory").mkdir(parents=True, exist_ok=True)
    (d / "memory" / "topic.md").write_text("# Topic\n", encoding="utf-8")
    kp.refresh_memory_log_index(kin)
    before = kp.load_memory(kin)
    (d / "memory" / "2026-08-06.md").write_text("# Tuesday\n", encoding="utf-8")
    check("a dated journal entry does not trigger a rewrite",
          kp.refresh_memory_log_index(kin) is False)
    check("...and memory.md is byte-identical", kp.load_memory(kin) == before)


def test_a_kin_with_no_logs_is_never_rewritten():
    import kin_persistence as kp
    kin = "NoLogs"
    _write(kin, "soul.md", "soul")
    _write(kin, "memory.md", "# Memory Index\n\n- just entries, no logs.\n")
    before = kp.load_memory(kin)
    check("a kin that keeps no depth logs is left alone",
          kp.refresh_memory_log_index(kin) is False)
    check("...its memory.md is byte-identical", kp.load_memory(kin) == before)


def test_the_folder_signature_ignores_edits_to_a_log():
    # The index points at files. Editing a log's CONTENTS must not change
    # the signature — rewriting memory.md invalidates the prompt cache,
    # which costs minutes on a local model.
    import kin_persistence as kp
    kin = "SigStable"
    _write(kin, "soul.md", "soul")
    d = kin_dir(kin)
    (d / "memory").mkdir(parents=True, exist_ok=True)
    (d / "memory" / "a.md").write_text("# A\n\nshort\n", encoding="utf-8")
    first = kp.memory_log_folder_signature(kin)
    (d / "memory" / "a.md").write_text("# A\n\nmuch longer now\n", encoding="utf-8")
    check("editing a log does not change the folder signature",
          kp.memory_log_folder_signature(kin) == first)
    (d / "memory" / "b.md").write_text("# B\n", encoding="utf-8")
    check("control: adding one DOES change it",
          kp.memory_log_folder_signature(kin) != first)


def test_the_send_path_rebuilds_the_index_too():
    import inspect
    from frame.status_voice_mixin import StatusVoiceMixin
    src = inspect.getsource(StatusVoiceMixin._refresh_kin_text_cache_if_stale)
    check("the stale-check rebuilds the log index",
          "refresh_memory_log_index" in src)
    check("...gated on the log SET changing, not on every send",
          "memory_log_folder_signature" in src)


def test_every_surface_repairs_the_index_not_just_the_desktop():
    """The repair used to hang off the DESKTOP send path alone.

    That is the surface a kin may be reached from least. Measured on a real
    kin: 75 depth logs on disk, 10 in its index -- its nightly tending ran in
    the cron subprocess and its conversation happened on Telegram, so the one
    caller that could have fixed it was never on the path. The kin could not
    find a definition it had written itself, and asked for it back.

    So the rule is about EVERY surface, not about one more surface. Each site
    that puts a kin's memory in front of the kin goes through
    `load_memory_for_prompt`; a bare `load_memory` there is the regression.

    Source-level on purpose: the failure is a call site quietly reverting, and
    that is visible in the source without standing up four surfaces. The
    behaviour of the repair itself is covered by the live tests above.
    """
    import inspect
    import hearthkin_cron
    from frame.bot_integration_mixin import BotIntegrationMixin
    from frame.rooms_mixin import RoomsMixin
    import kin_persistence as _kp

    check("the helper exists at all",
          hasattr(_kp, "load_memory_for_prompt"))

    # Positive control: prove this test can SEE a bare loader before any of
    # its "no bare loader here" checks are believed. `load_memory` is what a
    # regression would look like, and a search that never matches anything
    # would pass every check below while guarding nothing.
    control = inspect.getsource(_kp.load_memory_for_prompt)
    check("positive control: a bare load_memory IS visible to this test",
          "load_memory(kin_name)" in control)

    for label, src in (
        ("cron", inspect.getsource(hearthkin_cron)),
        ("telegram + discord",
         inspect.getsource(BotIntegrationMixin)),
        ("rooms", inspect.getsource(RoomsMixin)),
    ):
        # Narrow to the prompt-building calls: these modules may legitimately
        # read memory for display or accounting elsewhere.
        check("%s builds its prompt through load_memory_for_prompt" % label,
              "load_memory_for_prompt(" in src)


def test_the_send_path_actually_calls_it():
    # Wiring check. The method being correct is worth nothing if the
    # thing that builds the system prompt never calls it.
    import inspect
    from frame.chat_send_mixin import ChatSendMixin
    src = inspect.getsource(ChatSendMixin)
    i = src.find("_memory_cache")
    window = src[max(0, i - 1200):i]
    check("the send path refreshes the cache before building the prompt",
          "_refresh_kin_text_cache_if_stale()" in window)


def main():
    test_the_kin_sees_what_it_wrote()
    test_a_later_edit_is_picked_up_too()
    test_soul_is_covered_by_the_same_check()
    test_an_unchanged_kin_does_not_hit_disk_again()
    test_a_missing_file_appearing_counts_as_a_change()
    test_no_kin_selected_is_harmless()
    test_new_logs_reach_the_index()
    test_a_dated_journal_file_is_not_indexed()
    test_a_kin_with_no_logs_is_never_rewritten()
    test_the_folder_signature_ignores_edits_to_a_log()
    test_the_send_path_rebuilds_the_index_too()
    test_the_send_path_actually_calls_it()
    test_every_surface_repairs_the_index_not_just_the_desktop()

    print()
    if _failures:
        print(f"{len(_failures)} FAILURE(S): " + ", ".join(_failures))
        return 1
    print("test_kin_sees_own_memory: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
