# SPDX-License-Identifier: CC0-1.0
"""Every setting a surface has, answered for (23 Nov 2026).

The surface map (_surface_matrix.py) is strict about everything written on it,
but it could only check what someone remembered to write down. Telegram and
Discord had a "Show tool calls" switch for weeks while the main window and
rooms had none, and nothing failed, because the switch had never been put on
the map. The person: "why do you have to change it before it can catch it?"

So this goes looking instead of waiting to be told. Every setting in every
surface's defaults (Telegram's, Discord's, a room's, and the kin's own
"desktop_" ones) must be answered here, one of three ways:

  EVERY(capability)   the same thing on every surface; it must be a row on the
                      surface map, which then holds every surface to it.
  HERE(reason)        it belongs to this surface alone, and why.
  QUESTION(text)      not answered yet. Printed on every run as an open
                      question, like the map's gaps, until someone decides.

A setting that appears anywhere without an answer here FAILS the test
(tests/test_surface_parity.py), so a new switch on one surface can't sit
unnoticed while the others lack it.
"""


class EVERY:
    def __init__(self, capability):
        self.capability = capability


class HERE:
    def __init__(self, reason):
        self.reason = reason


class QUESTION:
    def __init__(self, text):
        self.text = text


_PEOPLE = "per_person_settings"

SETTINGS = {
    "telegram": {
        "allow_from": EVERY(_PEOPLE),
        "bot_token": HERE("The bot's own key; nothing else connects through it."),
        "enabled": HERE("Switches this bot on; the main window is always on."),
        "history_cap": QUESTION(
            "How many messages a Telegram chat keeps for the kin. Does every "
            "surface need a say in how much history it carries, or is this "
            "Telegram's own storage limit?"),
        "show_block_actions": EVERY("block_lines_switch"),
        "show_tool_calls": EVERY("tool_calls_switch"),
        "show_tool_summary": QUESTION(
            "Names the tools a reply used, at its end. Telegram and Discord "
            "have it; the main window and rooms show every call instead, and "
            "have no such footer. Should they?"),
        "user_exec_approval": EVERY("exec_approval"),
        "user_file_reach": EVERY(_PEOPLE),
        "user_is_operator": EVERY("operator_identity"),
        "user_labels": EVERY(_PEOPLE),
        "user_reach_said": EVERY(_PEOPLE),
        "user_share_desktop": EVERY("sharing_switches"),
        "user_tool_caps": EVERY("per_reply_tool_cap"),
        "user_tools": EVERY("tool_gating"),
        "user_webcam_permission": EVERY("webcam_approval"),
        "user_mirror_to_telegram": EVERY(_PEOPLE),
        "approval_timeout_secs": HERE(
            "Telegram asks for a command's approval in the chat itself and "
            "waits; Discord sends the question to the desktop."),
        "group_private_sees": EVERY("sharing_switches"),
        "group_sees_private": EVERY("sharing_switches"),
        "group_share_desktop": EVERY("sharing_switches"),
        "groups": HERE("Telegram's list of groups; Discord's places are its "
                       "channels, a room is its own place."),
        "message_wait_secs": EVERY("message_coalescing"),
    },
    "discord": {
        "allow_from": EVERY(_PEOPLE),
        "bot_token": HERE("The bot's own key; nothing else connects through it."),
        "enabled": HERE("Switches this bot on; the main window is always on."),
        "history_cap": QUESTION(
            "As Telegram's: how many messages a Discord chat keeps."),
        "show_block_actions": EVERY("block_lines_switch"),
        "show_tool_calls": EVERY("tool_calls_switch"),
        "show_tool_summary": QUESTION("As Telegram's: the main window and rooms "
                                      "have no tools-used footer."),
        "user_exec_approval": EVERY("exec_approval"),
        "user_file_reach": EVERY(_PEOPLE),
        "user_is_operator": EVERY("operator_identity"),
        "user_labels": EVERY(_PEOPLE),
        "user_reach_said": EVERY(_PEOPLE),
        "user_share_desktop": EVERY("sharing_switches"),
        "user_tool_caps": EVERY("per_reply_tool_cap"),
        "user_tools": EVERY("tool_gating"),
        "user_webcam_permission": EVERY("webcam_approval"),
        "user_mirror_desktop": EVERY(_PEOPLE),
        "message_wait_secs": EVERY("message_coalescing"),
        "channel_entries": HERE("Discord's server channels, each with its own "
                                "settings; Telegram's groups are its own list."),
        "channel_private_sees": EVERY("sharing_switches"),
        "channel_sees_private": EVERY("sharing_switches"),
        "channels": HERE("Discord's server channels."),
        "guilds": HERE("The Discord servers the bot is in."),
        "policy": HERE("Who may send this bot a direct message; Telegram uses "
                       "its people list."),
        "share_desktop": EVERY("sharing_switches"),
        "tool_progress_interval_secs": QUESTION(
            "The \"still working\" message during a long tool run. Discord "
            "lists it; Telegram reads the same name but it isn't in Telegram's "
            "defaults; the main window has its own cue under another name. "
            "One setting per surface, each listed?"),
    },
    "rooms": {
        "members": HERE("Which kin are in the room."),
        "context_note": HERE("A note added to each kin's prompt while in this "
                             "room."),
        "max_auto_rounds": HERE("Rounds the kin take on their own; only a room "
                                "has kin taking turns."),
        "auto_inactivity_min": HERE("Pauses those rounds when nobody's typed."),
        "per_turn_token_cap": QUESTION(
            "A room's own cap on each kin's reply length. Telegram and Discord "
            "use the kin's own caps. Should a room's override the kin's, and "
            "should chats be able to have one too?"),
        "distill_to_memory": EVERY("distillation_tick"),
        "show_tool_calls": EVERY("tool_calls_switch"),
        "show_block_actions": EVERY("block_lines_switch"),
    },
    "desktop": {
        "desktop_show_tool_calls": EVERY("tool_calls_switch"),
        "desktop_show_block_actions": EVERY("block_lines_switch"),
    },
}


def defaults():
    """The settings each surface actually has, read from the code's own
    defaults, never a hand-kept list."""
    import kin_persistence as kp
    return {
        "telegram": set(kp.DEFAULT_TELEGRAM_CONFIG),
        "discord": set(kp.DEFAULT_DISCORD_CONFIG),
        "rooms": set(kp.DEFAULT_ROOM_CONFIG),
        "desktop": {k for k in kp.DEFAULT_AGENT_CONFIG if k.startswith("desktop_")},
    }
