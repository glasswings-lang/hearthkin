# SPDX-License-Identifier: CC0-1.0
"""dialogs.telegram_user — the Telegram person window.

Since 12 Nov 2026 this is the shared PersonWindow (dialogs/person_window.py)
on the Telegram surface, so Telegram and Discord offer exactly the same
settings. The class and its old get_values() stay for callers written against
the old window.
"""

from .person_window import PersonWindow


class _TelegramUserDialog(PersonWindow):
    def __init__(self, parent, user_id="", label="", bucket="none",
                 share_desktop=False, mirror_to_telegram=False,
                 webcam_permission="ask", tool_cap=None,
                 exec_approval="default", values=None):
        if values is None:
            values = {"label": label, "bucket": bucket or "none",
                      "share_desktop": share_desktop,
                      "mirror": mirror_to_telegram,
                      "webcam": webcam_permission, "tool_cap": tool_cap,
                      "exec_approval": exec_approval}
        super().__init__(parent, "telegram", user_id, values)

    def get_values(self):
        """(user_id, label, bucket, share_desktop, mirror_to_telegram,
        webcam_permission) — the old window's answer."""
        v = self.get_settings()
        return (self.get_user_id(), v["label"], v["bucket"],
                v["share_desktop"], v["mirror"], v["webcam"])
