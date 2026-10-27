"""Quiet background music for one /teach episode.

Synthesizes a soft ambient pad with a sparse melody (no samples, no licensing questions) as long as
the narration, or takes your own track, and mixes it under the voice. The music ducks while the voice
speaks and comes back up in the pauses. Writes:

  <episode>/music.wav        the music alone (synthesized or the looped --track)
  <episode>/soundtrack.wav   voice + music; render.mjs uses it instead of narration.wav

Usage:
  python3 music.py <episode-dir> [--level -26] [--track my-music.mp3] [--seed 7]

--level is the music level in dB below full scale when nobody speaks (the voice sits around -18).
Keep one --seed (or one --track) for the whole series, so every lesson sounds the same.
Needs numpy and ffmpeg.
"""

import argparse
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

RATE = 48000
# A calm loop in A minor: Am9 – Fmaj7 – Cadd9 – G6, four seconds per chord (MIDI note numbers).
CHORDS = [[45, 57, 60, 64, 71], [41, 57, 60, 64, 69], [48, 55, 62, 64, 67], [43, 55, 59, 64, 67]]
MOTIF = [76, 72, 74, 71, 72, 69, 71, 67]  # one soft note every two seconds over the chords
CHORD_SEC = 4.0


def hz(note: int) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


def pad(note: int, dur: float, rng: np.random.Generator) -> np.ndarray:
    """A warm pad note: two slightly detuned partial stacks with a slow swell."""
    t = np.arange(int(dur * RATE)) / RATE
    tone = np.zeros_like(t)
    for detune in (-0.08, 0.08):
        f = hz(note) * 2 ** (detune / 12)
        for k, amp in ((1, 1.0), (2, 0.25), (3, 0.08)):  # few partials keep it muffled
            tone += amp * np.sin(2 * np.pi * f * k * t + rng.uniform(0, 2 * np.pi))
    swell = np.minimum(1, t / 1.2) * np.minimum(1, (dur - t) / 1.5).clip(0)
    return tone * swell * (0.5 + 0.08 * np.sin(2 * np.pi * 0.2 * t))


def bell(note: int, dur: float) -> np.ndarray:
    """A soft mallet note for the melody."""
    t = np.arange(int(dur * RATE)) / RATE
    f = hz(note)
    tone = np.sin(2 * np.pi * f * t) + 0.15 * np.sin(2 * np.pi * 2 * f * t)
    return tone * np.minimum(1, t / 0.02) * np.exp(-t * 1.6)


def synthesize(seconds: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int((seconds + 3) * RATE)
    out = np.zeros(n)
    step = int(CHORD_SEC * RATE)
    for c, start in enumerate(range(0, n, step)):
        chord = CHORDS[c % len(CHORDS)]
        for note in chord:
            seg = pad(note, CHORD_SEC + 1.5, rng) * (0.6 if note < 50 else 0.35)
            end = min(n, start + len(seg))
            out[start:end] += seg[:end - start]
    for m, start in enumerate(range(int(1.0 * RATE), n, int(2.0 * RATE))):
        seg = bell(MOTIF[m % len(MOTIF)], 2.5) * 0.25
        end = min(n, start + len(seg))
        out[start:end] += seg[:end - start]
    # Short exponential-noise reverb for space and a gentle low-pass (above ~1.8 kHz) for the muffled
    # feel, both applied in one FFT pass.
    ir_t = np.arange(int(1.6 * RATE)) / RATE
    ir = rng.standard_normal(len(ir_t)) * np.exp(-ir_t * 3.5) * 0.02
    ir[0] = 1.0
    spectrum = np.fft.rfft(out, 2 * n) * np.fft.rfft(ir, 2 * n)
    freqs = np.fft.rfftfreq(2 * n, 1 / RATE)
    spectrum *= 1 / (1 + (freqs / 1800) ** 4)
    out = np.fft.irfft(spectrum)[:int(seconds * RATE)]
    fade = int(2.0 * RATE)
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    return out / np.max(np.abs(out))


def write_wav(path: Path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes((samples * 32767 * 0.9).astype("<i2").tobytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("--level", type=float, default=-26, help="music level in dBFS while nobody speaks")
    ap.add_argument("--track", help="your own music file instead of the synthesized one (looped)")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    ep = Path(args.episode)
    voice = ep / "narration.wav"
    if not voice.exists():
        sys.exit(f"{voice} not found: run tts.py first")
    with wave.open(str(voice)) as w:
        seconds = w.getnframes() / w.getframerate()
    ff = shutil.which("ffmpeg") or sys.exit("ffmpeg not found")
    music = ep / "music.wav"
    if args.track:
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-stream_loop", "-1", "-i", args.track,
                        "-t", f"{seconds:.3f}", "-ac", "1", "-ar", str(RATE),
                        "-af", f"afade=t=in:d=2,afade=t=out:st={max(0, seconds - 2):.3f}:d=2", str(music)], check=True)
    else:
        write_wav(music, synthesize(seconds, args.seed))
    # Music at --level, ducked by the voice (sidechain), then voice on top.
    graph = (f"[1:a]aresample={RATE},loudnorm=I={args.level + 4}:TP=-6:LRA=7,volume=0dB[m];"
             f"[0:a]aresample={RATE},asplit=2[v][key];"
             f"[m][key]sidechaincompress=threshold=0.02:ratio=6:attack=80:release=600:makeup=1[md];"
             f"[v][md]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[out]")
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(voice), "-i", str(music),
                    "-filter_complex", graph, "-map", "[out]", "-ac", "1", "-ar", str(RATE),
                    str(ep / "soundtrack.wav")], check=True)
    print(f"soundtrack: {seconds:.1f}s, music {'from ' + args.track if args.track else 'synthesized, seed ' + str(args.seed)}, level {args.level} dB")


if __name__ == "__main__":
    main()
