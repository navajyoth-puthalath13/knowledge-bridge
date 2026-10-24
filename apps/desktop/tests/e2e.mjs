// End-to-end test: launches the real Electron app, which starts the real Python
// service, and drives the UI through setup, dry run, sync, review and diagnostics
// against fixture notes. Uses the deterministic mock AI provider.
//
//   npm run build && xvfb-run -a npm run test:e2e        (Linux CI)
//   npm run build && npm run test:e2e                     (macOS)
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { _electron: electron } = require("playwright");
const here = path.dirname(fileURLToPath(import.meta.url));
const appDir = path.resolve(here, "..");
const repo = path.resolve(appDir, "..", "..");
const shots = process.env.KB_SCREENSHOTS || path.join(appDir, "test-results");
fs.mkdirSync(shots, { recursive: true });

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "kb-e2e-"));
const src = path.join(tmp, "notes");
const vault = path.join(tmp, "Vault");
const data = path.join(tmp, "data");
fs.cpSync(path.join(repo, "fixtures", "notes"), src, { recursive: true });
fs.mkdirSync(path.join(vault, ".obsidian"), { recursive: true }); // a real vault has .obsidian/
fs.mkdirSync(data);
// Only the provider is pre-seeded (the mock provider is not offered in the UI); everything else is set through Setup.
fs.writeFileSync(path.join(data, "config.json"), JSON.stringify({ provider: { name: "mock" } }));

const env = { ...process.env, KB_DATA_DIR: data, KB_MOCK_RESPONSES: path.join(repo, "fixtures", "mock_responses.json"), KB_DISABLE_KEYRING: "1" };
delete env.NODE_OPTIONS;
const app = await electron.launch({
  executablePath: require("electron"),
  args: [appDir, ...(process.getuid?.() === 0 ? ["--no-sandbox"] : [])],
  env,
});
const page = await app.firstWindow();
page.setDefaultTimeout(20000);
const shot = (name) => page.screenshot({ path: path.join(shots, `${name}.png`) });
const step = (s) => console.log("•", s);

try {
  step("service starts and first launch opens Setup");
  await page.getByText("Service connected").waitFor();
  await page.getByText("Welcome").waitFor();

  step("configure source folder and vault through the UI");
  await page.getByRole("button", { name: "Exported files folder" }).click();
  const folderInput = page.locator("section", { hasText: "Apple Notes source" }).locator('input[type="text"]').first();
  await folderInput.fill(src);
  await page.getByPlaceholder("/Users/you/Documents/Vault").fill(vault);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByText("Settings saved").waitFor();
  await page.getByRole("button", { name: "Test connection" }).click();
  await page.getByText(/mock provider with \d+ canned responses/).waitFor();
  await shot("01-setup");

  step("dry run previews changes and writes nothing");
  await page.getByRole("button", { name: "Dashboard" }).click();
  await page.getByRole("button", { name: "Dry run" }).click();
  await page.getByText("Dry run", { exact: true }).last().waitFor();
  await page.locator("td", { hasText: "Design Days 2026 — Day 2" }).first().waitFor();
  // Saving Setup creates the empty folder structure; the dry run itself must not write any notes.
  assert.ok(fs.existsSync(path.join(vault, "Knowledge Bridge", "Entities", "Books")), "folder structure created on save");
  assert.deepEqual(fs.readdirSync(path.join(vault, "Apple Notes")), [], "dry run must not write notes");
  await shot("02-dry-run");

  step("sync now mirrors notes and builds the graph");
  await page.getByRole("button", { name: "Sync now" }).click();
  await page.getByText("6 discovered", { exact: false }).or(page.getByText("7 discovered", { exact: false })).waitFor();
  const metric = (label) => page.locator(".metric", { hasText: label }).locator(".metric-value").innerText();
  await page.waitForFunction(() => document.querySelector(".metric .metric-value")?.textContent === "6");
  assert.equal(await metric("Synced notes"), "6");
  assert.equal(await metric("Entities"), "11");
  assert.equal(await metric("Pending reviews"), "5");
  await shot("03-dashboard-after-sync");

  const ff = path.join(vault, "Knowledge Bridge", "Entities", "Books", "Finding Flow.md");
  assert.ok(fs.existsSync(ff), "Finding Flow page exists");
  const ffText = fs.readFileSync(ff, "utf8");
  assert.match(ffText, /\[\[Apple Notes\/Design Days 2026 — Day 2\|/);
  assert.match(ffText, /\[\[Apple Notes\/Design Matters — Debbie Millman with a product designer\|/);
  const mirror = fs.readFileSync(path.join(vault, "Apple Notes", "Design Days 2026 — Day 2.md"), "utf8");
  assert.ok(!/(?<!!)\[\[/.test(mirror), "no links inserted into mirrored notes");

  step("review queue: create a person from an ambiguous reference");
  await page.getByRole("button", { name: /Intelligence/ }).click();
  await page.getByText("Ambiguous reference").first().waitFor();
  await shot("04-review-queue");
  const alex = page.locator(".review", { hasText: "Alex gave a short talk" });
  await alex.getByRole("button", { name: "Create new" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".review").length === 4);

  step("entities: inspect the canonical book and its evidence");
  await page.getByRole("button", { name: "Entities", exact: true }).click();
  await page.locator("tr", { hasText: "Finding Flow" }).first().click();
  await page.locator(".drawer").getByText("Finding Flow: The Psychology of Engagement with Everyday Life").first().waitFor();
  await page.locator(".drawer").getByText("Halfway through, the speaker mentioned Finding Flow", { exact: false }).waitFor();
  await shot("05-entity-detail");
  await page.locator(".drawer").getByRole("button", { name: "Close" }).click();
  assert.ok(fs.existsSync(path.join(vault, "Knowledge Bridge", "Entities", "People", "Alex.md")), "Alex page rendered after review");

  step("history and diagnostics reflect real state");
  await page.getByRole("button", { name: "Sync history" }).click();
  await page.locator("td .badge.ok", { hasText: "ok" }).first().waitFor();
  await shot("06-history");
  await page.getByRole("button", { name: "Diagnostics" }).click();
  await page.getByText("6 mirrors match their checksum").waitFor();
  await shot("07-diagnostics");

  step("incremental sync after a note changes");
  const meta = path.join(src, "reading-list.meta.json");
  const m = JSON.parse(fs.readFileSync(meta, "utf8"));
  m.modified_at = "2026-10-20T10:00:00Z";
  fs.writeFileSync(meta, JSON.stringify(m));
  fs.writeFileSync(path.join(src, "reading-list.html"),
    "<div><h1>Reading list</h1></div><div>Also want to reread The Design of Everyday Things by Don Norman. And Finding Flow again.</div>");
  await page.getByRole("button", { name: "Dashboard" }).click();
  await page.getByRole("button", { name: "Sync now" }).click();
  await page.getByText("1 mirrored", { exact: false }).waitFor();
  assert.equal(await metric("Synced notes"), "6");
  assert.match(fs.readFileSync(path.join(vault, "Apple Notes", "Reading list.md"), "utf8"), /Finding Flow again/);

  console.log("\nE2E PASSED — screenshots in", shots);
} catch (e) {
  await shot("failure").catch(() => {});
  console.error("\nE2E FAILED:", e);
  process.exitCode = 1;
} finally {
  await app.close();
  // the Python service must be gone after quit
  const logs = fs.readFileSync(path.join(data, "logs", "knowledge-bridge.log"), "utf8");
  if (!process.exitCode) assert.match(logs, /starting Knowledge Bridge service/);
  fs.rmSync(tmp, { recursive: true, force: true });
}
