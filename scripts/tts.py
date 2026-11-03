"""Narration for one /teach episode.

Reads episode.json, synthesizes every line, lays the lines out on one timeline
and writes:

  <out>/narration.wav   the full voice track, exactly as long as the video
  <out>/timeline.json   scene and line start/duration in seconds (read by the player)
  <out>/subtitles.srt   subtitles for upload platforms

Usage:
  python3 tts.py <episode.json> <out-dir> [--voice dmitri|irina|denis|ruslan] [--speed 1.0]
  python3 tts.py <episode.json> <out-dir> --engine voicebox --profile "Voice name"
  python3 tts.py <episode.json> <out-dir> --no-voice     # silent track, lines timed for reading

Engines:
  piper     (default) offline, `pip install piper-tts`. The voice model is downloaded once
            from huggingface.co into ~/.cache/teach/voices; --model <path.onnx> uses a local one.
  voicebox  a cloned voice from the Voicebox app (github.com/jamiepine/voicebox) running on
            this machine. --profile is the voice profile name or id, --voicebox-url defaults to
            http://127.0.0.1:17493, --voicebox-engine / --instruct / --seed are passed through.
            Needs ffmpeg (found on PATH, $FFMPEG, ffmpeg-static or imageio-ffmpeg).

Synthesized lines are cached in <out>/lines/ by text and voice, so re-running after
changing pauses or one line only synthesizes what changed.

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
import urllib.error
import urllib.request
import wave
from pathlib import Path

VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
CACHE = Path.home() / ".cache" / "teach" / "voices"

LEAD_IN = 0.5      # silence before the first line of a scene
LINE_GAP = 0.35    # default pause after a line
SCENE_TAIL = 0.6   # silence after the last line of a scene


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


def find_ffmpeg() -> str:
    for candidate in (os.environ.get("FFMPEG"), shutil.which("ffmpeg")):
        if candidate:
            return candidate
    here = Path(__file__).resolve().parent / "node_modules" / "ffmpeg-static"
    for name in ("ffmpeg.exe", "ffmpeg"):
        if (here / name).exists():
            return str(here / name)
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg not found: install it, `npm install --prefix <skill-dir>/scripts`, or `pip install imageio-ffmpeg`")


class Voicebox:
    """Cloned voice from a local Voicebox server; writes 16-bit mono PCM at RATE."""

    RATE = 24000

    def __init__(self, url, profile, engine, instruct, seed, speed, language):
        self.url, self.engine, self.instruct, self.seed, self.speed, self.language = (
            url.rstrip("/"), engine, instruct, seed, speed, language)
        self.ffmpeg = find_ffmpeg()
        try:
            with urllib.request.urlopen(f"{self.url}/profiles", timeout=10) as r:
                profiles = json.load(r)
        except OSError as e:
            sys.exit(f"Voicebox is not reachable at {self.url} ({e}). Start the Voicebox app first.")
        match = [p for p in profiles if p["id"] == profile or p.get("name", "").lower() == profile.lower()]
        if not match:
            names = ", ".join(p.get("name", p["id"]) for p in profiles) or "none"
            sys.exit(f'Voicebox has no voice profile "{profile}". Profiles: {names}')
        self.profile_id = match[0]["id"]
        self.key = f"voicebox|{self.profile_id}|{engine}|{instruct}|{seed}|{speed}"

    def __call__(self, text, path):
        body = {"profile_id": self.profile_id, "text": text, "language": self.language, "seed": self.seed}
        if self.engine:
            body["engine"] = self.engine
        if self.instruct:
            body["instruct"] = self.instruct
        req = urllib.request.Request(f"{self.url}/generate/stream", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            sys.exit(f"Voicebox failed on {text!r}: {e.code} {e.read().decode(errors='replace')[:300]}")
        src = path.with_suffix(".src.wav")
        src.write_bytes(raw)
        tempo = ["-filter:a", f"atempo={self.speed}"] if abs(self.speed - 1) > 1e-3 else []
        subprocess.run([self.ffmpeg, "-y", "-loglevel", "error", "-i", str(src), *tempo,
                        "-ac", "1", "-ar", str(self.RATE), "-sample_fmt", "s16", str(path)], check=True)
        src.unlink()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("out")
    ap.add_argument("--voice", default="dmitri", help="dmitri, irina, denis, ruslan or a full Piper voice name")
    ap.add_argument("--speed", type=float, default=1.0, help=">1 is faster")
    ap.add_argument("--model", help="path to a local Piper .onnx model")
    ap.add_argument("--no-voice", action="store_true", help="no speech, time lines by reading speed")
    ap.add_argument("--engine", choices=["piper", "voicebox"], default="piper")
    ap.add_argument("--profile", help="voicebox: voice profile name or id")
    ap.add_argument("--voicebox-url", default="http://127.0.0.1:17493")
    ap.add_argument("--voicebox-engine", help="voicebox: qwen, chatterbox, ... (default: the profile's engine)")
    ap.add_argument("--instruct", help="voicebox: delivery instruction, e.g. calm and friendly")
    ap.add_argument("--seed", type=int, default=7, help="voicebox: a fixed seed keeps the voice steady across lines")
    ap.add_argument("--language", default="ru")
    ap.add_argument("--max-new", type=int, help="synthesize at most N new lines, then exit with code 3 (run again to continue)")
    args = ap.parse_args()

    episode = json.loads(Path(args.episode).read_text(encoding="utf-8"))
    out = Path(args.out)
    (out / "lines").mkdir(parents=True, exist_ok=True)

    if args.no_voice:
        synth, rate, key = None, 22050, "silent"
    elif args.engine == "voicebox":
        if not args.profile:
            sys.exit("--engine voicebox needs --profile <voice profile name>")
        synth = Voicebox(args.voicebox_url, args.profile, args.voicebox_engine, args.instruct,
                         args.seed, args.speed, args.language)
        rate, key = Voicebox.RATE, synth.key
    else:
        from piper import PiperVoice, SynthesisConfig

        model = args.model or str(voice_files(args.voice))
        voice = PiperVoice.load(model)
        config = SynthesisConfig(length_scale=1.0 / args.speed)
        rate, key = voice.config.sample_rate, f"piper|{Path(model).name}|{args.speed}"

        def synth(text, path):
            with wave.open(str(path), "wb") as wav:
                voice.synthesize_wav(text, wav, syn_config=config)

    pcm = bytearray()

    def silence(seconds: float) -> None:
        pcm.extend(b"\x00\x00" * int(round(seconds * rate)))

    def now() -> float:
        return len(pcm) / 2 / rate

    timeline = {"scenes": [], "sampleRate": rate}
    made = 0
    srt = []
    for scene in episode["scenes"]:
        start = now()
        entry = {"id": scene["id"], "start": start, "lines": []}
        lines = scene.get("lines", [])
        silence(scene.get("leadIn", LEAD_IN if lines else 0))
        for i, line in enumerate(lines):
            if synth:
                digest = hashlib.sha1(f"{key}|{line['say']}".encode()).hexdigest()[:10]
                path = out / "lines" / f"{scene['id']}-{i}-{digest}.wav"
                if not path.exists():
                    # Same text and voice already spoken in another scene or a copied lines/ folder
                    same = next((out / "lines").glob(f"*-{digest}.wav"), None)
                    if same:
                        shutil.copyfile(same, path)
                    else:
                        if args.max_new is not None and made >= args.max_new:
                            print(f"made {made} new lines, more left: run again")
                            sys.exit(3)
                        synth(line["say"], path)
                        made += 1
                with wave.open(str(path), "rb") as wav:
                    if wav.getframerate() != rate or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                        sys.exit(f"{path}: expected 16-bit mono {rate} Hz")
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
    print(f"narration: {timeline['total']:.1f}s, {len(srt)} lines, {len(timeline['scenes'])} scenes")


if __name__ == "__main__":
    main()
