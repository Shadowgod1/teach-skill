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
console.log("Войдите в открывшемся окне. После входа окно закроется само.");

// Save once the person has signed in (a cookie appeared and the page left the
// sign-in form), then close the window: the session must not keep being written.
const start = Date.now();
let saved = false;
const loginUrl = page.url();
while (!saved && Date.now() - start < 10 * 60 * 1000) {
  await new Promise((r) => setTimeout(r, 1500));
  if (page.isClosed()) break;
  const signedIn = (await context.cookies()).length > 0 && !/login|signin|auth/i.test(new URL(page.url()).pathname)
    && page.url() !== loginUrl.replace(/\/login.*$/, "/login");
  if (signedIn) {
    await page.waitForTimeout(2000);
    await context.storageState({ path: outFile });
    saved = true;
  }
}
await browser.close().catch(() => {});
console.log(saved ? `session saved: ${outFile} (delete it when the lesson is done)` : "session was not saved");
process.exit(saved ? 0 : 1);
