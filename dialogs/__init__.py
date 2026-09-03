# SPDX-License-Identifier: CC0-1.0

"""dialogs — wx.Dialog subclasses for Hearthkin.

Split from a single 4,300-line dialogs.py module into per-class files
under this package. Public names are re-exported here so existing
imports of the form `from dialogs import EditKinDialog, ...` keep
working unchanged.

Per-class modules:
    _shared.py        — _IntField (NVDA-friendly integer input widget)
    agent_name.py     — AgentNameDialog (new kin name prompt)
    app_prompts_all.py — AllAppPromptsDialog (install-wide prompt editor)
    base_prompt.py    — BasePromptDialog (shared base prompt editor)
    cron_entry.py     — CronEntryDialog (one scheduled wake-up)
    distill_prompt.py — DistillPromptDialog (per-kin summarizer prompt)
    edit_kin.py       — EditKinDialog (the seven-tab kin Settings dialog)
    exec_approval.py  — ExecApprovalDialog (allow/deny shell command)
    import_history.py — ImportHistoryDialog (foreign-log import)
    restore_history.py — RestoreHistoryDialog (a kin's own archive, back)
    room_edit.py      — RoomEditDialog (create or edit a multi-kin room)
    search.py         — SearchDialog (plain-text search across all kin)
    discord_user.py   — _DiscordUserDialog (per-user Discord settings)
    telegram_group.py — _TelegramGroupDialog (per-group Telegram settings)
    tool_probe_result.py — ToolProbeResultDialog (did this model call a tool?)
    telegram_user.py  — _TelegramUserDialog (per-user Telegram settings)
    usage_history.py  — UsageHistoryDialog (browsable usage.log view)
    webcam_approval.py — WebcamApprovalDialog (allow/deny webcam capture)
"""

from ._shared import _IntField, rebuild_listbox
from .agent_name import AgentNameDialog
from .app_prompts_all import AllAppPromptsDialog
from .base_prompt import BasePromptDialog
from .confirm_close import ConfirmCloseDialog
from .cron_entry import CronEntryDialog
from .health_check import HealthCheckDialog
from .sound_cues import SoundCuesDialog
from .heartbeat_settings import HeartbeatSettingsDialog
from .distill_prompt import DistillPromptDialog
from .tool_probe_result import ToolProbeResultDialog, format_probe_result
from .edit_kin import EditKinDialog
from .edit_message import EditMessageDialog
from .prompt_updates import PromptUpdatesDialog
from .dictation_settings import DictationSettingsDialog
from .recall_settings import RecallSettingsDialog
from .exec_approval import ExecApprovalDialog
from .import_history import ImportHistoryDialog
from .restore_history import RestoreHistoryDialog
from .api_providers import ApiProvidersDialog
from .ollama_machines import OllamaMachinesDialog
from .park_play import ParkPlayDialog
from .park_vocab import ParkVocabDialog
from .room_edit import RoomEditDialog
from .search import SearchDialog
from .discord_user import _DiscordUserDialog
from .telegram_group import _TelegramGroupDialog
from .telegram_user import _TelegramUserDialog
from .usage_history import UsageHistoryDialog
from .webcam_approval import WebcamApprovalDialog

__all__ = [
    "_IntField",
    "rebuild_listbox",
    "AgentNameDialog",
    "AllAppPromptsDialog",
    "BasePromptDialog",
    "ConfirmCloseDialog",
    "CronEntryDialog",
    "HealthCheckDialog",
    "SoundCuesDialog",
    "HeartbeatSettingsDialog",
    "DistillPromptDialog",
    "EditKinDialog",
    "EditMessageDialog",
    "PromptUpdatesDialog",
    "DictationSettingsDialog",
    "RecallSettingsDialog",
    "ExecApprovalDialog",
    "ImportHistoryDialog",
    "RestoreHistoryDialog",
    "ApiProvidersDialog",
    "OllamaMachinesDialog",
    "ParkPlayDialog",
    "ParkVocabDialog",
    "RoomEditDialog",
    "SearchDialog",
    "_TelegramGroupDialog",
    "_TelegramUserDialog",
    "_DiscordUserDialog",
    "UsageHistoryDialog",
    "WebcamApprovalDialog",
    "ToolProbeResultDialog",
    "format_probe_result",
]
