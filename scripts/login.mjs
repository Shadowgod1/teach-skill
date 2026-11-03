// Opens a visible browser window so the person signs in to the app themselves,
// then saves the session for capture.mjs. Claude never types the password.
//
//   node login.mjs <url> <out-auth.json>
//
// Sign in, wait until the app has loaded, then close the window.
// The session (cookies, local storage) is saved to <out-auth.json>; point
// "storageState" in shots.json at it. The file is a login: keep it out of git
// and delete it when the lesson is done.

import fs from "node:fs";
import path from "node:path";
import { launch } from "./browser.mjs";

const [url, outFile] = process.argv.slice(2);
if (!url || !outFile) {
  console.error("usage: node login.mjs <url> <out-auth.json>");
  process.exit(1);
}
fs.mkdirSync(path.dirname(path.resolve(outFile)), { recursive: true });

const browser = await launch({ headless: false });
const context = await browser.newContext({ viewport: null, locale: "ru-RU" });
const page = await context.newPage();
await page.goto(url);
console.log("Войдите в открывшемся окне. Когда приложение откроется, закройте окно.");

let saved = false;
const save = async () => {
  try {
    await context.storageState({ path: outFile });
    saved = true;
  } catch {}
};
const timer = setInterval(save, 1500);
await new Promise((done) => {
  page.on("close", done);
  browser.on("disconnected", done);
});
clearInterval(timer);
await browser.close().catch(() => {});
console.log(saved ? `session saved: ${outFile}` : "session was not saved");
process.exit(saved ? 0 : 1);
