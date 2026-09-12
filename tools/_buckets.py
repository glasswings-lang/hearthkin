# SPDX-License-Identifier: CC0-1.0

"""Tool access buckets for per-user gating in Telegram (and any future
multi-user surface).

The buckets are named tiers along a single risk axis (the sets below are
the source of truth; this is the shape):
  none  — kin can chat, can't trigger any tools
  read  — read-only / informational tools (memory_search, read_file,
          list_directory, fetch_url, web_search, list_processes,
          context_status, recent_thinking, read_staging, analyze_sound)
  write — read + tools that modify files or save state (write_file,
          edit_file, note, use_webcam, archive_staging, tff)
  full  — write + tools that can execute commands (exec, kill_process)

The bucket decides whether a remote user can EVER call exec. What happens
when they do is stricter than on the desktop: a denylisted command is
refused; a command remembered for that user (or surface) runs; otherwise
it asks — in the chat on Telegram, on the operator's desktop for Discord.
The kin's `tool_trust` alone never skips that question remotely. It is
skipped only when the kin is `trusted` or `full` AND `remote_unattended_exec`
is on (Tool behaviour settings → "Run remote (Telegram/Discord) exec without
asking"). The desktop wrapper honours `full` as "no gating at all"; the
asymmetry is deliberate — the operator's local convenience setting should
not silently apply to requests that arrive over the internet.

Tools not in any bucket above are treated as `none`-equivalent (must be
explicitly listed in a future bucket if added). Same goes for tools
that aren't registered in the kin's tools.json — the effective tool
set is intersection of (kin's allowlist) ∩ (bucket's tool set), so a
kin that hasn't enabled `exec` in its tools.json never exposes exec
even to a `full`-bucket user.

Power users editing config JSON by hand can use a list of tool names
instead of a bucket name — `tools_for_bucket` accepts both.
"""

# Read-only / informational tools. None of these change state on disk or
# spawn processes.
_READ = frozenset({
    "memory_search",
    "read_file",
    "list_directory",
    "fetch_url",
    "web_search",
    "list_processes",
    "context_status",
    "recent_thinking",
    "read_staging",
    # analyze_sound reads an audio file and returns objective acoustic facts;
    # no state change, no process spawn — same read-only exposure as read_file
    # (it can read any file on the host, so it sits with the other readers).
    "analyze_sound",
})

# Filesystem-mutating tools layered on top of read. `note` lives here
# (not in read) because it appends to files in the kin directory —
# memory.md rides every turn, so a read-bucket user with note access
# would have a durable injection path into the kin's context.
# use_webcam goes here because (a) it captures fresh state to disk
# (the attachment file), (b) it has physical-world implications worth
# gating beyond the read-only tier, and (c) on top of bucket gating,
# Telegram users get a SEPARATE per-user webcam-permission radio
# (ask / auto / deny) enforced by the executor wrap — so the bucket
# says "user can ever call use_webcam" and the radio says "what
# happens when they do."
# tff (the Time for Family park game) lives here (not read) because each turn
# persists the kin's own park save to disk. It's sandboxed to the kin's private
# game state — it can't touch the human's files or run commands — so it sits at
# the write tier, not full. A tool missing from EVERY bucket is invisible on
# Telegram regardless of the kin's tools.json, which is exactly how this tool
# (then named creature_park) failed to appear for a Telegram-side kin.
_WRITE = _READ | frozenset({
    "write_file",
    "edit_file",
    "note",
    "use_webcam",
    "archive_staging",
    "tff",
})

# Process-execution tools layered on top of write.
_FULL = _WRITE | frozenset({
    "exec",
    "kill_process",
})

# Bucket name → set of tool names.
BUCKETS = {
    "none": frozenset(),
    "read": _READ,
    "write": _WRITE,
    "full": _FULL,
}

# Public list, in escalation order, for UI dropdowns.
BUCKET_ORDER = ("none", "read", "write", "full")

# Tools deliberately kept OUT of every bucket — desktop-only by design, never
# drivable over a public bot surface. A tool in no bucket is silently dropped
# on Telegram; that's correct ONLY when it's intentional. List such tools here
# so the intent is explicit and recorded. tests/test_tool_buckets.py fails if a
# registered tool is neither bucketed NOR listed here — so forgetting to bucket
# a new tool (which is exactly how creature_park went invisible on Telegram for
# an evening) becomes a loud test failure instead of a silent mystery. Any tool
# whose blast radius is only acceptable on a single-user, physically-present
# surface belongs here rather than in a bucket.
INTENTIONALLY_TELEGRAM_BLOCKED = frozenset({
    # reach_out is proactive-only: it is granted solely on a heartbeat or a
    # scheduled cron wake (see tools.load_tools proactive_wake / cron_turn),
    # never through the Telegram per-user bucket. It is deliberately absent
    # from every bucket so it can't be driven from a chat turn.
    "reach_out",
})

# One-line user-facing explainers for the Settings telegram UI.
BUCKET_EXPLAINER = {
    "none": "Chat only. No tools can be triggered by this user.",
    "read": "Read-only tools: memory_search, read_file, list_directory, "
            "fetch_url, web_search, list_processes, context_status, "
            "recent_thinking, read_staging, analyze_sound.",
    "write": "Read tools + write_file, edit_file, note (can modify "
             "kin files) + archive_staging + use_webcam (capture from "
             "host webcam) + tff (play the kin's own Time for Family park "
             "game). use_webcam is additionally gated by a per-user "
             "permission setting (ask / auto / deny).",
    "full": "All tools including exec. A command this person asks for "
            "still needs approval — in the chat on Telegram, on your "
            "desktop for Discord — unless you remembered it, or the kin "
            "is trusted or full AND \"Run remote (Telegram/Discord) exec "
            "without asking\" is on in Tool behaviour settings. Denylisted "
            "commands are always refused.",
}


def tools_for_bucket(bucket):
    """Return the set of tool names a given bucket allows.

    Accepts:
      - a bucket name string ('none' / 'read' / 'write' / 'full')
      - a list/tuple/set of explicit tool names (power-user custom list)
      - None or anything else → empty set (safe default)
    """
    if isinstance(bucket, str):
        return BUCKETS.get(bucket.strip().lower(), frozenset())
    if isinstance(bucket, (list, tuple, set, frozenset)):
        return frozenset(str(t) for t in bucket)
    return frozenset()


def filter_tool_names(kin_allowlist, bucket):
    """Intersect the kin's enabled tools with the bucket's tool set.

    `kin_allowlist`: iterable of tool names from the kin's tools.json.
    `bucket`: string bucket name OR a list of explicit tool names.

    Returns a list (preserves order of kin_allowlist for stable schema
    ordering at the model side)."""
    allowed = tools_for_bucket(bucket)
    return [name for name in (kin_allowlist or []) if name in allowed]
