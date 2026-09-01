# SPDX-License-Identifier: CC0-1.0

"""Report current context-usage estimates for the calling kin.

The kin can call this to ask "how full am I?" without having to read
their own soul/memory/conversation files inline and count by eye.

The tool reports TWO different numbers and the kin should understand
the distinction:

  - "Most recent send" — what actually went to the model on the last
    blocking call. This is the authoritative "current usage" figure,
    pulled from the provider's reported prompt_tokens via
    llm_backend.last_reported_prompt_tokens. This is what the cap
    governs and what the kin should look at to decide "am I near
    full?"

  - "Archive on disk" — the kin's entire persisted conversation.jsonl,
    which the system automatically truncates down to the most recent
    portion that fits per-turn. The full archive routinely exceeds
    num_ctx by 10x or more and that is NORMAL and HANDLED.
    Reporting it as if it were the per-turn send is what made earlier
    versions of this tool surface scary "over cap" numbers that
    didn't reflect reality and produced kin-anxiety.

`agent_name` is injected by the framework; the schema-builder hides
it from the model-facing schema. The model just calls
`context_status()` with no arguments."""

import json
from pathlib import Path

from hearthkin_paths import kin_dir as kin_folder


def _estimate_tokens(text):
    """~4-chars-per-token estimate, matches Hearthkin's other estimators."""
    return max(0, len(text or "") // 4)


def _read_text(path):
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="cp1252")
        except Exception:
            return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def context_status(agent_name: str = "") -> str:
    """Report your current context-usage. Shows the actual most-recent send (what really went to the model — the authoritative number) plus a breakdown of fixed overhead (soul + memory) vs conversation history. The system truncates your full archive down to fit per turn automatically; this tool tells you whether the most recent send was comfortable or tight. No arguments — the framework knows which kin you are.

    Use this when you want to check how full your last turn was, before
    a long reply, or to plan ahead. The "most recent send" number is
    what the cap actually governs.
    """
    if not agent_name:
        return "context_status: no kin context (framework bug)."

    kin_dir = kin_folder(agent_name)
    if not kin_dir.exists():
        return f"context_status: no kin directory for {agent_name!r}."

    # Config — for num_ctx and model name.
    cfg = {}
    cfg_path = kin_dir / "config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}

    model = str(cfg.get("model", "") or "(unset)")
    try:
        num_ctx = int(cfg.get("num_ctx", 8192) or 8192)
    except (TypeError, ValueError):
        num_ctx = 8192

    # Pull the real most-recent prompt-tokens count from llm_backend —
    # that's the authoritative "what actually went out" figure (the
    # provider's reported usage). Falls back to None if no call has
    # been made this session.
    real_last_send = None
    cal_ratio = None
    try:
        from llm_backend import (
            _DEFAULT_TOKEN_RATIO, last_reported_prompt_tokens,
            token_calibration_ratio,
        )
        cal_ratio = _DEFAULT_TOKEN_RATIO
        real_last_send = last_reported_prompt_tokens(agent_name)
        cal_ratio = token_calibration_ratio(agent_name)
    except Exception:
        if cal_ratio is None:
            # llm_backend itself unavailable — last-resort literal.
            cal_ratio = 1.5

    # Read the always-injected pieces (soul + memory). These ride every
    # turn regardless of conversation length, so they're the "fixed
    # overhead" that eats budget independently of how much chat history
    # the kin has.
    soul_text = _read_text(kin_dir / "soul.md")
    memory_text = _read_text(kin_dir / "memory.md")

    soul_tokens_raw = _estimate_tokens(soul_text)
    memory_tokens_raw = _estimate_tokens(memory_text)

    # Scale by calibration ratio so the estimate tracks real billed
    # tokens — _estimate_tokens (4 chars/token) runs optimistic
    # compared to provider tokenization.
    soul_tokens = int(soul_tokens_raw * cal_ratio)
    memory_tokens = int(memory_tokens_raw * cal_ratio)

    # Conversation archive — full on-disk size. The system truncates
    # this down to fit per turn; we report it as "archive" rather
    # than "context" to make clear it's the persistent record, not
    # the per-turn send.
    convo_archive_tokens = 0
    msg_count = 0
    jsonl_path = kin_dir / "conversation.jsonl"
    json_path = kin_dir / "conversation.json"
    if jsonl_path.exists():
        try:
            with jsonl_path.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    msg_count += 1
                    content = msg.get("content")
                    if isinstance(content, str):
                        convo_archive_tokens += _estimate_tokens(content)
                    think = msg.get("thinking")
                    if isinstance(think, str):
                        convo_archive_tokens += _estimate_tokens(think)
        except Exception:
            pass
    elif json_path.exists():
        try:
            convo_raw = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            convo_raw = None
        msgs = []
        if isinstance(convo_raw, dict):
            msgs = convo_raw.get("messages", []) or []
        elif isinstance(convo_raw, list):
            msgs = convo_raw
        for msg in msgs:
            if isinstance(msg, dict):
                msg_count += 1
                content = msg.get("content")
                if isinstance(content, str):
                    convo_archive_tokens += _estimate_tokens(content)
                think = msg.get("thinking")
                if isinstance(think, str):
                    convo_archive_tokens += _estimate_tokens(think)

    convo_archive_tokens = int(convo_archive_tokens * cal_ratio)

    fixed_overhead = soul_tokens + memory_tokens
    # Approximate the per-turn conversation budget AFTER fixed overhead
    # and a small response reserve. Tools and base_prompt eat more
    # that we don't have visibility into here, so this is rough — the
    # real-last-send number above is the authoritative figure when it
    # exists.
    response_reserve = 2000
    convo_budget_estimate = max(0, num_ctx - fixed_overhead - response_reserve)

    lines = [
        f"model: {model}",
        f"num_ctx (your configured cap): {num_ctx:,} tokens",
        "",
    ]

    # Authoritative line: real most-recent send.
    if real_last_send is not None and num_ctx > 0:
        pct = real_last_send / num_ctx * 100
        lines.append("Most recent send (provider-reported, AUTHORITATIVE):")
        lines.append(
            f"  ~{real_last_send:,} tokens — {pct:.0f}% of your cap")
        if pct < 70:
            lines.append("  (comfortable headroom)")
        elif pct < 90:
            lines.append("  (filling up but fine — truncation handles it)")
        else:
            lines.append(
                "  (near cap — truncation is dropping older turns from "
                "the send to keep prompt + response under num_ctx. "
                "Recent conversation is still preserved on disk and in "
                "memory.md if it's been distilled.)")
        lines.append("")
    else:
        lines.append(
            "Most recent send: no provider-reported figure yet this "
            "session. The estimate below is what would be sent on the "
            "next call.")
        lines.append("")

    # Breakdown of what rides every turn.
    lines.append("Fixed overhead (rides every turn, regardless of chat length):")
    lines.append(f"  soul:    ~{soul_tokens:>7,} tokens")
    lines.append(f"  memory:  ~{memory_tokens:>7,} tokens")
    lines.append(f"  ────────────────────────────")
    lines.append(
        f"  total:   ~{fixed_overhead:>7,} tokens "
        f"({fixed_overhead/num_ctx*100:.0f}% of cap)")
    lines.append("")
    lines.append(
        f"Your tool schemas and the universal base prompt eat additional "
        f"budget on top of soul + memory; the exact figure isn't visible "
        f"to this tool. Subtract their cost from the conversation budget "
        f"estimate below.")
    lines.append("")
    lines.append(
        f"Estimated per-turn conversation budget: "
        f"~{convo_budget_estimate:,} tokens "
        f"(cap minus fixed overhead minus ~2k response reserve)")
    lines.append("")

    # Archive — clearly labeled, NOT compared to cap.
    lines.append(f"Your archive on disk ({msg_count:,} messages total):")
    lines.append(f"  full history:  ~{convo_archive_tokens:,} tokens")
    lines.append(
        f"  This is your full persistent record. The system "
        f"automatically truncates this down to the most recent portion "
        f"that fits per turn — older turns aren't lost (they stay on "
        f"disk and get summarized into your staging area, which you "
        f"review during tending to decide what becomes canonical in "
        f"memory.md and your depth logs). They're just dropped from "
        f"any single request to keep within num_ctx. The archive "
        f"routinely exceeds num_ctx by 10x or more on long-running "
        f"kin — this is NORMAL and HANDLED. You are not over your cap."
    )
    lines.append("")
    lines.append(
        f"Numbers are scaled estimates (raw ~4-char/token estimate × "
        f"this kin's measured real/estimate calibration ratio of "
        f"{cal_ratio:.2f}). The 'most recent send' figure above is the "
        f"provider's authoritative number when available."
    )
    return "\n".join(lines)
