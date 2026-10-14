import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { defineConfig } from "@playwright/test";

// End-to-end tests: a real `blendscape serve` (with the built web/dist) and a real browser. Needs the Python package
// installed (`pip install -e ".[dev]"`), `npm run build` done, and the rubberband executable for previews/renders.
const PORT = Number(process.env.E2E_PORT ?? 8801);
const run = path.join(process.cwd(), "e2e", ".run");
const home = path.join(run, "home");
const audio = path.join(run, "audio");
const py = process.env.PYTHON ?? "python";

if (!process.env.TEST_WORKER_INDEX) {
  // only the main process: fresh data folder, fixtures built once
  fs.rmSync(home, { recursive: true, force: true });
  fs.mkdirSync(home, { recursive: true });
  execSync(`${py} e2e/make_fixtures.py ${JSON.stringify(audio)}`, { stdio: "inherit" });
}

const chromium = process.env.PW_CHROMIUM ?? "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";

export default defineConfig({
  testDir: "./e2e",
  timeout: 240_000,
  expect: { timeout: 20_000 },
  workers: 1,
  fullyParallel: false,
  reporter: [["list"]],
  outputDir: path.join(run, "results"),
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    launchOptions: { executablePath: fs.existsSync(chromium) ? chromium : undefined, args: ["--no-sandbox"] },
  },
  webServer: {
    command: `${py} -m blendscape serve --port ${PORT} --home ${JSON.stringify(home)}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
