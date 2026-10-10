"""Narration for one /teach episode.

Reads episode.json, synthesizes every line with Piper (offline neural TTS),
lays the lines out on one timeline and writes:

  <out>/narration.wav   the full voice track, exactly as long as the video
  <out>/timeline.json   scene and line start/duration in seconds (read by the player)
  <out>/subtitles.srt   subtitles for upload platforms

Usage:
  python3 tts.py <episode.json> <out-dir> [--voice dmitri|irina|denis|ruslan] [--speed 1.0]
  python3 tts.py <episode.json> <out-dir> --no-voice     # silent track, lines timed for reading

Needs `pip install piper-tts` (or: uv run --no-project --with piper-tts python tts.py ...).
The voice model is downloaded once from huggingface.co into ~/.cache/teach/voices;
--model <path.onnx> uses a model file you already have (its .onnx.json must sit next to it).

A line is {"say": "text to speak", "sub": "optional subtitle text", "pause": 0.35}.
"say" is what the voice reads (write English terms the way they are pronounced),
"sub" is what the viewer reads (defaults to "say").
"""

import argparse
import json
import sys
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("out")
    ap.add_argument("--voice", default="dmitri", help="dmitri, irina, denis, ruslan or a full Piper voice name")
    ap.add_argument("--speed", type=float, default=1.0, help=">1 is faster")
    ap.add_argument("--model", help="path to a local Piper .onnx model")
    ap.add_argument("--no-voice", action="store_true", help="no speech, time lines by reading speed")
    args = ap.parse_args()

    episode = json.loads(Path(args.episode).read_text(encoding="utf-8"))
    out = Path(args.out)
    (out / "lines").mkdir(parents=True, exist_ok=True)

    if args.no_voice:
        voice, rate = None, 22050
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
    for scene in episode["scenes"]:
        start = now()
        entry = {"id": scene["id"], "start": start, "lines": []}
        lines = scene.get("lines", [])
        silence(scene.get("leadIn", LEAD_IN if lines else 0))
        for i, line in enumerate(lines):
            if voice:
                path = out / "lines" / f"{scene['id']}-{i}.wav"
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
    print(f"narration: {timeline['total']:.1f}s, {len(srt)} lines, {len(timeline['scenes'])} scenes")


if __name__ == "__main__":
    main()
