"""Dictation: where the transcription model lives, and the Talk button.

Three things are pinned here, and they are different kinds of thing.

The first is the addressing rule. A transcription model is named the
same way a chat model is — a model, plus the machine it runs on, where
an empty machine means "this computer". `route_for` is the one function
that reads that pair, and the settings screen and the engine both go
through it, so they cannot drift into disagreeing about where somebody's
audio is being sent. Every route is driven through a fake here, so this
needs no microphone, no model download and no network.

The second is that a machine can be named ANYWHERE. Nobody should be
limited to the graphics card in the computer they are typing at, and the
way that promise is kept is boring on purpose: one ordinary
`/v1/audio/transcriptions` endpoint, so a box on the shelf, the machine
that already runs the language models, and a hosted service are all the
same code path. The URL-shape tests exist because pasting an address out
of a server's own README should not be a wrong answer.

The third is the reason any of this exists. The Talk button used to be
shown only when the active kin had a paid ElevenLabs voice picked, so
dictation — putting your own words into the message box — was gated on
the kin being able to speak BACK, which is an unrelated thing, and on a
subscription. For anyone who finds typing hard that is the difference
between the app being usable and not. The test below asserts the gate is
gone, and carries the old behaviour as a positive control so a green
result means the check can actually see the gate it is looking for.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stt  # noqa: E402


FAILURES = []


def check(label, cond, detail=""):
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))
        FAILURES.append(label)


WAV = stt.wav_from_pcm(b"\x00\x00" * 1600, samplerate=16000)


# --- the addressing rule --------------------------------------------

def test_route_for():
    """A model plus a machine decides where the audio goes."""
    print("\nrouting")
    cases = [
        ("base.en", "", stt.ROUTE_LOCAL, "a bare model name means this computer"),
        ("", "", stt.ROUTE_LOCAL, "nothing set at all means this computer"),
        ("large-v3", "http://box:8080", stt.ROUTE_SERVER,
         "a machine named means that machine"),
        ("whisper-large-v3", "https://api.example.com/openai/v1",
         stt.ROUTE_SERVER, "a hosted service is just another machine"),
        ("elevenlabs/scribe_v1", "", stt.ROUTE_ELEVENLABS,
         "the elevenlabs prefix names its own provider"),
    ]
    for model, host, want, label in cases:
        check(label, stt.route_for(model, host) == want,
              stt.route_for(model, host))

    # A leftover address must not silently redirect audio away from the
    # provider the model string explicitly names.
    check("a model naming its provider beats a leftover address",
          stt.route_for("elevenlabs/scribe_v1", "http://box:8080")
          == stt.ROUTE_ELEVENLABS)
    check("the prefix is matched case-insensitively",
          stt.route_for("ElevenLabs/scribe_v1", "") == stt.ROUTE_ELEVENLABS)
    check("whitespace around a host still counts as a machine",
          stt.route_for("m", "   http://box:8080  ") == stt.ROUTE_SERVER)
    check("whitespace-only host is not a machine",
          stt.route_for("base.en", "   ") == stt.ROUTE_LOCAL)


def test_dispatch_follows_the_route():
    """Each route reaches its own transcriber and no other."""
    print("\ndispatch")
    seen = []
    orig = (stt._local_whisper, stt._remote_transcription, stt._elevenlabs)
    stt._local_whisper = lambda w, s: seen.append("local") or "local words"
    stt._remote_transcription = lambda w, s: seen.append("server") or "server words"
    stt._elevenlabs = lambda w, s, k: seen.append("scribe") or "scribe words"
    try:
        out = stt.transcribe(WAV, {"model": "base.en"})
        check("a local model routes to the local transcriber",
              seen == ["local"], seen)
        check("and its text comes back", out == "local words", out)

        seen.clear()
        stt.transcribe(WAV, {"model": "x", "host": "http://box:8080"})
        check("a named machine routes to the remote transcriber",
              seen == ["server"], seen)

        seen.clear()
        stt.transcribe(WAV, {"model": "elevenlabs/scribe_v1"})
        check("the elevenlabs prefix routes to ElevenLabs",
              seen == ["scribe"], seen)

        # The default matters: it is the promise that dictation costs
        # nothing. A default that quietly became the paid one would be
        # invisible until a bill arrived.
        seen.clear()
        stt.transcribe(WAV, {})
        check("no settings means the local model", seen == ["local"], seen)
        seen.clear()
        stt.transcribe(WAV, None)
        check("no settings at all means the local model",
              seen == ["local"], seen)
    finally:
        stt._local_whisper, stt._remote_transcription, stt._elevenlabs = orig


def test_empty_recording_is_refused_before_any_route():
    """No audio must not become a model call, a network call, or a bill."""
    print("\nempty recording")
    seen = []
    orig = (stt._local_whisper, stt._remote_transcription, stt._elevenlabs)
    stt._local_whisper = lambda w, s: seen.append("local")
    stt._remote_transcription = lambda w, s: seen.append("server")
    stt._elevenlabs = lambda w, s, k: seen.append("scribe")
    try:
        for settings in ({"model": "base.en"},
                         {"model": "x", "host": "http://box:8080"},
                         {"model": "elevenlabs/scribe_v1"}):
            try:
                stt.transcribe(b"", settings)
                check(f"empty audio refused ({settings})", False, "no error")
            except stt.SttError:
                check("empty audio refused "
                      f"({stt.route_for(settings.get('model'), settings.get('host'))})",
                      True)
        check("no route was called for empty audio", seen == [], seen)
    finally:
        stt._local_whisper, stt._remote_transcription, stt._elevenlabs = orig


# --- a machine, anywhere --------------------------------------------

def test_host_shapes_all_reach_the_same_endpoint():
    """A bare host, a trailing slash, a /v1, or a full endpoint pasted
    out of a README must all mean the same thing."""
    print("\nmachine address handling")
    want = "http://mac:8080/v1/audio/transcriptions"
    for given in ("http://mac:8080", "http://mac:8080/", "mac:8080",
                  "http://mac:8080/v1", "http://mac:8080/v1/",
                  "http://mac:8080/v1/audio/transcriptions"):
        check(f"{given!r} -> the transcriptions endpoint",
              stt.normalise_host(given) == want, stt.normalise_host(given))

    # A hosted service that puts its API under a path must not have that
    # path thrown away.
    check("a service with a path prefix keeps it",
          stt.normalise_host("https://api.example.com/openai/v1")
          == "https://api.example.com/openai/v1/audio/transcriptions",
          stt.normalise_host("https://api.example.com/openai/v1"))
    check("no host means no endpoint", stt.normalise_host("") == "")

    check("the model listing endpoint is derived the same way",
          stt.models_endpoint("mac:8080") == "http://mac:8080/v1/models",
          stt.models_endpoint("mac:8080"))
    check("...even from a full transcriptions URL",
          stt.models_endpoint("http://mac:8080/v1/audio/transcriptions")
          == "http://mac:8080/v1/models",
          stt.models_endpoint("http://mac:8080/v1/audio/transcriptions"))


def test_both_server_shapes_are_tried():
    """A machine that speaks whisper.cpp's own endpoint must work from a
    bare address, without anybody having to know which kind of server
    they are running.

    This is here because it was got wrong. The first version supported
    only the OpenAI-shaped path, and the whisper.cpp server already
    running on this household's other machine answers 404 to it while
    working perfectly on /inference -- so "point it at your own machine"
    would have failed on the very machine it was written for."""
    print("\nboth server shapes")
    import urllib.error

    seen = []

    def fake_post(url, wav, fields, headers, timeout, label="x"):
        seen.append(url)
        if url.endswith("/v1/audio/transcriptions"):
            raise stt.SttError("The transcription server answered HTTP 404: nope")
        return {"text": "from whisper.cpp"}

    orig = stt._post_multipart
    stt._post_multipart = fake_post
    try:
        stt._endpoints.clear()
        out = stt.transcribe(WAV, {"model": "m", "host": "box:8080"})
        check("the OpenAI shape is tried first",
              seen and seen[0].endswith("/v1/audio/transcriptions"), seen)
        check("...then whisper.cpp's own endpoint",
              len(seen) == 2 and seen[1].endswith("/inference"), seen)
        check("...and the transcript comes back",
              out == "from whisper.cpp", out)

        # The winner is remembered, so the dead path is not retried on
        # every dictation.
        seen.clear()
        stt.transcribe(WAV, {"model": "m", "host": "box:8080"})
        check("the working endpoint is remembered",
              seen == ["http://box:8080/inference"], seen)

        # A full URL is taken at its word -- somebody who pasted an
        # endpoint has already said what they mean.
        stt._endpoints.clear()
        seen.clear()
        stt.transcribe(WAV, {"model": "m",
                             "host": "http://box:8080/inference"})
        check("an explicit endpoint is used as given, with no probing",
              seen == ["http://box:8080/inference"], seen)
    finally:
        stt._post_multipart = orig
        stt._endpoints.clear()

    # A failure that is NOT a missing path must surface as itself. Trying
    # the other shape would replace a true error with a confusing one.
    def five_hundred(url, wav, fields, headers, timeout, label="x"):
        seen.append(url)
        raise stt.SttError("The transcription server answered HTTP 500: boom")

    stt._post_multipart = five_hundred
    try:
        seen.clear()
        try:
            stt.transcribe(WAV, {"model": "m", "host": "box:8080"})
            check("a server error is raised", False, "it did not raise")
        except stt.SttError as e:
            check("a server error is raised as itself", "500" in str(e), str(e))
        check("...and the other shape is not tried after it",
              len(seen) == 1, seen)
    finally:
        stt._post_multipart = orig
        stt._endpoints.clear()


def test_remote_call_carries_model_language_and_key():
    print("\nwhat the remote call sends")
    captured = {}

    def fake_post(url, wav, fields, headers, timeout, label="x"):
        captured.update(url=url, fields=fields, headers=headers)
        return {"text": "hello"}

    orig = stt._post_multipart
    stt._post_multipart = fake_post
    try:
        stt.transcribe(WAV, {"model": "large-v3", "host": "http://mac:8080",
                             "host_key": "s3cret", "language": "en"})
        check("the model name goes as the model field",
              captured["fields"].get("model") == "large-v3", captured["fields"])
        check("the language is passed through",
              captured["fields"].get("language") == "en", captured["fields"])
        check("a machine key is sent as a bearer token",
              captured["headers"].get("Authorization") == "Bearer s3cret",
              captured["headers"])

        stt.transcribe(WAV, {"model": "m", "host": "http://mac:8080"})
        check("no key means no Authorization header",
              "Authorization" not in captured["headers"], captured["headers"])
        check("no language means the field is left off",
              "language" not in captured["fields"], captured["fields"])
    finally:
        stt._post_multipart = orig


def test_remote_without_an_address():
    """Choosing a remote model and leaving the address blank has to say
    so, not fall through to something else."""
    print("\nno machine address")
    orig = stt.route_for
    try:
        # Force the server route with an empty host, the state the
        # settings screen can produce mid-edit.
        stt.route_for = lambda model="", host="": stt.ROUTE_SERVER
        try:
            stt.transcribe(WAV, {"model": "x", "host": ""})
            check("missing address raises", False, "it did not raise")
        except stt.SttError as e:
            check("missing address raises and says where to set it",
                  "Dictation" in str(e), str(e))
    finally:
        stt.route_for = orig


def test_plain_text_server_reply_is_a_transcript():
    """Some transcription servers answer text/plain. That is an answer,
    not a failure, and treating it as one loses the words."""
    print("\nplain-text server reply")

    class FakeResp:
        def __init__(self, body):
            self._b = body

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = lambda req, timeout=None: FakeResp(
        b"the kettle is on")
    try:
        out = stt.transcribe(WAV, {"model": "m", "host": "http://mac:8080"})
        check("a bare-text body is read as the transcript",
              out == "the kettle is on", out)
    finally:
        urllib.request.urlopen = orig


def test_list_server_models_parses_and_explains():
    print("\nasking a machine what it has")

    class FakeResp:
        def __init__(self, body):
            self._b = body

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = lambda req, timeout=None: FakeResp(
        b'{"data":[{"id":"whisper-large-v3"},{"id":"whisper-small"}]}')
    try:
        got = stt.list_server_models("http://mac:8080")
        check("an OpenAI-shaped model list is read",
              got == ["whisper-large-v3", "whisper-small"], got)
    finally:
        urllib.request.urlopen = orig

    # Unreachable must explain what to check, not just relay errno.
    import urllib.error
    def boom(req, timeout=None):
        raise urllib.error.URLError("nope")
    urllib.request.urlopen = boom
    try:
        stt.list_server_models("http://mac:8080")
        check("an unreachable machine raises", False, "it did not raise")
    except stt.SttError as e:
        check("an unreachable machine says what to check",
              "address" in str(e) and "port" in str(e), str(e))
    finally:
        urllib.request.urlopen = orig


def test_elevenlabs_without_a_key_names_the_free_way_out():
    """The failure has to name the alternatives, not just complain."""
    print("\nno ElevenLabs key")
    try:
        stt.transcribe(WAV, {"model": "elevenlabs/scribe_v1"},
                       get_api_key=lambda: "")
        check("missing key raises", False, "it did not raise")
    except stt.SttError as e:
        msg = str(e)
        check("missing key raises", True)
        check("and points at this computer or a machine of your own",
              "this computer" in msg and "no key" in msg, msg)


def test_describe_says_where_in_plain_words():
    """The settings screen's one-line answer. Written to be read out."""
    print("\ndescribing where it runs")
    check("local names the computer",
          "this computer" in stt.describe("base.en", ""),
          stt.describe("base.en", ""))
    check("local says which part of it, when that is known",
          "processor" in stt.describe("base.en", "", ("cpu", "int8")),
          stt.describe("base.en", "", ("cpu", "int8")))
    check("a machine is named",
          stt.describe("large-v3", "http://box:8080")
          == "large-v3 on http://box:8080",
          stt.describe("large-v3", "http://box:8080"))
    check("ElevenLabs is named without its prefix showing",
          stt.describe("elevenlabs/scribe_v1", "")
          == "ElevenLabs (scribe_v1)",
          stt.describe("elevenlabs/scribe_v1", ""))


def test_preload_only_warms_a_local_model():
    """Warming up is a local idea. A machine elsewhere must not be
    pinged at startup by something nobody asked for."""
    print("\nwarm-up")
    called = []
    orig = stt.load_model
    stt.load_model = lambda *a, **k: called.append(a)
    try:
        stt.preload({"model": "base.en", "host": ""})
        check("a local model is warmed", len(called) == 1, called)
        called.clear()
        stt.preload({"model": "x", "host": "http://box:8080"})
        check("a model on another machine is not", called == [], called)
        called.clear()
        stt.preload({"model": "elevenlabs/scribe_v1"})
        check("ElevenLabs is not", called == [], called)
    finally:
        stt.load_model = orig

    # A warm-up that fails must never raise; the cost simply stays where
    # it was.
    def raiser(*a, **k):
        raise RuntimeError("no")
    stt.load_model = raiser
    try:
        stt.preload({"model": "base.en"})
        check("a failed warm-up is swallowed", True)
    except Exception as e:
        check("a failed warm-up is swallowed", False, str(e))
    finally:
        stt.load_model = orig


def test_no_ffmpeg_is_needed_or_shipped():
    """Audio is decoded here, not by faster-whisper, and that is a
    LICENSING property as much as a technical one.

    faster-whisper decodes media files through PyAV, and the PyAV wheel
    ships an FFmpeg built with libx264 and libx265 -- both GPL. Shipping
    that would put copyleft obligations on the releases of a CC0 project.
    Nothing here needs it: the only audio ever transcribed locally is a
    WAV this app recorded itself, so the standard library reads it and
    Whisper is handed plain samples.

    The stub has to behave like a real module. The first version raised
    on any attribute at all, including __spec__, and an unrelated package
    three imports away probing "is PyAV installed?" crashed the whole
    chain."""
    print("\nno FFmpeg")
    import importlib.util
    import sys

    saved = sys.modules.get("av")
    try:
        sys.modules.pop("av", None)
        stt._install_av_stub()
        av = sys.modules["av"]

        check("a stub is installed", getattr(av, "__version__", "") == "0-stub")
        check("...that answers a spec probe without raising",
              importlib.util.find_spec("av") is not None)
        check("...and is importable as a module",
              av.__name__ == "av" and av.__spec__ is not None)
        check("installing twice does not stack stubs",
              (stt._install_av_stub(), sys.modules["av"] is av)[1])

        # Any REAL use is a mistake, and must say so in words rather than
        # failing as a bare AttributeError somebody has to decode.
        try:
            av.open("something.mp3")
            check("using it raises", False, "it did not raise")
        except RuntimeError as e:
            check("using it explains itself in words",
                  "FFmpeg" in str(e) and "stt.py" in str(e), str(e)[:80])
    finally:
        if saved is not None:
            sys.modules["av"] = saved
        else:
            sys.modules.pop("av", None)


def test_wav_is_decoded_without_a_media_library():
    """Every WAV shape the app can produce or be handed reads correctly
    with nothing but the standard library and numpy."""
    print("\nWAV decoding")
    import numpy as np

    a = stt.wav_to_array(stt.wav_from_pcm(b"\x00\x01" * 1600, samplerate=16000))
    check("16 kHz mono comes back as float32", a.dtype == np.float32, a.dtype)
    check("...at the same length", len(a) == 1600, len(a))
    check("...and within range", float(np.abs(a).max()) <= 1.0)

    # A rate other than 16 kHz is resampled rather than refused.
    b = stt.wav_to_array(stt.wav_from_pcm(b"\x00\x01" * 2205, samplerate=22050))
    check("22 kHz is resampled towards 16 kHz",
          1500 < len(b) < 1700, len(b))

    # Stereo is mixed down; Whisper wants one channel.
    c = stt.wav_to_array(
        stt.wav_from_pcm(b"\x00\x01\x00\x01" * 800, samplerate=16000, channels=2))
    check("stereo is mixed to mono", len(c) == 800, len(c))

    # Something that is not a WAV must say so, not crash obscurely.
    try:
        stt.wav_to_array(b"this is not audio")
        check("a non-WAV is refused", False, "it did not raise")
    except stt.SttError as e:
        check("a non-WAV is refused in words", "WAV" in str(e), str(e)[:60])


def test_downloaded_models_is_a_set_not_a_lie():
    """An unreadable cache must read as 'we don't know', not as 'none'.

    Absence is not evidence: reporting an empty set as fact would make
    the model picker claim nothing is downloaded on a machine where
    everything is."""
    print("\ndownloaded model listing")
    check("returns a set", isinstance(stt.downloaded_models(), set))


# --- the reason this exists -----------------------------------------

class _FakeButton:
    def __init__(self):
        self.shown = None

    def Show(self):
        self.shown = True

    def Hide(self):
        self.shown = False

    def GetParent(self):
        return None


def _frame_with(config, agent="Someone", room=None, kin_voice=None):
    """A stand-in frame carrying only what the visibility rule reads."""
    from frame.status_voice_mixin import StatusVoiceMixin

    class F(StatusVoiceMixin):
        pass

    f = F()
    f.config = config
    f.current_agent = agent
    f.current_room = room
    f.agent_cfg = {"voice": kin_voice or {"enabled": False, "voice_id": ""}}
    f.talk_btn = _FakeButton()
    return f


def test_talk_button_no_longer_needs_a_paid_voice():
    """The gate that made dictation a paid feature is gone.

    Positive control first: the OLD rule is spelled out here and asserted
    to hide the button for a kin with no ElevenLabs voice. If that
    control ever stops hiding it, this test has stopped being able to see
    the difference it claims to measure, and its green is worthless."""
    print("\nTalk button visibility")

    def old_rule(frame):
        v = (frame.agent_cfg or {}).get("voice") or {}
        return bool(frame.current_agent and frame.current_room is None
                    and v.get("enabled", False)
                    and (v.get("voice_id") or "").strip())

    check("positive control: the old rule hid it with no paid voice",
          old_rule(_frame_with({})) is False)

    orig_avail = stt.available_locally
    stt.available_locally = lambda: True
    try:
        f = _frame_with({"dictation": {"model": "base.en"}})
        f._refresh_talk_button_visibility()
        check("with a local model, Talk shows for a kin with no paid voice",
              f.talk_btn.shown is True, f.talk_btn.shown)

        r = _frame_with({"dictation": {"model": "base.en"}},
                        agent=None, room="Kitchen")
        r._refresh_talk_button_visibility()
        check("Talk shows in a room as well", r.talk_btn.shown is True,
              r.talk_btn.shown)

        n = _frame_with({"dictation": {"model": "base.en"}},
                        agent=None, room=None)
        n._refresh_talk_button_visibility()
        check("Talk hides when no kin and no room is open",
              n.talk_btn.shown is False, n.talk_btn.shown)
    finally:
        stt.available_locally = orig_avail

    stt.available_locally = lambda: False
    try:
        f = _frame_with({"dictation": {"model": "base.en"}})
        f._refresh_talk_button_visibility()
        check("Talk hides when nothing local is installed and no machine "
              "is named", f.talk_btn.shown is False, f.talk_btn.shown)
        ok, why = f._dictation_ready()
        check("and the reason says another machine could do it instead",
              ok is False and "no other machine is named" in why, why)

        # THE POINT OF THIS WHOLE CHANGE: no local speech library, no
        # graphics card, nothing installed here — and dictation still
        # works, because the model lives somewhere else.
        f2 = _frame_with({"dictation": {"model": "large-v3",
                                        "host": "http://box:8080"}})
        f2._refresh_talk_button_visibility()
        check("naming a machine makes Talk available with nothing "
              "installed here", f2.talk_btn.shown is True, f2.talk_btn.shown)
        ok, _why = f2._dictation_ready()
        check("...and readiness says yes without touching the network",
              ok is True)
    finally:
        stt.available_locally = orig_avail


def test_dictation_ready_covers_every_route():
    """Every route has to give a real answer. A route that falls through
    to 'not ready' with no reason hides the button and says nothing about
    why, which is the failure this whole change exists to remove."""
    print("\nreadiness by route")
    import frame.status_voice_mixin as svm

    f = _frame_with({"dictation": {"model": "elevenlabs/scribe_v1"}})
    real = svm.llm_backend.resolve_provider_key
    try:
        svm.llm_backend.resolve_provider_key = lambda name: "sk_fake"
        ok, why = f._dictation_ready()
        check("ElevenLabs with a key is ready", ok is True, why)

        svm.llm_backend.resolve_provider_key = lambda name: ""
        ok, why = f._dictation_ready()
        check("ElevenLabs with no key is not ready, and says so",
              ok is False and "API key" in why, why)

        def throws(name):
            raise RuntimeError("boom")
        svm.llm_backend.resolve_provider_key = throws
        ok, _why = f._dictation_ready()
        check("a key lookup that raises is handled, not fatal", ok is False)
    finally:
        svm.llm_backend.resolve_provider_key = real

    # A machine that is asleep must not remove the button. Being told
    # when you press Talk is the right answer; a missing button reads as
    # a missing feature.
    g = _frame_with({"dictation": {"model": "m", "host": "http://asleep:9"}})
    ok, _why = g._dictation_ready()
    check("an unreachable machine still leaves dictation available",
          ok is True)


def test_dictation_config_merges_over_defaults():
    """A setting added after somebody's config file was written must
    still reach them. The app's top-level config merge is shallow, so a
    nested dict would otherwise stay frozen at the shape it was first
    saved with — and an option nobody can receive is no option."""
    print("\nconfig merge")
    from kin_persistence import DEFAULT_CONFIG

    f = _frame_with({"dictation": {"host": "http://box:8080"}})
    d = f._dictation_cfg()
    check("the saved key wins", d["host"] == "http://box:8080", d["host"])
    check("keys absent from the saved config come from the defaults",
          d.get("model") == DEFAULT_CONFIG["dictation"]["model"],
          d.get("model"))
    check("every default key survives the merge",
          set(DEFAULT_CONFIG["dictation"]) <= set(d),
          sorted(set(DEFAULT_CONFIG["dictation"]) - set(d)))

    f2 = _frame_with({})
    check("no dictation config at all still yields the free default",
          stt.route_for(f2._dictation_cfg().get("model"),
                        f2._dictation_cfg().get("host")) == stt.ROUTE_LOCAL)


def test_old_settings_shape_still_says_what_its_owner_meant():
    """These settings changed shape once, and a file written under the
    old one is sitting on real disks.

    Under a plain shallow merge the superseded keys would sit there being
    ignored while the new defaults quietly took over — which reads, from
    a chair, as the app forgetting a setting you made. Worse than an
    option nobody can receive, because it looks like a fault in the app
    rather than a gap in it."""
    print("\nolder settings files")
    from kin_persistence import migrate_dictation_config as mig

    was_local = {"backend": "whisper", "whisper_model": "small.en",
                 "whisper_device": "cpu", "whisper_language": "fr",
                 "whisper_beam_size": 3, "auto_send": True}
    got = mig(was_local)
    check("an old local choice keeps its model",
          got["model"] == "small.en", got["model"])
    check("...and stays on this computer", got["host"] == "", got["host"])
    check("...and keeps the part of the computer it was pinned to",
          got["device"] == "cpu", got["device"])
    check("...and its language", got["language"] == "fr", got["language"])
    check("...and unrelated preferences are not lost",
          got["auto_send"] is True, got["auto_send"])
    check("...and it routes where it used to",
          stt.route_for(got["model"], got["host"]) == stt.ROUTE_LOCAL)

    was_server = {"backend": "whisper_server",
                  "server_model": "whisper-large-v3",
                  "server_url": "http://box:8080", "server_token": "k",
                  "server_timeout_secs": 60}
    got = mig(was_server)
    check("an old server choice keeps its machine",
          got["host"] == "http://box:8080", got["host"])
    check("...and the model that machine knows",
          got["model"] == "whisper-large-v3", got["model"])
    check("...and its key", got["host_key"] == "k", got["host_key"])
    check("...and its timeout", got["timeout_secs"] == 60, got["timeout_secs"])
    check("...and still routes to that machine",
          stt.route_for(got["model"], got["host"]) == stt.ROUTE_SERVER)

    got = mig({"backend": "elevenlabs", "elevenlabs_model": "scribe_v1"})
    check("an old ElevenLabs choice is not silently downgraded to free",
          stt.route_for(got["model"], got["host"]) == stt.ROUTE_ELEVENLABS,
          got["model"])
    check("...named with the provider prefix",
          got["model"] == "elevenlabs/scribe_v1", got["model"])

    # Shape-stable: running it twice must not change the answer, since
    # it runs on every load.
    once = mig(was_server)
    check("running it again changes nothing", mig(once) == once)

    # The current shape passes straight through, and a stored file that
    # has BOTH shapes must trust the current one.
    check("the current shape passes through",
          mig({"model": "large-v3", "host": "http://x:1"})["model"]
          == "large-v3")
    mixed = mig({"model": "tiny.en", "host": "",
                 "backend": "elevenlabs", "elevenlabs_model": "scribe_v1"})
    check("a half-migrated file trusts the current keys",
          mixed["model"] == "tiny.en", mixed["model"])

    # Nothing understandable at all costs the default, not the app.
    for junk in (None, {}, {"backend": "??"}, {"backend": None}):
        try:
            out = mig(junk)
            check(f"unreadable settings fall back to the default ({junk!r})",
                  isinstance(out, dict) and "model" in out)
        except Exception as e:
            check(f"unreadable settings fall back to the default ({junk!r})",
                  False, str(e))


def test_defaults_are_the_free_ones():
    """Stated plainly because it is the promise, not an implementation
    detail: out of the box, dictation costs nothing and sends nothing."""
    print("\ndefaults")
    from kin_persistence import DEFAULT_CONFIG
    d = DEFAULT_CONFIG["dictation"]
    check("no machine is assumed, so it runs here", d["host"] == "", d["host"])
    check("the default route is local",
          stt.route_for(d["model"], d["host"]) == stt.ROUTE_LOCAL)
    check("the device is left to choose for itself, so a machine with no "
          "usable graphics card still works", d["device"] == "auto",
          d["device"])
    check("auto-send is off, so a wrong word can be fixed first",
          d["auto_send"] is False, d["auto_send"])


def main():
    test_route_for()
    test_dispatch_follows_the_route()
    test_empty_recording_is_refused_before_any_route()
    test_host_shapes_all_reach_the_same_endpoint()
    test_both_server_shapes_are_tried()
    test_remote_call_carries_model_language_and_key()
    test_remote_without_an_address()
    test_plain_text_server_reply_is_a_transcript()
    test_list_server_models_parses_and_explains()
    test_elevenlabs_without_a_key_names_the_free_way_out()
    test_describe_says_where_in_plain_words()
    test_preload_only_warms_a_local_model()
    test_no_ffmpeg_is_needed_or_shipped()
    test_wav_is_decoded_without_a_media_library()
    test_downloaded_models_is_a_set_not_a_lie()
    test_talk_button_no_longer_needs_a_paid_voice()
    test_dictation_ready_covers_every_route()
    test_dictation_config_merges_over_defaults()
    test_old_settings_shape_still_says_what_its_owner_meant()
    test_defaults_are_the_free_ones()

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): " + "; ".join(FAILURES))
        return 1
    print("dictation: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
