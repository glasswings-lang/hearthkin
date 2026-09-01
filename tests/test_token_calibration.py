"""Token-calibration ratchet tests.

The truncation budget is enforced in ESTIMATED tokens divided by a
per-kin real/estimate ratio learned from the provider's reported prompt
token count. When a prompt overflows the context window the provider
clamps it, and the number it reports back is the window size rather than
the size of what was actually sent — a censored measurement.

Treating that censored number as a clean sample can pull the ratio DOWN,
which shrinks the truncation margin immediately after an overflow and
makes the next call overflow too. These tests pin the ratchet that stops
that feedback loop.

Run: python tests/test_token_calibration.py
"""
import json
import os
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# BEFORE importing anything that reaches kin_persistence. This file feeds a
# deliberately-corrupt JSON file to the loader to prove it's ignored, and the
# loader logs that failure to logs/save_failures.log — one of the always-on
# diagnostic logs. Unsandboxed, every run of this test appended a synthetic
# failure to the REAL one, making it useless for spotting a genuine save
# problem. tests/run_all.py sets this for the whole suite; setting it here too
# keeps a standalone `python tests/test_token_calibration.py` just as clean.
os.environ.setdefault(
    "HEARTHKIN_HOME",
    tempfile.mkdtemp(prefix="hearthkin-caltest-"))

import llm_backend as lb


failures = []


def check(label, cond):
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        failures.append(label)


def reset(kin, ratio=None):
    lb._token_calibration.pop(kin, None)
    lb._last_prompt_tokens.pop(kin, None)
    lb._calibration_loaded.discard(kin)
    lb._calibration_on_disk.pop(kin, None)
    if ratio is not None:
        lb._token_calibration[kin] = ratio
        lb._calibration_loaded.add(kin)


def test_cap_hit_never_lowers_ratio():
    """The regression this file exists for. A kin sitting at a healthy
    ratio hits the context wall; the clamped report implies a LOWER
    ratio than the one already learned. The ratio must not drop."""
    kin = "cal-test-nolower"
    reset(kin, ratio=1.9)
    # num_ctx 32768, prompt clamped to 32767, estimate 20000 =>
    # measured ~1.64, below the current 1.9.
    lb._update_token_calibration(
        kin, 20000, {"prompt_tokens": 32767}, num_ctx=32768)
    after = lb._token_calibration[kin]
    check("cap-hit does not lower the ratio", after >= 1.9)
    check("cap-hit raises the ratio by a step", after > 1.9)


def test_cap_hit_still_accepts_a_higher_measurement():
    """A cap-hit whose implied ratio is ABOVE the current one should
    still move the ratio up — the ratchet is a floor, not a freeze."""
    kin = "cal-test-higher"
    reset(kin, ratio=1.2)
    lb._update_token_calibration(
        kin, 12000, {"prompt_tokens": 32767}, num_ctx=32768)
    check("higher cap-hit measurement is adopted",
          lb._token_calibration[kin] > 1.2)


def test_normal_call_still_lowers_ratio():
    """Ordinary (non-cap-hit) calls must keep converging in both
    directions — the ratchet applies only at the wall."""
    kin = "cal-test-normal"
    reset(kin, ratio=2.5)
    lb._update_token_calibration(
        kin, 10000, {"prompt_tokens": 12000}, num_ctx=32768)
    check("normal call may lower the ratio",
          lb._token_calibration[kin] < 2.5)


def test_implausible_cap_hit_still_ratchets():
    """An out-of-range derived ratio is normally discarded. On a cap-hit
    the overflow itself is still information, so the ratio must move."""
    kin = "cal-test-implausible"
    reset(kin, ratio=2.0)
    # est_sent tiny => measured ~32.8, far outside the 0.8-5.0 band.
    lb._update_token_calibration(
        kin, 1000, {"prompt_tokens": 32767}, num_ctx=32768)
    check("implausible cap-hit still raises the ratio",
          lb._token_calibration[kin] > 2.0)


def test_ratio_is_bounded():
    """Repeated cap-hits must not ratchet without limit."""
    kin = "cal-test-bounded"
    reset(kin, ratio=4.9)
    for _ in range(20):
        lb._update_token_calibration(
            kin, 8000, {"prompt_tokens": 32767}, num_ctx=32768)
    check("ratio stays within the plausibility ceiling",
          lb._token_calibration[kin] <= lb._MAX_TOKEN_RATIO)


def test_no_num_ctx_means_no_cap_hit():
    """Callers that don't know num_ctx must not trigger the ratchet."""
    kin = "cal-test-nonumctx"
    reset(kin, ratio=2.5)
    lb._update_token_calibration(kin, 10000, {"prompt_tokens": 12000})
    check("absent num_ctx behaves as an ordinary sample",
          lb._token_calibration[kin] < 2.5)


class temp_calibration_store:
    """Redirect calibration files into a scratch directory.

    The real path resolves inside the kin folder; tests must never write
    there."""

    def __enter__(self):
        self._dir = tempfile.mkdtemp(prefix="hk-calibration-")
        self._orig = lb._calibration_path
        lb._calibration_path = (
            lambda kin: pathlib.Path(self._dir) / f"{kin}.json")
        return self

    def __exit__(self, *_exc):
        lb._calibration_path = self._orig
        shutil.rmtree(self._dir, ignore_errors=True)
        return False


def simulate_restart(kin):
    """Drop everything a process holds in memory, keeping only disk."""
    lb._token_calibration.pop(kin, None)
    lb._calibration_loaded.discard(kin)
    lb._calibration_on_disk.pop(kin, None)


def test_ratio_survives_a_process_restart():
    """The reason this exists: scheduled wake-ups are separate
    short-lived processes, so a ratio held only in memory is never
    learned by the runs that need it most."""
    kin = "cal-test-persist"
    with temp_calibration_store():
        reset(kin)
        lb._update_token_calibration(
            kin, 10000, {"prompt_tokens": 18000}, num_ctx=32768)
        learned = lb._token_calibration[kin]
        simulate_restart(kin)
        check("a fresh process inherits the stored ratio",
              abs(lb.token_calibration_ratio(kin) - learned) < 0.01)


def test_live_measurement_beats_stored_value():
    """Anything measured in this process is newer than the file."""
    kin = "cal-test-livewins"
    with temp_calibration_store():
        reset(kin)
        lb._update_token_calibration(
            kin, 10000, {"prompt_tokens": 18000}, num_ctx=32768)
        lb._calibration_loaded.discard(kin)
        lb._token_calibration[kin] = 2.7
        lb._load_calibration(kin)
        check("in-process ratio is not overwritten by the file",
              lb._token_calibration[kin] == 2.7)


def test_corrupt_stored_ratio_is_ignored():
    """A junk file costs one recalibration, never a crash."""
    kin = "cal-test-corrupt"
    with temp_calibration_store():
        reset(kin)
        lb._calibration_path(kin).write_text(
            json.dumps({"token_ratio": 99.0}), encoding="utf-8")
        lb._load_calibration(kin)
        check("out-of-range stored ratio is discarded",
              kin not in lb._token_calibration)

        reset(kin)
        lb._calibration_path(kin).write_text("{not json", encoding="utf-8")
        lb._load_calibration(kin)
        check("unparseable stored ratio is discarded",
              kin not in lb._token_calibration)


def test_cap_hit_is_always_written():
    """An overflow may move the ratio less than the write threshold, but
    it is the one lesson a fresh process most needs to inherit."""
    kin = "cal-test-capwrite"
    with temp_calibration_store():
        reset(kin, ratio=1.9)
        lb._calibration_on_disk[kin] = 1.9
        lb._update_token_calibration(
            kin, 20000, {"prompt_tokens": 32767}, num_ctx=32768)
        check("cap-hit is persisted", lb._calibration_path(kin).exists())


def main():
    print("token calibration")
    test_cap_hit_never_lowers_ratio()
    test_cap_hit_still_accepts_a_higher_measurement()
    test_normal_call_still_lowers_ratio()
    test_implausible_cap_hit_still_ratchets()
    test_ratio_is_bounded()
    test_no_num_ctx_means_no_cap_hit()
    test_ratio_survives_a_process_restart()
    test_live_measurement_beats_stored_value()
    test_corrupt_stored_ratio_is_ignored()
    test_cap_hit_is_always_written()
    print()
    if failures:
        print(f"FAILED ({len(failures)}): " + ", ".join(failures))
        return 1
    print("all token-calibration tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
