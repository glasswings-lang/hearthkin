"""FileMenuMixin — extracted from hearthkin.pyw (2026-07 modularisation).

One cohesive slice of the Hearthkin frame's behavior. Mixed into the
Hearthkin(wx.Frame) class in hearthkin.pyw; every method here runs with
the frame as `self`. Moved verbatim — no logic changed.
"""
from frame_shared import (
    CONVOS_DIR, Path, _clean_chat_message, agent_dir, append_failure_log,
    atomic_write_text, datetime, json, load_agent_config, now_iso, nvda_speak,
    save_agent_conversation, save_room_conversation, strip_model_annotation, wx,
)


class FileMenuMixin:

    # --- File ops --- #

    def _on_new(self, event):
        if self.current_room is not None:
            self._set_status("Rooms persist their own history. Use 'Clear chat' to wipe a room.")
            return
        archived_name = None
        if self.conversation:
            dlg = wx.MessageDialog(
                self,
                f"Start fresh with {self.current_agent or 'this kin'}? "
                "The current conversation will be archived to the kin's "
                "conversations folder first, then cleared. "
                f"{self.current_agent or 'The kin'}'s memory and soul are "
                "untouched.",
                "New conversation",
                wx.YES_NO | wx.CANCEL | wx.NO_DEFAULT | wx.ICON_WARNING,
            )
            r = dlg.ShowModal()
            dlg.Destroy()
            if r != wx.ID_YES:
                return
            # Auto-archive before wiping. If the archive doesn't land on
            # disk, ABORT the wipe — never trade the live conversation for
            # a failed save. (_write_convo already logs + shows the error.)
            try:
                CONVOS_DIR.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            agent_slug = self.current_agent or "unknown"
            archive_path = CONVOS_DIR / f"convo_{agent_slug}_{stamp}_pre-new.json"
            self._write_convo(archive_path)
            if not archive_path.exists():
                self._set_status(
                    "New conversation cancelled — archive failed, your "
                    "conversation is untouched (see logs/save_failures.log)."
                )
                return
            archived_name = archive_path.name
        self.conversation.clear()
        self.chat_display.Clear()
        self.current_convo_file = None
        self._persist_current_conversation()
        self._update_token_display()
        if archived_name:
            self._set_status(
                f"New conversation started. Previous one archived to "
                f"{archived_name} in the kin's conversations folder "
                f"(File → Open to reload it). Memory and soul untouched."
            )
        else:
            self._set_status("New conversation started.")

    def _on_open(self, event):
        if self.current_room is not None:
            self._set_status("Open is for single-kin conversations. Switch to a kin first.")
            return
        dlg = wx.FileDialog(self, "Open conversation", str(CONVOS_DIR),
                            wildcard="JSON files (*.json)|*.json|All files (*.*)|*.*",
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() == wx.ID_OK:
            path = Path(dlg.GetPath())
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                raw = data.get("messages", [])
                cleaned = []
                for m in raw:
                    entry = _clean_chat_message(m)
                    if entry is not None:
                        cleaned.append(entry)
                # Loading a snapshot REPLACES the kin's current
                # conversation on disk. Confirm so a casual "let me peek
                # at March" doesn't wipe a long live conversation.
                confirm = wx.MessageDialog(
                    self,
                    f"Loading this snapshot will REPLACE "
                    f"{self.current_agent}'s current conversation with "
                    f"its contents ({len(cleaned)} message"
                    f"{'s' if len(cleaned) != 1 else ''}). "
                    f"The current conversation on disk will be "
                    f"overwritten.\n\nContinue?",
                    "Replace conversation?",
                    wx.YES_NO | wx.CANCEL | wx.NO_DEFAULT | wx.ICON_WARNING,
                )
                r = confirm.ShowModal()
                confirm.Destroy()
                if r != wx.ID_YES:
                    self._set_status("Open cancelled.")
                    dlg.Destroy()
                    return
                # Force-rewrite kin's main conversation.jsonl to match
                # the snapshot FIRST; only adopt the snapshot as live
                # state once the write succeeded. Without this the next
                # auto-persist call uses the stale _persisted_msg_count
                # from the prior load — appending the snapshot tail on
                # top of the old conversation, or fragment-shrinking it.
                # Either way produces a corrupt hybrid file (audit H1);
                # assigning state before the write recreated the same
                # hybrid on a write failure (audit L-B20).
                save_agent_conversation(self.current_agent, cleaned)
                self.conversation = cleaned
                self.current_convo_file = path
                self._persisted_msg_count = len(self.conversation)
                self._conversation_mtime_seen = (
                    self._stat_conversation_mtime(self.current_agent)
                )
                # Recompute render window so a longer snapshot doesn't
                # paint only the prior window slice (audit H2).
                window_cfg = int(
                    self.config.get("chat_history_window", 200) or 0
                )
                if window_cfg <= 0:
                    self._render_window = len(self.conversation)
                else:
                    self._render_window = min(
                        window_cfg, len(self.conversation),
                    )
                self._render_conversation()
                try:
                    self._refresh_load_older_button()
                except Exception:
                    pass
                self._update_token_display()
                self._set_status(f"Loaded {path.name} (now the active conversation)")
            except Exception as e:
                wx.MessageBox(f"Failed to load: {e}", "Error", wx.OK | wx.ICON_ERROR)
        dlg.Destroy()

    def _on_save(self, event):
        if self.current_room is not None:
            try:
                save_room_conversation(self.current_room, self.room_conversation)
            except Exception as e:
                append_failure_log(
                    "save_failures.log",
                    self.current_room or "?",
                    "save_room_conversation (manual save)",
                    e,
                )
            self._set_status("Room conversation auto-saves; export to markdown via File menu.")
            return
        self._save_conversation_interactive()

    def _save_conversation_interactive(self):
        """Ctrl+S / File→Save handler. Default for a kin is "already
        auto-saved" — every reply has already been written to the kin's
        auto-persist file. We surface that fact instead of silently
        opening a file dialog and giving the false impression that
        nothing has been saved.

        The user can still create a separate named snapshot by answering
        Yes in the dialog — that path keeps the old behavior (file
        picker, _write_convo, current_convo_file linkage so subsequent
        auto-saves also write to the snapshot)."""
        if not self.conversation:
            wx.MessageBox("Nothing to save yet.", "Save", wx.OK)
            return False
        # If the user already created or opened a snapshot file, the
        # auto-persist + snapshot are kept in sync by
        # _persist_current_conversation — no need to bother them on Ctrl+S.
        if self.current_convo_file:
            self._set_status(
                f"Already auto-saved to {self.current_convo_file.name} "
                f"and the kin's auto-persist file."
            )
            return True
        # No snapshot exists. Tell the user where their data actually is,
        # and offer to make a separate named snapshot if they want one.
        auto_path = agent_dir(self.current_agent) / "conversation.jsonl" \
            if self.current_agent else None
        msg = (
            "This conversation is auto-saved after every reply.\n\n"
            f"Auto-save location:\n{auto_path}\n\n"
            "When you reopen this kin, hearthkin loads from that file — "
            "you don't need to save manually. It's already done.\n\n"
            "Want to also save a separate snapshot to a named file? Useful "
            "for keeping a frozen copy of this conversation, or moving it "
            "outside the kin's directory."
        )
        dlg = wx.MessageDialog(
            self, msg,
            "Already auto-saved",
            wx.YES_NO | wx.CANCEL | wx.NO_DEFAULT | wx.ICON_INFORMATION,
        )
        r = dlg.ShowModal()
        dlg.Destroy()
        if r != wx.ID_YES:
            self._set_status("Conversation is auto-saved — nothing to do.")
            return True
        # User wants a separate snapshot. Open the file picker.
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        agent_slug = self.current_agent or "unknown"
        file_dlg = wx.FileDialog(
            self, "Save snapshot to a named file", str(CONVOS_DIR),
            defaultFile=f"convo_{agent_slug}_{stamp}.json",
            wildcard="JSON files (*.json)|*.json",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        ok = False
        if file_dlg.ShowModal() == wx.ID_OK:
            path = Path(file_dlg.GetPath())
            self._write_convo(path)
            self.current_convo_file = path
            ok = True
        file_dlg.Destroy()
        return ok

    def _write_convo(self, path):
        data = {
            "agent": self.current_agent,
            "model": self._current_chat_model_clean(),
            "saved_at": now_iso(),
            "messages": self.conversation,
        }
        try:
            atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            append_failure_log(
                "save_failures.log", self.current_agent, f"manual:{path.name}", e,
            )
            wx.MessageBox(
                f"Could not save to {path}:\n\n{e}\n\n"
                f"See logs/save_failures.log for the full record.",
                "Save failed", wx.OK | wx.ICON_ERROR,
            )
            return
        self._set_status(f"Saved: {path.name}")

    def _on_export_md(self, event):
        if self.current_room is not None:
            self._export_room_md()
            return
        if not self.conversation:
            wx.MessageBox("Nothing to export yet.", "Export", wx.OK)
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        agent_slug = self.current_agent or "unknown"
        dlg = wx.FileDialog(self, "Export as markdown", str(CONVOS_DIR),
                            defaultFile=f"convo_{agent_slug}_{stamp}.md",
                            wildcard="Markdown (*.md)|*.md",
                            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        if dlg.ShowModal() == wx.ID_OK:
            path = Path(dlg.GetPath())
            lines = [f"# Conversation with {agent_slug}", ""]
            lines.append(f"_Model:_ `{self._current_chat_model_clean()}`")
            lines.append(f"_Saved:_ {now_iso()}")
            lines.append("")
            for m in self.conversation:
                role_raw = m.get("role")
                # Tool round-trip turns aren't human-conversational. Skip
                # them in the markdown export so the doc reads as a clean
                # back-and-forth. Tool actions are still recoverable from
                # conversation.json if anyone needs forensics.
                if role_raw == "tool":
                    continue
                content = m.get("content")
                if role_raw == "assistant" and not isinstance(content, str):
                    continue
                role = "You" if role_raw == "user" else (self.current_agent or "Model")
                ts = m.get("ts", "")
                ts_obj = None
                try:
                    ts_obj = datetime.datetime.fromisoformat(ts) if ts else None
                except ValueError:
                    ts_obj = None
                tstr = f" · {ts_obj.strftime('%Y-%m-%d %H:%M')}" if ts_obj else ""
                lines.append(f"## {role}{tstr}")
                lines.append("")
                lines.append(content or "")
                lines.append("")
            atomic_write_text(path, "\n".join(lines))
            self._set_status(f"Exported: {path.name}")
        dlg.Destroy()

    def _export_room_md(self):
        if not self.room_conversation:
            wx.MessageBox("Nothing in this room yet.", "Export", wx.OK)
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        room = self.current_room or "room"
        dlg = wx.FileDialog(self, "Export room as markdown", str(CONVOS_DIR),
                            defaultFile=f"room_{room}_{stamp}.md",
                            wildcard="Markdown (*.md)|*.md",
                            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        if dlg.ShowModal() == wx.ID_OK:
            path = Path(dlg.GetPath())
            members = self.room_cfg.get("members", [])
            member_models = []
            for m in members:
                cfg = load_agent_config(m)
                member_models.append(f"- **{m}** — `{strip_model_annotation(cfg.get('model', '?'))}`")
            lines = [f"# Room: {room}", ""]
            lines.append(f"_Saved:_ {now_iso()}")
            lines.append("")
            lines.append("## Members")
            lines.append("")
            lines.extend(member_models)
            lines.append("")
            lines.append("---")
            lines.append("")
            for msg in self.room_conversation:
                speaker = msg.get("speaker") or ("You" if msg.get("role") == "user" else "Model")
                model = msg.get("model", "")
                ts = msg.get("ts", "")
                ts_obj = None
                try:
                    ts_obj = datetime.datetime.fromisoformat(ts) if ts else None
                except ValueError:
                    ts_obj = None
                tstr = f" · {ts_obj.strftime('%Y-%m-%d %H:%M')}" if ts_obj else ""
                model_str = f" · `{model}`" if model else ""
                lines.append(f"## {speaker}{tstr}{model_str}")
                lines.append("")
                lines.append(msg.get("content", ""))
                lines.append("")
            atomic_write_text(path, "\n".join(lines))
            self._set_status(f"Exported room: {path.name}")
        dlg.Destroy()

    def _on_restore_history(self, event):
        """File → Restore a kin's history… — bring a kin's OWN archived
        conversation.jsonl back (archived kin folder, rescued backup, a
        conversation cleaned up outside the app).

        Separate from Import on purpose: import writes markers and stamps
        `source: import:<label>`, which would relabel a kin's own past as
        carried-in seed history and lose where each turn came from. See
        dialogs/restore_history.py."""
        from dialogs.restore_history import RestoreHistoryDialog
        from importers import restore_from_files

        dlg = RestoreHistoryDialog(self)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            picked = dlg.result
        finally:
            dlg.Destroy()
        if not picked:
            return

        # One call with every file rather than a loop: restore_rows builds
        # its seen-set once and extends it as it walks the incoming rows,
        # so a single pass dedupes ACROSS the chosen files as well as
        # against the kin. A loop would let two archives that overlap EACH
        # OTHER both land — and overlapping archives is the normal case,
        # since that is what having several backups means.
        unreadable = []
        try:
            result = restore_from_files(
                picked.get("paths") or [picked["path"]], picked["kin"],
                mode=picked["mode"], report=unreadable,
            )
        except Exception as e:  # noqa: BLE001
            wx.MessageBox(f"Couldn't restore that history:\n\n{e}",
                          "Restore failed", wx.OK | wx.ICON_ERROR, self)
            return

        kin = result["kin"]

        # A restore rewrites conversation.jsonl underneath whatever the app
        # is holding. If this is the loaded kin, our in-memory list is now
        # stale and short — and chat_stream_mixin persists by writing
        # `self.conversation` back in full, so the very next turn would
        # save the pre-restore list straight over the restored file.
        # `_persisted_msg_count` and the mtime watermark are wrong too.
        # The background poller would notice eventually; a turn taken
        # before it ticks would not survive that wait. Reload now, so
        # there's no window at all.
        if getattr(self, "current_agent", None) == kin:
            try:
                self._load_agent(kin)
            except Exception:
                # Reload is belt-and-braces over the poller, not the only
                # safeguard — don't fail a completed restore over it.
                pass

        skipped = result.get("skipped_duplicates") or 0
        first = (result.get("first_ts") or "")[:10]
        last = (result.get("last_ts") or "")[:10]
        date_part = f" ({first} to {last})" if first and last else ""
        skip_part = f", {skipped} already there" if skipped else ""
        # Say what didn't make it. A file silently dropped from a
        # twenty-file restore is how an archive arrives incomplete with no
        # record of what is missing.
        miss_part = (f", {len(unreadable)} file"
                     f"{'s' if len(unreadable) != 1 else ''} skipped"
                     if unreadable else "")
        msg = (f"Restored {result['written']} turns to {kin}"
               f"{date_part}{skip_part}{miss_part}.")
        self._set_status(msg)
        try:
            nvda_speak(msg)
        except Exception:
            pass

    def _on_import_history(self, event):
        """File → Import history… — bring foreign chat history (Telegram
        archives, hand-authored seed history, more formats to come)
        into a kin's conversation.jsonl. See docs/design/history-import.md."""
        from dialogs.import_history import ImportHistoryDialog
        dlg = ImportHistoryDialog(self)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            result = dlg.result
        finally:
            dlg.Destroy()
        if not result:
            return

        kin = result["kin"]
        written = result["written"]
        dropped = result["dropped"]
        # If a new kin was created, refresh the agents listing so the
        # kin/room selector picks it up immediately.
        if result.get("created_kin"):
            try:
                self._populate_agents_list()
            except AttributeError:
                # Older selector populates on next interaction anyway;
                # don't fail the import over a refresh quirk.
                pass

        first = (result.get("first_ts") or "")[:10]
        last = (result.get("last_ts") or "")[:10]
        date_part = f" ({first} to {last})" if first and last else ""
        drop_part = f", {dropped} dropped" if dropped else ""
        msg = (
            f"Imported {written} turns into {kin}{date_part}{drop_part}."
        )
        self._set_status(msg)
        try:
            nvda_speak(msg)
        except Exception:
            pass
