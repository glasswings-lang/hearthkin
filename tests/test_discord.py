# SPDX-License-Identifier: CC0-1.0
"""Guard tests for the Discord surface's pure logic.

The live parts (Gateway connection, run_tool_loop, model calls) are
integration-level and need discord.py + a real bot + a model. These unit
checks pin the pieces the surface's behavior depends on:

  * access control — deny-by-default: empty allow_from = nobody, "*" =
    anyone, populated = only those IDs
  * per-channel history — merged (conversation.jsonl by source tag) vs
    segregated (discord_history.json), each seeing ONLY its own channel
  * segregated store round-trip (append_discord_turn / load_discord_history)
  * a Discord scope key maps to a filename-safe staging name (Windows)
  * the discord config section defaults + back-fill onto legacy configs
"""

import os
import sys
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kin_persistence as kp        # noqa: E402
from discord_bot import DiscordBot  # noqa: E402

_fails = []


def check(label, cond):
    if not cond:
        _fails.append(label)
        print(f"  FAIL: {label}")
    else:
        print(f"  ok: {label}")


def _bot():
    return DiscordBot("X", lambda: {}, lambda: "", lambda: "",
                      lambda: {}, lambda l: None)


def test_access_control():
    # Deny-by-default (2026-07 security audit A3): a tool-capable kin must
    # not be reachable by "anyone in any server" out of the box.
    a = DiscordBot._is_allowed
    check("empty allow_from -> nobody", a(123, []) is False)
    check("None allow_from -> nobody", a(123, None) is False)
    check('"*" -> anyone', a(123, ["*"]) is True)
    check("listed id allowed", a(123, ["123", "456"]) is True)
    check("unlisted id blocked", a(999, ["123", "456"]) is False)
    check("int author vs str list matches", a(123, ["123"]) is True)
    check("str author vs int list matches", a("123", [123]) is True)


def test_short_args():
    f = DiscordBot._short_args
    check("dict args formatted", "path=notes.md" in f({"path": "notes.md"}))
    check("long value truncated", len(f({"c": "x" * 500})) < 220)
    check("non-dict passes through", f("raw") == "raw")


def test_channel_history_merged():
    bot = _bot()
    kp.load_agent_conversation = lambda name: [
        {"role": "user", "content": "[Al] hi", "source": "discord:123"},
        {"role": "assistant", "content": "hey", "source": "discord:123"},
        {"role": "user", "content": "[Bo] yo", "source": "discord:999"},
        {"role": "user", "content": "desktop", "source": ""},
    ]
    h = bot._channel_history(123, share=True)
    check("merged: only this channel's rows",
          h == [{"role": "user", "content": "[Al] hi"},
                {"role": "assistant", "content": "hey"}])
    check("merged: other channel excluded",
          bot._channel_history(999, share=True) ==
          [{"role": "user", "content": "[Bo] yo"}])
    check("merged: desktop rows excluded",
          all(t["content"] != "desktop"
              for t in bot._channel_history(123, share=True)))


def test_segregated_store_roundtrip():
    tk = "zz_disc_unit_kin"
    try:
        kp.append_discord_turn(tk, 123, {"role": "user", "content": "seg hi"})
        kp.append_discord_turn(tk, 123, {"role": "assistant", "content": "yo"})
        kp.append_discord_turn(tk, 777, {"role": "user", "content": "other"})
        data = kp.load_discord_history(tk)
        check("segregated: channels kept separate on disk",
              set(data.keys()) == {"123", "777"})
        check("segregated: channel 123 has both turns",
              len(data["123"]) == 2)
        # cap enforcement
        for i in range(250):
            kp.append_discord_turn(tk, 555, {"role": "user", "content": str(i)},
                                   cap=200)
        check("segregated: cap trims to newest 200",
              len(kp.load_discord_history(tk)["555"]) == 200)
    finally:
        d = kp.agent_dir(tk)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)


def test_scope_filename_safe():
    for scope in ("discord:123456789", "desktop", "tg:group:9"):
        safe = kp._staging_scope_safe(scope)
        bad = [c for c in (":", "/", chr(92)) if c in safe]
        check(f"scope {scope!r} filename-safe", not bad)


def test_config_defaults_and_backfill():
    d = kp.DEFAULT_AGENT_CONFIG.get("discord")
    check("discord section present",
          set(d or {}) == {"enabled", "bot_token", "policy",
                           "share_desktop", "allow_from",
                           "user_tools", "guilds", "channels"})
    check("policy default mention_only", d.get("policy") == "mention_only")
    check("share_desktop default off", d.get("share_desktop") is False)
    check("allow_from default deny-all", d.get("allow_from") == [])
    check("user_tools default empty", d.get("user_tools") == {})
    check("guilds default empty", d.get("guilds") == [])
    check("channels default empty", d.get("channels") == [])


def main():
    for t in (test_access_control, test_short_args, test_channel_history_merged,
              test_segregated_store_roundtrip, test_scope_filename_safe,
              test_config_defaults_and_backfill):
        print(f"\n[{t.__name__}]")
        t()
    if _fails:
        print(f"\nFAILED {len(_fails)}: " + ", ".join(_fails))
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
