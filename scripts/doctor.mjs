// Checks that this machine can make /teach videos and says how to fix what's missing.
//   node doctor.mjs
//   node doctor.mjs --voice "voicebox:<profile name>"   check the Voicebox app instead of Piper
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

const rows = [];
const ok = (name, detail) => rows.push(["ok  ", name, detail]);
const bad = (name, detail) => rows.push(["FAIL", name, detail]);
const run = (cmd, args) => spawnSync(cmd, args, { encoding: "utf8", shell: process.platform === "win32" });

const major = Number(process.versions.node.split(".")[0]);
major >= 18 ? ok("node", process.version) : bad("node", `${process.version}; need 18+`);

let deps = true;
for (const p of ["playwright-core", "@fontsource/inter", "@highlightjs/cdn-assets"]) {
  try { require.resolve(`${p}/package.json`); } catch { deps = false; }
}
deps ? ok("npm deps", "installed") : bad("npm deps", "run: npm install --prefix <skill-dir>/scripts");

try {
  const { launch } = await import("./browser.mjs");
  const b = await launch();
  ok("chromium", b.version());
  await b.close();
} catch (e) {
  bad("chromium", `${e.message.split("\n")[0]} -> npx playwright install chromium (or: pip install playwright && playwright install chromium)`);
}

let ff = process.env.FFMPEG || (spawnSync("ffmpeg", ["-version"]).status === 0 ? "ffmpeg" : null);
if (!ff) try { const p = require("ffmpeg-static"); if (p && fs.existsSync(p)) ff = p; } catch {}
if (!ff) {
  const r = spawnSync("python3", ["-c", "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"], { encoding: "utf8" });
  if (r.status === 0) ff = r.stdout.trim();
}
ff ? ok("ffmpeg", ff) : bad("ffmpeg", "apt-get install ffmpeg, npm i ffmpeg-static, or pip install imageio-ffmpeg");

const voiceArg = process.argv.indexOf("--voice");
const voiceOpt = voiceArg > 0 ? process.argv[voiceArg + 1] ?? "" : "";
const voicebox = voiceOpt.startsWith("voicebox:") ? voiceOpt.slice("voicebox:".length).trim() : null;

const py = ["python3", "python"].find((p) => run(p, ["--version"]).status === 0);
if (!py) bad("python", "python 3.9+ is needed for narration");
else {
  ok("python", run(py, ["--version"]).stdout.trim());
  if (!voicebox) {
    run(py, ["-c", "import piper"]).status === 0
      ? ok("piper-tts", "installed")
      : bad("piper-tts", `run: ${py} -m pip install piper-tts  (or use uv run --with piper-tts)`);
  }
}

const cache = path.join(os.homedir(), ".cache", "teach", "voices");
const cached = fs.existsSync(cache) ? fs.readdirSync(cache).filter((f) => f.endsWith(".onnx")) : [];
if (voicebox) {
  const url = (process.env.VOICEBOX_URL || "http://127.0.0.1:17493").replace(/\/$/, "");
  const get = async (p) => (await fetch(url + p, { signal: AbortSignal.timeout(8000) })).json();
  try {
    const health = await get("/health");
    ok("voicebox", `${url}, ${health.backend_variant}${health.backend_variant === "cpu" ? " (about a minute per 10 s of speech)" : ""}`);
    const { models } = await get("/models/status");
    models.find((m) => m.model_name === "qwen-tts-1.7B")?.downloaded
      ? ok("qwen-tts", "1.7B downloaded")
      : bad("qwen-tts", "download Qwen TTS 1.7B in Voicebox > Models (~4 GB)");
    const profiles = await get("/profiles");
    const hit = profiles.filter((p) => p.name === voicebox || p.id === voicebox
      || p.name.toLowerCase().startsWith(voicebox.toLowerCase()));
    hit.length === 1
      ? ok("profile", `«${hit[0].name}»`)
      : bad("profile", `«${voicebox}» ${hit.length ? "is ambiguous" : "not found"}; profiles: ${profiles.map((p) => `«${p.name}»`).join(", ") || "none"}`);
  } catch {
    bad("voicebox", `no answer at ${url}: start the Voicebox app (its server takes ~20 s) or set VOICEBOX_URL`);
  }
} else if (cached.length) ok("voice", `cached: ${cached.join(", ")}`);
else {
  try {
    const r = await fetch("https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json", { method: "HEAD", signal: AbortSignal.timeout(8000) });
    r.ok ? ok("voice", "huggingface.co reachable; the model downloads on first use (~63 MB)")
         : bad("voice", `huggingface.co answered ${r.status}`);
  } catch {
    bad("voice", "huggingface.co is not reachable. On claude.ai allow the domain huggingface.co in Settings > Capabilities > Code execution, "
      + "or pass --model with a local .onnx voice, or use --no-voice (subtitles only)");
  }
}

for (const [s, n, d] of rows) console.log(`${s}  ${n.padEnd(10)} ${d}`);
process.exit(rows.some((r) => r[0] === "FAIL") ? 1 : 0);
