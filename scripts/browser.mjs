// Finds a Chromium to drive: Playwright's own build, any build in the
// Playwright cache, then installed Chrome / Edge.
import { chromium } from "playwright-core";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

function cachedChromium() {
  const roots = [
    process.env.PLAYWRIGHT_BROWSERS_PATH,
    path.join(os.homedir(), "AppData", "Local", "ms-playwright"),
    path.join(os.homedir(), ".cache", "ms-playwright"),
    path.join(os.homedir(), "Library", "Caches", "ms-playwright"),
  ].filter(Boolean);
  const exes = [
    ["chrome-win", "chrome.exe"],
    ["chrome-win64", "chrome.exe"],
    ["chrome-linux", "chrome"],
    ["chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium"],
  ];
  for (const root of roots) {
    if (!fs.existsSync(root)) continue;
    const dirs = fs.readdirSync(root).filter((d) => /^chromium-\d+$/.test(d)).sort().reverse();
    for (const dir of dirs)
      for (const exe of exes) {
        const p = path.join(root, dir, ...exe);
        if (fs.existsSync(p)) return p;
      }
  }
  return null;
}

export async function launch() {
  const own = chromium.executablePath();
  const executablePath = fs.existsSync(own) ? own : cachedChromium();
  if (executablePath) return chromium.launch({ executablePath });
  for (const channel of ["chrome", "msedge"]) {
    try {
      return await chromium.launch({ channel });
    } catch {}
  }
  throw new Error("No Chromium found. Run: npx playwright install chromium");
}
