// Renders one /teach episode folder to video.
//
//   node render.mjs <episode-dir> --stills 0,4.5,12      look at single frames first
//   node render.mjs <episode-dir>                       full render -> <episode-dir>/video.mp4
//   node render.mjs <episode-dir> --serve               preview at http://localhost:4710/__teach/player.html?t=3
//
// The episode dir holds episode.json, timeline.json, narration.wav and shots/.
// Rendering is split into chunks (work/chunks/*.mp4, --chunk seconds, default 20).
// Finished chunks are kept, so if a run is cut off just run the same command again.
// --fps overrides episode.format.fps (default 30); --chunks-only stops before muxing.

import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { launch } from "./browser.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const args = process.argv.slice(2);
const dir = path.resolve(args[0] ?? "");
const opt = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i === -1 ? fallback : args[i + 1] ?? true;
};
if (!args[0] || !fs.existsSync(path.join(dir, "episode.json"))) {
  console.error("usage: node render.mjs <episode-dir> [--stills t1,t2] [--serve] [--chunk 20] [--fps 30]");
  process.exit(1);
}

export function ffmpegPath() {
  if (process.env.FFMPEG) return process.env.FFMPEG;
  if (spawnSync("ffmpeg", ["-version"]).status === 0) return "ffmpeg";
  try {
    const p = require("ffmpeg-static");
    if (p && fs.existsSync(p)) return p;
  } catch {}
  const py = spawnSync("python3", ["-c", "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"]);
  if (py.status === 0) return py.stdout.toString().trim();
  throw new Error("ffmpeg not found: install it, `npm i ffmpeg-static`, or `pip install imageio-ffmpeg`");
}

// ---------- static server: the episode dir plus /__teach/ assets ----------
const types = { ".html": "text/html; charset=utf-8", ".json": "application/json", ".png": "image/png",
  ".jpg": "image/jpeg", ".css": "text/css", ".js": "text/javascript", ".woff2": "font/woff2", ".woff": "font/woff",
  ".ttf": "font/ttf", ".otf": "font/otf", ".svg": "image/svg+xml", ".wav": "audio/wav" };

function pkgDir(name) {
  try { return path.dirname(require.resolve(`${name}/package.json`)); } catch { return null; }
}
function fontsCss() {
  // Inter for text and JetBrains Mono for code, both with Cyrillic.
  let css = "";
  for (const [pkg, weights] of [["@fontsource/inter", [400, 500, 600, 700, 800]], ["@fontsource/jetbrains-mono", [400, 600]]]) {
    const root = pkgDir(pkg);
    if (!root) continue;
    for (const w of weights) {
      const f = path.join(root, `${w}.css`);
      if (fs.existsSync(f)) css += fs.readFileSync(f, "utf8").replaceAll("./files/", `/__teach/pkg/${pkg}/files/`);
    }
  }
  return css;
}
function teachAsset(rel) {
  if (rel === "player.html") return path.join(here, "..", "assets", "player.html");
  const cdn = pkgDir("@highlightjs/cdn-assets");
  if (rel === "hljs.js" && cdn) return path.join(cdn, "highlight.min.js");
  if (rel === "hljs.css" && cdn) return path.join(cdn, "styles", "github-dark.min.css");
  const m = /^pkg\/(@[^/]+\/[^/]+|[^/]+)\/(.+)$/.exec(rel);
  if (m && pkgDir(m[1])) return path.join(pkgDir(m[1]), m[2]);
  return null;
}
function serve(port = 0) {
  const fonts = fontsCss();
  const server = http.createServer((req, res) => {
    const url = decodeURIComponent(new URL(req.url, "http://x").pathname);
    if (url === "/theme.css" && !fs.existsSync(path.join(dir, "theme.css"))) {
      res.writeHead(200, { "content-type": "text/css" });
      return res.end("");
    }
    if (url === "/__teach/fonts.css") {
      res.writeHead(200, { "content-type": "text/css" });
      return res.end(fonts);
    }
    let file = url.startsWith("/__teach/") ? teachAsset(url.slice(9)) : path.join(dir, url);
    if (!file || !path.resolve(file).startsWith(path.resolve(url.startsWith("/__teach/") ? path.dirname(file) : dir)) ||
        !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404);
      return res.end();
    }
    res.writeHead(200, { "content-type": types[path.extname(file)] ?? "application/octet-stream" });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((ok) => server.listen(port, "127.0.0.1", () => ok(server)));
}

// ---------- main ----------
const episode = JSON.parse(fs.readFileSync(path.join(dir, "episode.json"), "utf8"));
const W = episode.format?.w ?? 1920, H = episode.format?.h ?? 1080;
const fps = Number(opt("fps", episode.format?.fps ?? 30));
const server = await serve(opt("serve") ? 4710 : 0);
const base = `http://127.0.0.1:${server.address().port}`;

if (opt("serve")) {
  console.log(`preview: ${base}/__teach/player.html?t=0  (Ctrl+C to stop)`);
} else {
  const browser = await launch();
  const page = await browser.newPage({ viewport: { width: W, height: H } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => m.type() === "error" && !m.text().startsWith("Failed to load resource") && errors.push(m.text()));
  await page.goto(`${base}/__teach/player.html`);
  await page.evaluate(() => window.__ready);
  if (errors.length) throw new Error("player errors:\n" + errors.join("\n"));
  const total = await page.evaluate(() => window.__duration);
  const shot = (t) => page.evaluate((t) => window.__seek(t), t)
    .then(() => { if (errors.length) throw new Error(`at t=${t}: ${errors.join("; ")}`); })
    .then(() => page.screenshot({ type: "jpeg", quality: 92 }));

  const work = path.join(dir, "work");
  fs.mkdirSync(work, { recursive: true });
  const ff = ffmpegPath();

  if (opt("stills")) {
    const out = path.join(work, "stills");
    fs.mkdirSync(out, { recursive: true });
    const times = String(opt("stills")) === "auto" ? autoTimes(episode, total) : String(opt("stills")).split(",").map(Number);
    for (const t of times) {
      const file = path.join(out, `t${t.toFixed(2).padStart(7, "0")}.jpg`);
      fs.writeFileSync(file, await shot(t));
      console.log(file);
    }
  } else {
    const chunk = Number(opt("chunk", 20));
    const chunks = path.join(work, "chunks");
    fs.mkdirSync(chunks, { recursive: true });
    const frames = Math.round(total * fps);
    const perChunk = Math.round(chunk * fps);
    const list = [];
    for (let c = 0; c * perChunk < frames; c++) {
      const file = path.join(chunks, `c${String(c).padStart(4, "0")}.mp4`);
      list.push(file);
      if (fs.existsSync(file)) continue;
      const tmp = file + ".part.mp4";
      const enc = spawn(ff, ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(fps), "-c:v", "mjpeg", "-i", "-",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", String(fps), tmp], { stdio: ["pipe", "inherit", "inherit"] });
      const done = new Promise((ok, fail) => enc.on("close", (code) => (code === 0 ? ok() : fail(new Error("ffmpeg failed")))));
      const end = Math.min(frames, (c + 1) * perChunk);
      for (let f = c * perChunk; f < end; f++) {
        const buf = await shot(f / fps);
        if (!enc.stdin.write(buf)) await new Promise((ok) => enc.stdin.once("drain", ok));
        if (f === 0) fs.writeFileSync(path.join(dir, "poster.jpg"), buf);
      }
      enc.stdin.end();
      await done;
      fs.renameSync(tmp, file);
      console.log(`chunk ${c + 1}/${Math.ceil(frames / perChunk)} done (${(end / fps).toFixed(1)}s / ${total.toFixed(1)}s)`);
    }
    if (!fs.existsSync(path.join(dir, "poster.jpg"))) fs.writeFileSync(path.join(dir, "poster.jpg"), await shot(0));
    if (!opt("chunks-only")) {
      const listFile = path.join(chunks, "list.txt");
      fs.writeFileSync(listFile, list.map((f) => `file '${path.basename(f)}'`).join("\n"));
      // soundtrack.wav (voice + music from music.py) wins, unless the voice was re-done after it.
      const voice = path.join(dir, "narration.wav");
      const mixed = path.join(dir, "soundtrack.wav");
      let audio = voice;
      if (fs.existsSync(mixed)) {
        if (!fs.existsSync(voice) || fs.statSync(mixed).mtimeMs >= fs.statSync(voice).mtimeMs) audio = mixed;
        else console.warn("soundtrack.wav is older than narration.wav: using the voice only; re-run music.py");
      }
      const outFile = path.join(dir, "video.mp4");
      const mux = spawnSync(ff, ["-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listFile,
        ...(fs.existsSync(audio) ? ["-i", audio, "-c:a", "aac", "-b:a", "192k", "-ar", "48000"] : []),
        "-c:v", "copy", "-t", total.toFixed(3), "-movflags", "+faststart", outFile], { stdio: "inherit" });
      if (mux.status !== 0) throw new Error("muxing failed");
      console.log(`video: ${outFile} (${total.toFixed(1)}s)`);
    }
  }
  await browser.close();
  server.close();
}

// One still in the settled middle of every scene plus one right at each scene change.
function autoTimes(ep, total) {
  const tl = JSON.parse(fs.readFileSync(path.join(dir, "timeline.json"), "utf8"));
  const ts = [];
  for (const s of tl.scenes) {
    ts.push(s.start + s.dur * 0.6);
    if (s.start > 0) ts.push(s.start + 0.12);
  }
  return ts.filter((t) => t < total).sort((a, b) => a - b);
}
