import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const audio = (n: string) => path.join(process.cwd(), "e2e", ".run", "audio", n);
const shot = (page: Page, name: string) => page.screenshot({ path: path.join(process.cwd(), "e2e", ".run", "screens", `${name}.png`), fullPage: true });

/** Fail a test on any error the page logs. */
function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(`console: ${m.text()}`);
  });
  return errors;
}

/** "1:33.3" -> seconds */
const secs = (t: string) => {
  const [m, s] = t.trim().split(":");
  return Number(m) * 60 + Number(s);
};

async function newProject(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("New project name").fill(name);
  await page.getByRole("button", { name: "New mix" }).click();
  await expect(page.getByRole("textbox", { name: "Project name" })).toHaveValue(name);
}

async function uploadAndAnalyse(page: Page, files: string[]) {
  await page.getByTestId("file-input").setInputFiles(files.map(audio));
  await expect(page.getByRole("region", { name: "Mix timeline" })).toBeVisible({ timeout: 150_000 });
}

test.describe.configure({ mode: "serial" });

test("a whole session: upload, analyse, edit a transition, preview, reorder, render, download", async ({ page, request }) => {
  const errors = watchErrors(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Your mixes" })).toBeVisible();
  await shot(page, "01-home");

  await newProject(page, "E2E set");
  await expect(page.getByText("Start with two or more songs")).toBeVisible();

  // upload the three songs through the real file input; analysis starts by itself
  await page.getByTestId("file-input").setInputFiles([audio("one.wav"), audio("two.wav"), audio("three.wav")]);
  await expect(page.getByText("Analysing the tracks")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("region", { name: "Mix timeline" })).toBeVisible({ timeout: 150_000 });
  await expect(page.getByRole("status").filter({ hasText: "Finished." })).toBeVisible();

  const tracks = page.getByRole("region", { name: "Tracks" });
  await expect(tracks.getByText("124.0 BPM")).toBeVisible();
  await expect(tracks.getByText("128.0 BPM")).toBeVisible();
  await expect(tracks.getByText("126.0 BPM")).toBeVisible();
  await expect(tracks.getByText(/sure \d+%/).first()).toBeVisible();
  await expect(page.getByRole("region", { name: "Transition 1" })).toBeVisible();
  await shot(page, "02-planned");

  // drag the markers with a real mouse: they snap to bars, mark the transition as edited, and Reset undoes it all
  const original = {
    out: secs((await page.getByTestId("out-time").textContent())!),
    inn: secs((await page.getByTestId("in-time").textContent())!),
    overlap: await page.getByTestId("overlap-beats").textContent(),
  };
  const dragHandle = async (id: string, dx: number) => {
    const handle = page.getByTestId(`handle-${id}`).first();
    await handle.scrollIntoViewIfNeeded();
    const box = (await handle.boundingBox())!;
    const x = box.x + box.width / 2;
    const y = box.y + box.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + dx / 2, y, { steps: 5 });
    await page.mouse.move(x + dx, y, { steps: 5 });
    await page.mouse.up();
  };
  await dragHandle("out", -150);
  await expect(page.getByText("edited", { exact: true })).toBeVisible();
  await expect.poll(async () => secs((await page.getByTestId("out-time").textContent())!)).toBeLessThan(original.out - 2);
  await dragHandle("in", 100);
  await expect.poll(async () => secs((await page.getByTestId("in-time").textContent())!)).toBeGreaterThan(original.inn + 1);
  await dragHandle("end-a", -80);
  await expect.poll(async () => await page.getByTestId("overlap-beats").textContent()).not.toBe(original.overlap);
  await shot(page, "02b-dragged");
  await page.getByRole("button", { name: /Reset to the planner's choice/ }).click();
  await expect(page.getByText("edited", { exact: true })).toHaveCount(0);
  await expect(page.getByTestId("overlap-beats")).toHaveText(original.overlap!);
  await expect.poll(async () => secs((await page.getByTestId("out-time").textContent())!)).toBeCloseTo(original.out, 1);

  // nudging "leaves at" by a bar moves it by exactly one bar (4 beats at 124 BPM = 1.935 s)
  const leaves = page.getByTestId("out-time");
  const before = secs((await leaves.textContent())!);
  await page.getByRole("button", { name: /leaves at one bar earlier/ }).click();
  await expect(leaves).not.toHaveText(String(before));
  await expect.poll(async () => secs((await leaves.textContent())!)).toBeCloseTo(before - 4 * (60 / 124), 0);
  await expect(page.getByText("edited by hand")).toBeVisible();
  // an invalid edit is refused with a reason and nothing changes
  const good = await leaves.textContent();
  // (these test songs are only 24 bars long, so a whole phrase later runs off the end)
  await page.getByRole("button", { name: /leaves at one phrase later/ }).click();
  await expect(page.getByRole("alert").filter({ hasText: /runs past the end/ })).toBeVisible();
  await expect(leaves).toHaveText(good!); // ...and nothing changed
  await page.getByRole("button", { name: /Reset to the planner's choice/ }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByText("edited by hand")).toHaveCount(0);

  // overlap presets
  await page.getByRole("button", { name: "16", exact: true }).click();
  await expect(page.getByTestId("overlap-beats")).toHaveText("16 beats");

  // preview: renders, shows progress, then the player has the audio with a real length
  await page.getByRole("button", { name: /Preview this transition/ }).click();
  const player = page.getByRole("region", { name: "Audio player" });
  await expect(player).toBeVisible({ timeout: 120_000 });
  await expect(player.getByText(/Preview: one\.wav → two\.wav/)).toBeVisible();
  await expect(player.locator(".player-time")).toHaveText(/\/ 0:[1-5]\d\.\d/, { timeout: 30_000 });
  await expect(player.getByTitle("the overlap")).toBeVisible();
  await shot(page, "03-preview");

  // the bar-line check clip plays as well
  await tracks.getByRole("button", { name: "Listen to bar line" }).first().click();
  await expect(player.getByText(/bar-line check/)).toBeVisible();
  await expect(player.locator(".player-time")).toHaveText(/\/ 0:[2-3]\d\.\d/, { timeout: 30_000 });

  // reorder: move song 3 above song 2; the plan goes stale; plan again
  await page.getByRole("button", { name: "Move three.wav up" }).click();
  await expect(page.getByText(/changed since the last plan/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Render the mix" })).toBeDisabled();
  await page.getByRole("button", { name: "Plan again" }).click();
  await expect(page.getByText(/changed since the last plan/)).toHaveCount(0);

  const order = await page.locator(".track .track-name").allTextContents();
  expect(order).toEqual(["one.wav", "three.wav", "two.wav"]);
  await expect(page.getByRole("region", { name: "Transition 1" })).toContainText("one.wav → three.wav");

  // shuffle: a different order, and the transitions are already recalculated (no "plan again" needed)
  const trackOrder = async () => (await page.locator(".tracks .track-name").allTextContents()).join(",");
  const beforeShuffle = await trackOrder();
  await page.getByRole("button", { name: /Shuffle/ }).click();
  await expect.poll(trackOrder).not.toBe(beforeShuffle);
  await expect(page.getByText(/changed since the last plan/)).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Transition 1" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Render the mix" })).toBeEnabled();

  // render and download
  await page.getByRole("button", { name: "Render the mix" }).click();
  await expect(page.getByText("Rendering the mix")).toBeVisible();
  const mixes = page.getByRole("region", { name: "Rendered mixes" });
  await expect(mixes).toBeVisible({ timeout: 200_000 });
  await shot(page, "04-rendered");
  const href = await mixes.getByRole("link", { name: "Download" }).getAttribute("href");
  const res = await request.get(href!);
  expect(res.status()).toBe(200);
  const body = await res.body();
  expect(body.subarray(0, 4).toString()).toBe("RIFF");
  expect(body.length).toBeGreaterThan(5_000_000);

  expect(errors).toEqual([]);
});

test("the project is still there after a reload, and can be deleted", async ({ page }) => {
  await page.goto("/");
  const link = page.getByRole("link", { name: /E2E set/ });
  await expect(link).toBeVisible();
  await link.click();
  await expect(page.getByRole("region", { name: "Mix timeline" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("region", { name: "Mix timeline" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Rendered mixes" })).toBeVisible();
  await page.goto("/");
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Delete E2E set" }).click();
  await expect(page.getByRole("link", { name: /E2E set/ })).toHaveCount(0);
});

test("shifting a bar line by one beat, and going back to automatic", async ({ page }) => {
  const errors = watchErrors(page);
  await newProject(page, "Bar line");
  await uploadAndAnalyse(page, ["one.wav", "two.wav"]);
  const tracks = page.getByRole("region", { name: "Tracks" });
  await expect(tracks.getByText("set by you")).toHaveCount(0);
  await page.getByRole("button", { name: "Move the bar line of one.wav one beat later" }).click();
  await expect(tracks.getByText("set by you")).toBeVisible();
  await expect(page.getByText(/changed since the last plan/)).toBeVisible();
  await page.getByRole("button", { name: "Plan again" }).click();
  await expect(page.getByText(/changed since the last plan/)).toHaveCount(0);
  await tracks.getByRole("button", { name: "auto" }).click();
  await expect(tracks.getByText("set by you")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("settings: overlap by seconds, then plan again", async ({ page }) => {
  await newProject(page, "Settings");
  await uploadAndAnalyse(page, ["one.wav", "two.wav"]);
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByLabel("by seconds").click(); // becomes checked once the server has accepted the change
  await expect(page.getByLabel("by seconds")).toBeChecked();
  const seconds = page.getByLabel("Seconds", { exact: true });
  await seconds.fill("6");
  await seconds.blur();
  await expect(page.getByText(/changed since the last plan/)).toBeVisible();
  await page.getByRole("button", { name: "Plan again" }).click();
  await expect(page.getByText(/changed since the last plan/)).toHaveCount(0);
  const text = await page.getByTestId("overlap-beats").textContent();
  const beats = Number(text!.match(/\d+/)![0]);
  expect(Math.abs((beats * 60) / 124 - 6)).toBeLessThan(2.1); // about 6 s, in whole bars
  await shot(page, "05-settings");
});

test("problems are explained, not swallowed", async ({ page }) => {
  await newProject(page, "Problems");
  const tracks = page.getByRole("region", { name: "Tracks" });
  // a file that is not audio
  await page.getByTestId("file-input").setInputFiles(audio("notes.txt"));
  await expect(tracks.getByRole("alert")).toContainText("Drop audio files");
  // a path that does not exist
  await page.getByRole("button", { name: /add by file path/ }).click();
  await page.getByLabel("File path").fill("/definitely/not/here.wav");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(tracks.getByRole("alert")).toContainText("no such file");
  // a path that does exist works, and the file is used in place
  await page.getByLabel("File path").fill(`"${audio("one.wav")}"`);
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.locator(".track .track-name")).toHaveText(["one.wav"]);
});

test("a narrow screen still works: no sideways scrolling, controls reachable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await newProject(page, "Phone");
  await uploadAndAnalyse(page, ["one.wav", "two.wav"]);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await expect(page.getByRole("button", { name: /Preview this transition/ })).toBeVisible();
  await shot(page, "06-narrow");
});
