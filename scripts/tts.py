"""Narration for one /teach episode.

Reads episode.json, synthesizes every line with Piper (offline neural TTS) or with
a cloned voice from a local Voicebox app, lays the lines out on one timeline and writes:

  <out>/narration.wav   the full voice track, exactly as long as the video
  <out>/timeline.json   scene and line start/duration in seconds (read by the player)
  <out>/subtitles.srt   subtitles for upload platforms

Usage:
  python3 tts.py <episode.json> <out-dir> [--voice dmitri|irina|denis|ruslan] [--speed 1.0]
  python3 tts.py <episode.json> <out-dir> --voice "voicebox:<profile name>" [--seed 42]
  python3 tts.py <episode.json> <out-dir> --no-voice     # silent track, lines timed for reading

Piper needs `pip install piper-tts` (or: uv run --no-project --with piper-tts python tts.py ...).
The voice model is downloaded once from huggingface.co into ~/.cache/teach/voices;
--model <path.onnx> uses a model file you already have (its .onnx.json must sit next to it).

Voicebox (https://github.com/jamiepine/voicebox) must be running; its server listens on
http://127.0.0.1:17493 (override with --voicebox-url or VOICEBOX_URL). Every line is generated
by Qwen3-TTS in the given voice profile and cached in lines/: a rerun only regenerates lines
whose text, voice, seed or speed changed. Needs ffmpeg to bring lines to one sample format.

A line is {"say": "text to speak", "sub": "optional subtitle text", "pause": 0.35}.
"say" is what the voice reads (write English terms the way they are pronounced),
"sub" is what the viewer reads (defaults to "say").
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
CACHE = Path.home() / ".cache" / "teach" / "voices"

LEAD_IN = 0.5      # silence before the first line of a scene
LINE_GAP = 0.35    # default pause after a line
SCENE_TAIL = 0.6   # silence after the last line of a scene

VOICEBOX_RATE = 24000      # Qwen3-TTS output rate; every line is brought to it
VOICEBOX_LINE_TIMEOUT = 900  # seconds; a CPU-only machine needs ~1 min per 10 s of speech


def voice_files(name: str) -> Path:
    """Download a Piper voice (e.g. ru_RU-dmitri-medium) into the cache once."""
    if "-" not in name:  # short Russian names: dmitri, irina, denis, ruslan
        name = f"ru_RU-{name}-medium"
    lang, speaker, quality = name.split("-")
    model = CACHE / f"{name}.onnx"
    for path in (model, CACHE / f"{name}.onnx.json"):
        if path.exists() and path.stat().st_size > 0:
            continue
        CACHE.mkdir(parents=True, exist_ok=True)
        url = f"{VOICES_URL}/{lang.split('_')[0]}/{lang}/{speaker}/{quality}/{path.name}"
        print(f"downloading {url}", file=sys.stderr)
        urllib.request.urlretrieve(url, path)
    return model


def ffmpeg_exe() -> str:
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            sys.exit("ffmpeg not found: install it, set FFMPEG, or pip install imageio-ffmpeg")
    return exe


class Voicebox:
    """Generates lines with a cloned voice through the local Voicebox REST API."""

    def __init__(self, url: str, profile: str, seed: int, speed: float):
        self.url = url.rstrip("/")
        self.seed = seed
        self.speed = speed
        self.ffmpeg = ffmpeg_exe()
        try:
            health = self.get("/health")
        except OSError:
            sys.exit(f"Voicebox does not answer at {self.url}. Start the Voicebox app "
                     "(its server takes ~20 s to come up) or pass --voicebox-url.")
        profiles = self.get("/profiles")
        match = [p for p in profiles if profile in (p["id"], p["name"])]
        if not match:
            match = [p for p in profiles if p["name"].lower().startswith(profile.lower())]
        if len(match) != 1:
            names = ", ".join(f"«{p['name']}»" for p in profiles) or "none"
            why = "is ambiguous" if match else "not found"
            sys.exit(f"Voicebox profile «{profile}» {why}. Profiles: {names}")
        self.profile = match[0]
        samples = self.get(f"/profiles/{self.profile['id']}/samples")
        # A changed reference sample changes the voice, so it must invalidate the cache.
        self.voice_key = [self.profile["id"], sorted(s["id"] for s in samples)]
        if health.get("backend_variant") == "cpu":
            print("voicebox: CPU only, expect about a minute per 10 s of speech", file=sys.stderr)

    def get(self, path: str):
        with urllib.request.urlopen(self.url + path, timeout=30) as r:
            return json.load(r)

    def post(self, path: str, body: dict):
        req = urllib.request.Request(self.url + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            sys.exit(f"Voicebox {path} failed: {e.code} {e.read().decode('utf-8', 'replace')}")

    def synthesize(self, text: str, path: Path) -> bool:
        """Write the line to path as 16-bit mono WAV; return False when the cached file was reused."""
        key = hashlib.sha256(json.dumps([self.voice_key, text, self.seed, self.speed],
                                        ensure_ascii=False).encode("utf-8")).hexdigest()
        stamp = path.with_suffix(".key")
        if path.exists() and stamp.exists() and stamp.read_text() == key:
            return False
        gen = self.post("/generate", {"profile_id": self.profile["id"], "text": text, "language": "ru",
                                      "engine": "qwen", "model_size": "1.7B", "seed": self.seed})
        deadline = time.time() + VOICEBOX_LINE_TIMEOUT
        while True:
            state = self.get(f"/history/{gen['id']}")
            if state["status"] == "completed":
                break
            if state["status"] in ("failed", "error", "cancelled"):
                sys.exit(f"Voicebox could not voice «{text}»: {state.get('error') or state['status']}")
            if time.time() > deadline:
                sys.exit(f"Voicebox took longer than {VOICEBOX_LINE_TIMEOUT} s on «{text}»")
            time.sleep(2)
        raw = path.with_suffix(".raw.wav")
        with urllib.request.urlopen(f"{self.url}/audio/{urllib.parse.quote(gen['id'])}", timeout=60) as r:
            raw.write_bytes(r.read())
        tempo = ["-af", f"atempo={self.speed}"] if self.speed != 1.0 else []
        subprocess.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw), *tempo,
                        "-ac", "1", "-ar", str(VOICEBOX_RATE), "-sample_fmt", "s16", str(path)], check=True)
        raw.unlink()
        stamp.write_text(key)
        return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("out")
    ap.add_argument("--voice", default="dmitri",
                    help="dmitri, irina, denis, ruslan, a full Piper voice name, or voicebox:<profile name>")
    ap.add_argument("--speed", type=float, default=1.0, help=">1 is faster")
    ap.add_argument("--model", help="path to a local Piper .onnx model")
    ap.add_argument("--voicebox-url", default=os.environ.get("VOICEBOX_URL", "http://127.0.0.1:17493"))
    ap.add_argument("--seed", type=int, default=42, help="Voicebox seed; keep it fixed for the whole series")
    ap.add_argument("--no-voice", action="store_true", help="no speech, time lines by reading speed")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        stream.reconfigure(encoding="utf-8", errors="replace")

    episode = json.loads(Path(args.episode).read_text(encoding="utf-8"))
    out = Path(args.out)
    (out / "lines").mkdir(parents=True, exist_ok=True)

    voice = cloned = None
    if args.no_voice or args.voice == "none":
        rate = 22050
    elif args.voice.startswith("voicebox:"):
        cloned = Voicebox(args.voicebox_url, args.voice.split(":", 1)[1].strip(), args.seed, args.speed)
        rate = VOICEBOX_RATE
        print(f"voicebox: profile «{cloned.profile['name']}»", file=sys.stderr)
    else:
        from piper import PiperVoice, SynthesisConfig

        voice = PiperVoice.load(args.model or str(voice_files(args.voice)))
        config = SynthesisConfig(length_scale=1.0 / args.speed)
        rate = voice.config.sample_rate

    pcm = bytearray()

    def silence(seconds: float) -> None:
        pcm.extend(b"\x00\x00" * int(round(seconds * rate)))

    def now() -> float:
        return len(pcm) / 2 / rate

    timeline = {"scenes": [], "sampleRate": rate}
    srt = []
    total_lines = sum(len(s.get("lines", [])) for s in episode["scenes"])
    generated = 0
    for scene in episode["scenes"]:
        start = now()
        entry = {"id": scene["id"], "start": start, "lines": []}
        lines = scene.get("lines", [])
        silence(scene.get("leadIn", LEAD_IN if lines else 0))
        for i, line in enumerate(lines):
            if voice or cloned:
                path = out / "lines" / f"{scene['id']}-{i}.wav"
                if cloned:
                    fresh = cloned.synthesize(line["say"], path)
                    generated += fresh
                    print(f"  {'voiced' if fresh else 'cached'} {path.name}", file=sys.stderr)
                else:
                    with wave.open(str(path), "wb") as wav:
                        voice.synthesize_wav(line["say"], wav, syn_config=config)
                with wave.open(str(path), "rb") as wav:
                    frames = wav.readframes(wav.getnframes())
            else:
                # About 0.4 s per word: comfortable reading time for a subtitle.
                words = len(line.get("sub", line["say"]).split())
                frames = bytes(2 * int(max(1.4, words * 0.4) * rate))
            line_start = now()
            pcm.extend(frames)
            dur = now() - line_start
            text = line.get("sub", line["say"])
            entry["lines"].append({"start": line_start, "dur": dur, "text": text})
            srt.append((line_start, line_start + dur, text))
            last = i == len(lines) - 1
            silence(line.get("pause", scene.get("tail", SCENE_TAIL) if last else LINE_GAP))
        # Scenes without narration (title cards, pure demos) need an explicit length.
        if scene.get("minDur") and now() - start < scene["minDur"]:
            silence(scene["minDur"] - (now() - start))
        entry["dur"] = now() - start
        timeline["scenes"].append(entry)
    timeline["total"] = now()

    with wave.open(str(out / "narration.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(bytes(pcm))

    (out / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")

    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

    (out / "subtitles.srt").write_text(
        "\n".join(f"{i}\n{ts(a)} --> {ts(b)}\n{t}\n" for i, (a, b, t) in enumerate(srt, 1)),
        encoding="utf-8",
    )
    reuse = f", {generated} voiced, {total_lines - generated} from cache" if cloned else ""
    print(f"narration: {timeline['total']:.1f}s, {len(srt)} lines, {len(timeline['scenes'])} scenes{reuse}")


if __name__ == "__main__":
    main()
