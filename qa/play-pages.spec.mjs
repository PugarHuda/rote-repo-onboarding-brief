// Real-browser QA for the four published Plays. Run: npm test (in this folder)
// Opens each public page in Chromium and asserts HTTP 200, the version this
// repository's manifest declares, the Public badge, the local tools, the inputs
// a stranger will be asked for, and the read-only wording. Exits non-zero on
// any mismatch so it can gate a publish.
import { chromium } from "playwright";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const REPO = fileURLToPath(new URL("..", import.meta.url));
const PLAYS = [
  { name: "repo-onboarding-brief", manifest: `${REPO}main.ts`, inputs: ["Repo", "Branch"] },
  { name: "monorepo-workspace-map", manifest: `${REPO}plays/monorepo-workspace-map/main.ts`, inputs: ["Repo", "Branch"] },
  { name: "codeowners-drift", manifest: `${REPO}plays/codeowners-drift/main.ts`, inputs: ["Repo", "Branch", "Verify owners"] },
  { name: "dependency-trust-diff", manifest: `${REPO}plays/dependency-trust-diff/main.ts`, inputs: ["Repo", "Branch", "Scope", "Max packages"] },
];

const versionOf = (file) => readFileSync(file, "utf8").match(/^\s*\*\s+version:\s*([0-9.]+)/m)[1];

const browser = await chromium.launch();
const page = await browser.newPage();
let failures = 0;
for (const p of PLAYS) {
  const url = `https://play.modiqo.ai/pugarhuda/${p.name}`;
  const res = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
  const body = await page.locator("main").innerText();
  // The full description sits in a collapsed <details class="more">; innerText skips it, the DOM has it.
  const html = await page.content();
  const expected = versionOf(p.manifest);
  // The registry also serves a machine-readable manifest per version; compare against that too.
  const manifest = await (await page.request.get(`${url}@${expected}.json`)).json().catch(() => ({}));
  const registryVersion = manifest?.metadata?.version ?? manifest?.version ?? null;
  const checks = {
    "HTTP 200": res.status() === 200,
    [`registry JSON declares ${expected}`]: registryVersion === expected,
    [`version v${expected} shown`]: body.includes(`v${expected}`),
    "Public badge": body.includes("Public"),
    "tools python3/sh/git/mktemp": ["python3", "sh", "git", "mktemp"].every((t) => body.includes(t)),
    [`inputs ${p.inputs.join("/")}`]: p.inputs.every((i) => body.includes(i)),
    "description mentions read-only": /read-only/i.test(html),
    "description says nothing the repo ships is executed": /nothing the repository ships is ever executed/i.test(html),
  };
  const bad = Object.entries(checks).filter(([, ok]) => !ok).map(([k]) => k);
  failures += bad.length;
  console.log(`${bad.length ? "FAIL" : "ok  "} ${p.name}@${expected}${bad.length ? "  -> " + bad.join(", ") : ""}`);
}
await browser.close();
console.log(failures ? `${failures} check(s) failed` : "all four pages render the version this repository declares");
process.exit(failures ? 1 : 0);
