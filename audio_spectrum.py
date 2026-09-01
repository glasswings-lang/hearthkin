"""Spectrum ear — objective acoustic facts about a sound, from pure numpy + soundfile.

No model, no GPU, no network. Reads an audio file and reports the tonal centre,
pitch set, detuning/beating (the between-the-semitones signature), brightness,
loudness, slow movement rate, stereo width, and how the piece evolves across its
length. Deterministic, instant, free, offline — the *reliable* half of a kin's
"sound card" (an audio-LLM is trusted for feel, this is trusted for facts).

This is a lean, bundle-friendly reimplementation of the librosa-based
`audio_facts.py` so it can live directly inside Hearthkin: it drops librosa (and
its heavyweight numba/scipy chain) in favour of numpy's own FFT plus soundfile
for reading. The DSP is the same simple math librosa was doing under the hood.

Region selection is first-class: analysis can be restricted to a time window so
fade-in/out or any uninteresting stretch is excluded. All reads are windowed, so
a multi-hour file is handled without loading it into memory.

Public API (what Hearthkin calls):
    analyze_audio(path, start=None, end=None, skip_edges=None) -> dict
    format_sound_card(facts) -> str

CLI (for testing behaviour):
    python audio_spectrum.py <file> [--start S] [--end S] [--skip-edges S]
"""
import numpy as np
import soundfile as sf

_NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _midi_to_name(midi):
    m = int(round(midi))
    return f"{_NOTES[m % 12]}{m // 12 - 1}"


def _note_of(f):
    """(note name, cents off) for a frequency in Hz. Cents = how far between
    the semitones — where the microtonal detail of a tuning lives. Drone and
    just-intonation work sits in exactly this gap, so reporting only the
    nearest note name would throw away the part that matters."""
    if f <= 0:
        return ("?", 0.0)
    midi = 69.0 + 12.0 * np.log2(f / 440.0)
    nearest = round(midi)
    return _midi_to_name(nearest), float((midi - nearest) * 100.0)


def _pick_nfft(n):
    if n >= 65536:
        return 65536
    p = 1024
    while p * 2 <= n:
        p *= 2
    return max(p, 256)


def _read_mono(path, sr, start_s, dur_s):
    """Read a [start, start+dur] window and downmix to mono. Windowed read, so
    the file size doesn't matter."""
    start_frame = max(0, int(start_s * sr))
    n = max(1, int(dur_s * sr))
    y, _ = sf.read(path, start=start_frame, frames=n, dtype="float32",
                   always_2d=True)
    if y.shape[0] == 0:
        return np.zeros(1, dtype=np.float32)
    return y.mean(axis=1)


def _avg_spectrum(y, sr):
    """Averaged magnitude spectrum across overlapping windows (Hann), + the
    frequency for each bin. This is librosa.stft(...).mean(axis=time) by hand."""
    n_fft = _pick_nfft(len(y))
    if len(y) < n_fft:
        y = np.pad(y, (0, n_fft - len(y)))
    hop = n_fft // 2
    win = np.hanning(n_fft).astype(np.float32)
    acc = None
    count = 0
    for s in range(0, len(y) - n_fft + 1, hop):
        m = np.abs(np.fft.rfft(y[s:s + n_fft] * win))
        acc = m if acc is None else acc + m
        count += 1
    if count == 0:
        acc = np.abs(np.fft.rfft(y[:n_fft] * win))
        count = 1
    mag = acc / count
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    return mag, freqs


def _peaks(mag, freqs, fmin=40.0, fmax=4000.0, n=10, rel=0.08):
    band = (freqs > fmin) & (freqs < fmax)
    fm, mm = freqs[band], mag[band]
    if mm.size < 3:
        return []
    thr = mm.max() * rel
    idx = [i for i in range(1, len(mm) - 1)
           if mm[i] > mm[i - 1] and mm[i] > mm[i + 1] and mm[i] > thr]
    idx.sort(key=lambda i: -mm[i])
    return [(float(fm[i]), float(mm[i])) for i in idx[:n]]


def _centroid(mag, freqs):
    s = float(mag.sum())
    return float((freqs * mag).sum() / s) if s > 0 else 0.0


def _movement(y, sr):
    """Strongest slow amplitude pulse (Hz) — the swell/beat rate you can feel."""
    n_fft, hop = 4096, 512
    if len(y) < n_fft * 4:
        return None
    win = np.hanning(n_fft).astype(np.float32)
    env = np.array([np.abs(np.fft.rfft(y[s:s + n_fft] * win)).mean()
                    for s in range(0, len(y) - n_fft + 1, hop)], dtype=np.float32)
    if len(env) < 8:
        return None
    env = env - env.mean()
    fr = sr / hop
    E = np.abs(np.fft.rfft(env))
    ef = np.fft.rfftfreq(len(env), d=1.0 / fr)
    band = (ef > 0.05) & (ef < 20.0)
    if not band.any():
        return None
    return float(ef[band][np.argmax(E[band])])


def _stereo_corr(path, sr, start_s, dur_s):
    y, _ = sf.read(path, start=max(0, int(start_s * sr)),
                   frames=max(1, int(dur_s * sr)), dtype="float32", always_2d=True)
    if y.shape[1] < 2 or y.shape[0] < 2:
        return None
    return float(np.corrcoef(y[:, 0], y[:, 1])[0, 1])


def _resolve_region(dur, start, end, skip_edges):
    r0, r1 = 0.0, dur
    if start is not None:
        r0 = max(0.0, float(start))
    if end is not None:
        r1 = min(dur, float(end))
    if skip_edges is not None and start is None and end is None:
        se = min(float(skip_edges), dur / 2.0)
        r0, r1 = se, dur - se
    if r1 <= r0:
        r0, r1 = 0.0, dur
    return r0, r1


def analyze_audio(path, start=None, end=None, skip_edges=None):
    """Return a dict of acoustic facts for [start, end] (seconds) of the file.

    start/end select an explicit window; skip_edges (seconds) is a convenience
    that trims that many seconds off both ends — e.g. skip_edges=10 to ignore a
    10s fade-in and 10s fade-out. If nothing is given, the whole file is used.
    """
    info = sf.info(path)
    sr, dur, ch = info.samplerate, info.duration, info.channels
    r0, r1 = _resolve_region(dur, start, end, skip_edges)
    region_dur = r1 - r0

    win_len = min(30.0, region_dur)
    mid = r0 + (region_dur - win_len) / 2.0
    y = _read_mono(path, sr, mid, win_len)

    mag, freqs = _avg_spectrum(y, sr)
    peaks = _peaks(mag, freqs)
    centroid = _centroid(mag, freqs)
    rms = float(np.sqrt(np.mean(y ** 2))) if len(y) else 0.0
    root_f = min((f for f, _ in peaks[:4]), default=0.0)

    beats = []
    strong = [f for f, _ in peaks[:8]]
    for i in range(len(strong)):
        for j in range(i + 1, len(strong)):
            b = abs(strong[i] - strong[j])
            lo = min(strong[i], strong[j])
            if 0.3 < b < 12.0 and lo > 0 and b / lo < 0.06:
                name, _ = _note_of((strong[i] + strong[j]) / 2.0)
                beats.append((strong[i], strong[j], b, name))

    movement = _movement(y, sr)
    stereo = _stereo_corr(path, sr, mid, min(20.0, win_len)) if ch >= 2 else None

    evo = []
    for frac in (0.05, 0.25, 0.5, 0.75, 0.95):
        off = r0 + region_dur * frac
        yw = _read_mono(path, sr, off, min(15.0, region_dur))
        if len(yw) < 4096:
            continue
        m2, f2 = _avg_spectrum(yw, sr)
        pk = _peaks(m2, f2)
        if pk:
            rf = min(f for f, _ in pk[:4])
            name, _ = _note_of(rf)
            evo.append((off, name, rf, _centroid(m2, f2)))

    return {
        "path": path, "duration": dur, "samplerate": sr, "channels": ch,
        "region": (r0, r1), "peaks": peaks, "root_hz": root_f,
        "root_note": _note_of(root_f) if root_f > 0 else None,
        "centroid": centroid, "rms_dbfs": 20 * np.log10(rms + 1e-9),
        "beats": beats, "movement_hz": movement, "stereo_corr": stereo,
        "evolution": evo,
    }


def _fmt_time(s):
    return f"{int(s // 3600)}h {int((s % 3600) // 60)}m {s % 60:04.1f}s"


def format_sound_card(f):
    """Human/kin-readable rendering of analyze_audio()'s facts."""
    L = []
    r0, r1 = f["region"]
    whole = (r0 <= 0.01 and r1 >= f["duration"] - 0.01)
    L.append(f"SOUND: {_fmt_time(f['duration'])}, {f['samplerate']} Hz, "
             f"{'stereo' if f['channels'] >= 2 else 'mono'}")
    if not whole:
        L.append(f"  (analysed {int(r0 // 60)}m{int(r0 % 60):02d}s "
                 f"-> {int(r1 // 60)}m{int(r1 % 60):02d}s)")

    if f["root_note"]:
        name, cents = f["root_note"]
        L.append(f"\nTONAL CENTRE: {name} ({f['root_hz']:.1f} Hz, {cents:+.0f} cents)")
    L.append("PITCH SET (strongest partials):")
    for hz, _ in f["peaks"][:8]:
        name, cents = _note_of(hz)
        tag = "   <- root" if hz == f["root_hz"] else ""
        L.append(f"  {hz:7.1f} Hz   {name:>4} {cents:+4.0f} cents{tag}")

    L.append("\nDETUNING / BEATING (between-the-semitones signature):")
    if f["beats"]:
        for a, b, beat, name in f["beats"]:
            L.append(f"  {a:.1f} & {b:.1f} Hz  beat ~{beat:.1f} Hz  "
                     f"(detuned pair around {name})")
    else:
        L.append("  (no strongly-beating close pairs in this window)")

    c = f["centroid"]
    bright = "dark" if c < 500 else "mid" if c < 2000 else "bright"
    L.append(f"\nBRIGHTNESS: centroid {c:.0f} Hz ({bright})")
    L.append(f"LOUDNESS: {f['rms_dbfs']:.1f} dBFS")
    if f["movement_hz"]:
        p = f["movement_hz"]
        L.append(f"MOVEMENT: slow pulse ~{p:.2f} Hz ({1 / p:.1f}s per cycle)")
    if f["stereo_corr"] is not None:
        c2 = f["stereo_corr"]
        w = "near-mono" if c2 > 0.9 else "wide" if c2 < 0.5 else "moderate width"
        L.append(f"STEREO: L/R correlation {c2:.2f} ({w})")

    if f["evolution"]:
        L.append("\nEVOLUTION across the analysed span:")
        for off, name, hz, cen in f["evolution"]:
            L.append(f"  {int(off // 60):3d}m: root ~{name} ({hz:.0f} Hz), "
                     f"centroid {cen:.0f} Hz")
    return "\n".join(L)


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Acoustic facts about an audio file.")
    ap.add_argument("path")
    ap.add_argument("--start", type=float, default=None, help="region start (seconds)")
    ap.add_argument("--end", type=float, default=None, help="region end (seconds)")
    ap.add_argument("--skip-edges", type=float, default=None,
                    help="trim this many seconds off both ends (e.g. fades)")
    a = ap.parse_args(argv)
    facts = analyze_audio(a.path, start=a.start, end=a.end, skip_edges=a.skip_edges)
    print(format_sound_card(facts))


if __name__ == "__main__":
    import sys
    _main(sys.argv[1:])
