# SPDX-License-Identifier: CC0-1.0
"""dialogs.discord_user — the Discord person window.

Since 12 Nov 2026 this is the shared PersonWindow (dialogs/person_window.py)
on the Discord surface, so it offers exactly the settings Telegram does. The
class and its old get_values() stay for callers written against the old
window.
"""

from .person_window import PersonWindow


class _DiscordUserDialog(PersonWindow):
    def __init__(self, parent, user_id="", bucket="none", tool_cap=None,
                 exec_approval="default", values=None):
        if values is None:
            values = {"bucket": bucket or "none", "tool_cap": tool_cap,
                      "exec_approval": exec_approval}
        super().__init__(parent, "discord", user_id, values)

    def get_values(self):
        """(user_id, bucket) — the old window's answer."""
        return self.get_user_id(), self.get_settings()["bucket"]
