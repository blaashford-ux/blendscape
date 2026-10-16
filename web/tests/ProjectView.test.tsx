import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api/client";
import { PlayerBar } from "../src/components/PlayerBar";
import { ProjectView } from "../src/components/ProjectView";
import { PlayerProvider } from "../src/state/usePlayer";
import { clearPeaksCache } from "../src/state/useProject";
import { FakeAudio, FakeES, job } from "./helpers";
import { peaks, project, track } from "./fixtures";

vi.mock("../src/api/client", async (orig) => {
  const real = await orig<typeof import("../src/api/client")>();
  const names = ["health", "getProject", "renameProject", "waveform", "analyze", "plan", "render", "preview", "addTrackByPath", "patchTransition", "reorder", "deleteTrack", "setDownbeat", "cancelJob", "getJob"] as const;
  return { ...real, api: { ...real.api, ...Object.fromEntries(names.map((n) => [n, vi.fn()])) } };
});

const mocked = <K extends keyof typeof api>(k: K) => vi.mocked(api[k] as (...a: never[]) => unknown);

function mount() {
  return render(
    <PlayerProvider>
      <ProjectView id="p_abc123" />
      <PlayerBar />
    </PlayerProvider>,
  );
}

beforeEach(() => {
  FakeES.reset();
  clearPeaksCache();
  vi.stubGlobal("EventSource", FakeES);
  FakeAudio.install();
  mocked("health").mockResolvedValue({ ok: true, version: "0.1.0", stretch_backend: "cli", home: "/h" });
  mocked("getProject").mockResolvedValue(project());
  mocked("waveform").mockResolvedValue(peaks());
});

/** Finish the most recent job the app is following. */
const finishJob = async (result: Record<string, unknown> = {}) => {
  await act(async () => {
    FakeES.last.emit({ state: "done", message: "done", fraction: 1, result });
  });
};

describe("ProjectView: a planned project", () => {
  it("shows the tracks, the timeline and the first transition", async () => {
    mount();
    expect(await screen.findByRole("region", { name: "Mix timeline" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Project name" })).toHaveValue("Test mix"));
    expect(screen.getAllByText("song0.wav").length).toBeGreaterThan(0);
    // the first transition is selected by an effect just after the first render, so wait for it
    expect(await screen.findByRole("region", { name: "Transition 1" })).toBeInTheDocument();
  });

  it("switches transitions from the timeline", async () => {
    mount();
    await screen.findByRole("region", { name: "Mix timeline" });
    await userEvent.click(screen.getByRole("button", { name: /Transition 2: song1\.wav into song2\.wav/ }));
    expect(await screen.findByRole("region", { name: "Transition 2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Transition 2:/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("renames the project when you leave the name box", async () => {
    mocked("renameProject").mockResolvedValue(project({ name: "Friday" }));
    mount();
    const box = await screen.findByRole("textbox", { name: "Project name" });
    await userEvent.clear(box);
    await userEvent.type(box, "Friday");
    fireEvent.blur(box);
    expect(api.renameProject).toHaveBeenCalledWith("p_abc123", "Friday");
  });

  it("shows planner warnings and notes", async () => {
    mocked("getProject").mockResolvedValue(project({ plan_warnings: ["Your edit to a -> b no longer fits and was dropped."] }));
    mount();
    expect(await screen.findByText(/no longer fits and was dropped/)).toBeInTheDocument();
  });
});

describe("ProjectView: out-of-date plans and missing programs", () => {
  it("offers to plan again, and won't render a stale plan", async () => {
    mocked("getProject").mockResolvedValue(project({ plan_stale: true }));
    mocked("plan").mockResolvedValue(project());
    mount();
    expect(await screen.findByText(/changed since the last plan/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Render the mix" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Plan again" }));
    expect(api.plan).toHaveBeenCalledWith("p_abc123");
    await waitFor(() => expect(screen.queryByText(/changed since the last plan/)).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Render the mix" })).toBeEnabled();
  });

  it("shows why planning failed", async () => {
    mocked("getProject").mockResolvedValue(project({ plan_stale: true }));
    mocked("plan").mockRejectedValue(new Error("'song0.wav' is too short for a 64-beat overlap"));
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Plan again" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("too short for a 64-beat overlap");
  });

  it("explains that rendering is unavailable without Rubber Band", async () => {
    mocked("health").mockResolvedValue({ ok: true, version: "0.1.0", stretch_backend: null, home: "/h" });
    mount();
    expect(await screen.findByText(/Rubber Band program isn't installed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Render the mix" })).toBeDisabled();
  });

  it("reports a project that can't be opened", async () => {
    mocked("getProject").mockRejectedValue(new Error("not found: p_abc123"));
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not open this project: not found");
    expect(screen.getByRole("link", { name: "Back to projects" })).toHaveAttribute("href", "#/");
  });
});

describe("ProjectView: onboarding", () => {
  it("tells a new user what to do", async () => {
    mocked("getProject").mockResolvedValue(project({ tracks: [], plan: null }, 0));
    mount();
    expect(await screen.findByText("Start with two or more songs")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Render the mix" })).toBeDisabled();
  });
  it("asks for a second song", async () => {
    mocked("getProject").mockResolvedValue(project({ plan: null }, 1));
    mount();
    expect(await screen.findByText(/Add at least one more song/)).toBeInTheDocument();
  });
});

describe("ProjectView: analysing", () => {
  const needsAnalysis = () => project({ tracks: [track(0), track(1, { analysed: false, analysis: undefined })], plan: null }, 2);

  it("analyses on request, shows progress, then reloads the project", async () => {
    mocked("getProject").mockResolvedValue(needsAnalysis());
    mocked("analyze").mockResolvedValue(job());
    mount();
    await userEvent.click((await screen.findAllByRole("button", { name: "Analyse & plan" }))[0]);
    expect(api.analyze).toHaveBeenCalledWith("p_abc123", { replan: true, force: false });
    await act(async () => {
      FakeES.last.emit({ message: "analysing song1.wav (2/2)", fraction: 0.5 });
    });
    const bar = screen.getByRole("status");
    expect(within(bar).getByText("Analysing the tracks")).toBeInTheDocument();
    expect(within(bar).getByText("analysing song1.wav (2/2)")).toBeInTheDocument();
    mocked("getProject").mockResolvedValue(project());
    await finishJob({ tracks: 2, planned: true });
    expect(await screen.findByRole("region", { name: "Mix timeline" })).toBeInTheDocument();
  });

  it("can be cancelled", async () => {
    mocked("getProject").mockResolvedValue(needsAnalysis());
    mocked("analyze").mockResolvedValue(job());
    mocked("cancelJob").mockResolvedValue(job());
    mount();
    await userEvent.click((await screen.findAllByRole("button", { name: "Analyse & plan" }))[0]);
    await act(async () => {
      FakeES.last.emit({ message: "analysing", fraction: 0.2 });
    });
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.cancelJob).toHaveBeenCalledWith("j_1");
    await act(async () => {
      FakeES.last.emit({ state: "cancelled", message: "cancelled" });
    });
    expect(await screen.findByText("Cancelled.")).toBeInTheDocument();
  });

  it("shows an analysis failure without crashing", async () => {
    mocked("getProject").mockResolvedValue(needsAnalysis());
    mocked("analyze").mockResolvedValue(job());
    mount();
    await userEvent.click((await screen.findAllByRole("button", { name: "Analyse & plan" }))[0]);
    await act(async () => {
      FakeES.last.emit({ state: "error", message: "x", error: "song1.wav: could not find a steady beat" });
    });
    expect(await screen.findByText("song1.wav: could not find a steady beat")).toBeInTheDocument();
  });

  it("starts analysing by itself after tracks are added", async () => {
    mocked("getProject").mockResolvedValue(project({ tracks: [], plan: null }, 0));
    mocked("addTrackByPath").mockResolvedValue(track(0, { analysed: false, analysis: undefined }));
    mocked("analyze").mockResolvedValue(job());
    mount();
    await userEvent.click(await screen.findByRole("button", { name: /add by file path/ }));
    mocked("getProject").mockResolvedValue(project({ tracks: [track(0, { analysed: false, analysis: undefined })], plan: null }, 1));
    await userEvent.type(screen.getByLabelText("File path"), "/music/a.wav");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(api.analyze).toHaveBeenCalledWith("p_abc123", { replan: true, force: false }));
  });

  it("does not analyse again after a track was only removed", async () => {
    mocked("deleteTrack").mockResolvedValue(undefined);
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Remove song2.wav" }));
    await waitFor(() => expect(api.deleteTrack).toHaveBeenCalled());
    expect(api.analyze).not.toHaveBeenCalled();
  });
});

describe("ProjectView: previews and renders", () => {
  it("renders a preview and plays it with the overlap marked", async () => {
    mocked("preview").mockResolvedValue(job({ kind: "preview" }));
    mount();
    await userEvent.click(await screen.findByRole("button", { name: /Preview this transition/ }));
    expect(api.preview).toHaveBeenCalledWith("p_abc123", 0);
    await finishJob({ url: "/api/projects/p_abc123/previews/t1.wav", duration_s: 30, overlap_start_s: 8, overlap_end_s: 24, peak_db: -1, max_reduction_db: 0 });
    const player = await screen.findByRole("region", { name: "Audio player" });
    expect(within(player).getByText("Preview: song0.wav → song1.wav")).toBeInTheDocument();
    expect(FakeAudio.last.src).toContain("/api/projects/p_abc123/previews/t1.wav");
    await act(async () => FakeAudio.last.loaded(30)); // the browser has read the file's length
    const region = within(player).getByTitle("the overlap");
    expect(region.style.left).toBe(`${(8 / 30) * 100}%`);
  });

  it("renders the mix and lists it with a download link", async () => {
    mocked("render").mockResolvedValue(job({ kind: "render" }));
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Render the mix" }));
    expect(api.render).toHaveBeenCalledWith("p_abc123");
    expect(screen.getByRole("button", { name: "Render the mix" })).toBeDisabled(); // not while it runs
    mocked("getProject").mockResolvedValue(project({ mixes: [{ name: "Test mix-20260101-120000.wav", size: 52_000_000, modified: 1 }] }));
    await finishJob({ url: "/x", name: "Test mix-20260101-120000.wav", clipped: false });
    const list = await screen.findByRole("region", { name: "Rendered mixes" });
    expect(within(list).getByText("Test mix-20260101-120000.wav")).toBeInTheDocument();
    expect(within(list).getByRole("link", { name: "Download" })).toHaveAttribute("href", "/api/projects/p_abc123/mixes/Test%20mix-20260101-120000.wav");
    expect(within(list).getByRole("link", { name: "Download" })).toHaveAttribute("download");
  });

  it("warns if the finished mix clipped", async () => {
    mocked("render").mockResolvedValue(job({ kind: "render" }));
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Render the mix" }));
    await finishJob({ clipped: true });
    expect(await screen.findByText(/The mix clipped/)).toBeInTheDocument();
  });

  it("shows a render that failed to start", async () => {
    mocked("render").mockRejectedValue(new Error("the plan is out of date: plan again first"));
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Render the mix" }));
    expect(await screen.findByText("the plan is out of date: plan again first")).toBeInTheDocument();
  });
});
