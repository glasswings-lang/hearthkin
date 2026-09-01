"""DiagnosticsMixin — extracted from hearthkin.pyw (2026-07 modularisation).

One cohesive slice of the Hearthkin frame's behavior. Mixed into the
Hearthkin(wx.Frame) class in hearthkin.pyw; every method here runs with
the frame as `self`. Moved verbatim — no logic changed.
"""
from frame_shared import LOGS_DIR, datetime, logging


class DiagnosticsMixin:

    # --- Logging --- #

    def _setup_logging(self):
        root = logging.getLogger("hearthkin")
        lb_logger = logging.getLogger("llm_backend")
        # Unconditionally detach + close handlers on BOTH loggers. The
        # old shape only cleared llm_backend's handler inside the
        # enabled branch, so toggling logging OFF left llm_backend
        # writing conversation content to the stale session log until
        # restart — and never .close()d handlers leaked a file handle
        # per toggle cycle (audit M-F1).
        for lg in (root, lb_logger):
            for h in list(lg.handlers):
                lg.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        root.setLevel(logging.DEBUG)
        if self.config.get("logging_enabled"):
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = LOGS_DIR / f"session_{stamp}.log"
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            root.addHandler(fh)
            # Also capture llm_backend debug logs (SSE raw deltas) into same file
            lb_logger.setLevel(logging.DEBUG)
            lb_logger.addHandler(fh)
        self.logger = root

    def _log(self, msg):
        if self.config.get("logging_enabled"):
            self.logger.info(msg)

    def _log_empty_reply(self, speaker, model, raw_buf):
        """Always-on diagnostic for empty replies. Bypasses logging_enabled
        because empties are rare and we want them captured even when general
        session logging is off."""
        try:
            path = LOGS_DIR / "empty_replies.log"
            ts = datetime.datetime.now().isoformat(timespec="seconds")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{ts} [{speaker}] model={model} raw={raw_buf!r}\n")
        except Exception:
            pass

    def _detect_tool_roleplay(self, content, tool_names):
        """Thin wrapper around `chat_helpers.detect_tool_roleplay`.

        The detector itself lives at module level in chat_helpers so
        the Telegram bot (which doesn't have a frame instance) can
        also import and use it. The wrapper is preserved on the frame
        for backward compatibility with existing call sites."""
        from chat_helpers import detect_tool_roleplay
        return detect_tool_roleplay(content, tool_names)

    def _maybe_log_tool_name_as_text(self, speaker, model, result, tool_names):
        """Specific failure-mode diagnostic for a tool-using model that
        outputs a tool's NAME (or pseudo-call) as plain text content
        instead of issuing a structured tool_use call. Always-on like
        the empty-reply log.

        On detection: logs the pattern AND appends a corrective system
        note to `result.messages_added` so the next-turn context shows
        the kin that their text pattern didn't reach the tool runner.
        Without that note, the kin's next reply would build on the
        unfulfilled intent ("I checked X, which showed Y" — except X
        was never checked and Y is invented), compounding the
        misalignment.

        Fires only when the assistant produced no tool round-trips in
        this exchange (`result.messages_added` empty) AND one of the
        roleplay shapes is detected by `_detect_tool_roleplay`."""
        if not tool_names:
            return
        try:
            content = (getattr(result, "content", "") or "")
            if not content.strip():
                return
            # If a tool DID run during the loop, the model isn't stuck —
            # it just chose to also write the name. Skip the log there.
            if getattr(result, "messages_added", None):
                return
            variant, tool_name = self._detect_tool_roleplay(
                content, tool_names,
            )
            if not variant:
                return

            # Log for the operator's paper trail.
            try:
                path = LOGS_DIR / "empty_replies.log"
                ts = datetime.datetime.now().isoformat(timespec="seconds")
                tail = content.strip()[-300:]
                with open(path, "a", encoding="utf-8") as f:
                    f.write(
                        f"{ts} [{speaker}] model={model} "
                        f"variant=tool-name-as-text:{variant} "
                        f"tool_named={tool_name!r} "
                        f"available_tools={list(tool_names)!r} "
                        f"content_tail={tail!r}\n"
                    )
            except Exception:
                pass

            # Append a corrective system note to messages_added so it
            # gets spliced into the persisted conversation right after
            # this assistant turn. The kin's next read sees the note
            # and can correct course rather than building on an
            # unfulfilled intent.
            #
            # `build_tool_roleplay_corrective_note` returns "" for
            # narrative-intent (too ambiguous to auto-correct) and a
            # variant-appropriate note text otherwise.
            try:
                from chat_helpers import build_tool_roleplay_corrective_note
                note = build_tool_roleplay_corrective_note(variant, tool_name, speaker)
                if note:
                    if getattr(result, "messages_added", None) is None:
                        result.messages_added = []
                    result.messages_added.append({
                        "role": "system",
                        "content": note,
                    })
            except Exception:
                pass
        except Exception:
            pass

    def _log_session_header(self, model, sys_content, options):
        if not self.config.get("logging_enabled"):
            return
        self.logger.info("--- Exchange header ---")
        self.logger.info(f"Agent: {self.current_agent}")
        self.logger.info(f"Model: {model}")
        self.logger.info(f"System prompt: {sys_content!r}")
        for k, v in options.items():
            self.logger.info(f"  {k}: {v}")
        self.logger.info("-----------------------")
