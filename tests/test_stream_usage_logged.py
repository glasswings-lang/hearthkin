# SPDX-License-Identifier: CC0-1.0
"""Guard test: a streamed reply from an added provider reaches usage.log.

Found by noticing that a paid service was spending money no Usage window
could see. OpenAI's shape only reports token use on a STREAMED reply when the
request asks for it (`stream_options.include_usage`). OpenRouter always sends
it anyway, so nothing here ever asked, and every streamed reply from an added
provider — Telegram and the desktop both stream — came back with no usage and
left no line in usage.log. Checked against Ollama's cloud before the fix:
without the request the stream carries no usage at all; with it, the last
event does.

The fake server below behaves exactly like that: it reports usage only when
asked. So the end-to-end check fails on the old request and passes on the new.

Pinned:
  - a streamed request to an added provider asks for usage;
  - a non-streamed one doesn't need to, and OpenRouter's request is unchanged;
  - end to end, a streamed chat leaves a usage.log line with the kin, the
    model, the surface and the real token counts.

Run: python tests/test_stream_usage_logged.py
"""

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ["HEARTHKIN_HOME"] = tempfile.mkdtemp(
    prefix="hk-streamusage-", dir=(os.environ.get("HEARTHKIN_HOME") or None))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import llm_backend as lb  # noqa: E402
import kin_persistence    # noqa: E402

_failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _failures.append(name)


_real = lb.api_providers
MSGS = [{"role": "user", "content": "hi"}]


def _payload(model, stream):
    return lb._build_openrouter_payload(model, MSGS, {}, "off", None, False, stream)


def test_request_shape(base):
    def provs():
        p = _real()
        p["plainstream"] = {"base": base, "label": "plainstream"}
        return p
    lb.api_providers = provs
    try:
        p = _payload("plainstream/m", True)
        check("a streamed request to an added provider asks for usage",
              p.get("stream_options") == {"include_usage": True})
        check("a non-streamed request doesn't carry it",
              "stream_options" not in _payload("plainstream/m", False))
        check("OpenRouter's request is unchanged (it sends usage anyway)",
              "stream_options" not in _payload("openrouter/x/y", True))
    finally:
        lb.api_providers = _real


def test_end_to_end(base, seen):
    def provs():
        p = _real()
        p["plainstream"] = {"base": base, "label": "plainstream"}
        return p
    lb.api_providers = provs
    os.environ["PLAINSTREAM_API_KEY"] = "not-needed"
    log = kin_persistence.LOGS_DIR / "usage.log"
    try:
        text = ""
        for chunk in lb.chat("plainstream/test-model", list(MSGS), stream=True,
                             think_effort="off", kin_name="TestKin",
                             surface="telegram-dm"):
            text += getattr(chunk, "content", "") or ""
            if getattr(chunk, "done", False):
                break
        check("the reply streamed through", text == "Hello there.")
        check("the server was asked for usage",
              seen and seen[-1].get("stream_options") == {"include_usage": True})
        lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        mine = [l for l in lines if "kin=TestKin" in l]
        check("usage.log has a line for it", len(mine) == 1)
        line = mine[0] if mine else ""
        check("...naming the model and surface",
              "model=plainstream/test-model" in line and "surface=telegram-dm" in line)
        check("...with the real token counts", "in=7" in line and "out=3" in line)
    finally:
        lb.api_providers = _real
        os.environ.pop("PLAINSTREAM_API_KEY", None)


def main():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body)
            events = [
                {"choices": [{"index": 0, "delta": {"content": "Hello "}}]},
                {"choices": [{"index": 0, "delta": {"content": "there."},
                              "finish_reason": "stop"}]},
            ]
            # Like Ollama's cloud: usage only when the request asks for it.
            if (body.get("stream_options") or {}).get("include_usage"):
                events.append({"choices": [], "usage": {
                    "prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}})
            out = "".join("data: " + json.dumps(e) + "\n\n" for e in events)
            out = (out + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d/v1" % srv.server_address[1]
    try:
        test_request_shape(base)
        test_end_to_end(base, seen)
    finally:
        srv.shutdown()
    if _failures:
        print("\nFAILED %d: %s" % (len(_failures), ", ".join(_failures)))
        return 1
    print("\ntest_stream_usage_logged: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
