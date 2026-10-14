import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api/client";
import App from "../src/App";
import { JobBar } from "../src/components/JobBar";
import { MixTimeline } from "../src/components/MixTimeline";
import { PlayerBar } from "../src/components/PlayerBar";
import { ProjectList } from "../src/components/ProjectList";
import { PlayerProvider, usePlayer } from "../src/state/usePlayer";
import { clearPeaksCache } from "../src/state/useProject";
import { FakeAudio } from "./helpers";
import { peaks, project } from "./fixtures";

vi.mock("../src/api/client", async (orig) => {
  const real = await orig<typeof import("../src/api/client")>();
  const names = ["listProjects", "createProject", "deleteProject", "waveform", "health", "getProject"] as const;
  return { ...real, api: { ...real.api, ...Object.fromEntries(names.map((n) => [n, vi.fn()])) } };
});
const m = <K extends keyof typeof api>(k: K) => vi.mocked(api[k] as (...a: never[]) => unknown);

beforeEach(() => {
  clearPeaksCache();
  m("waveform").mockResolvedValue(peaks());
  window.location.hash = "";
});

describe("MixTimeline", () => {
  it("shows every track and marks each overlap", () => {
    const onSelect = vi.fn();
    render(<MixTimeline project={project()} selected={0} onSelect={onSelect} />);
    expect(screen.getByText(/3 tracks · 5:00 · 2 transitions/)).toBeInTheDocument();
    expect(screen.getByText("1. song0.wav")).toBeInTheDocument();
    expect(screen.getByText("3. song2.wav")).toBeInTheDocument();
    const first = screen.getByRole("button", { name: /Transition 1: song0\.wav into song1\.wav, 1:20\.3 to 1:36\.3/ });
    expect(first).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: /Transition 2:/ }));
    expect(onSelect).toHaveBeenCalledWith(1);
  });
  it("zooms", () => {
    const { container } = render(<MixTimeline project={project()} selected={null} onSelect={() => undefined} />);
    const inner = container.querySelector(".timeline-inner") as HTMLElement;
    fireEvent.change(screen.getByLabelText("Timeline zoom"), { target: { value: "10" } });
    expect(inner.style.width).toBe("3000px"); // 300 s at 10 px/s
    fireEvent.change(screen.getByLabelText("Timeline zoom"), { target: { value: "2" } });
    expect(inner.style.width).toBe("600px");
  });
  it("puts each track where the plan says", () => {
    const { container } = render(<MixTimeline project={project()} selected={null} onSelect={() => undefined} />);
    fireEvent.change(screen.getByLabelText("Timeline zoom"), { target: { value: "10" } });
    const lanes = [...container.querySelectorAll<HTMLElement>(".lane")];
    expect(lanes.map((l) => l.style.left)).toEqual(["0px", "803px", "1603px"]); // 0, 80.3 s, 160.3 s at 10 px/s
  });
});

describe("JobBar", () => {
  it("shows nothing without a job", () => {
    const { container } = render(<JobBar run={null} onCancel={() => undefined} onDismiss={() => undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
  it("shows progress while running and a cancel button", async () => {
    const onCancel = vi.fn();
    render(<JobBar run={{ label: "Rendering", state: "running", message: "limiting", fraction: 0.5 }} onCancel={onCancel} onDismiss={() => undefined} />);
    expect(screen.getByRole("progressbar", { name: "Rendering progress" })).toHaveValue(50);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
  });
  it("is indeterminate when the fraction is unknown", () => {
    render(<JobBar run={{ label: "Rendering", state: "running", message: "", fraction: null }} onCancel={() => undefined} onDismiss={() => undefined} />);
    expect(screen.getByRole("progressbar")).not.toHaveAttribute("value");
  });
  it("shows errors and can be dismissed", async () => {
    const onDismiss = vi.fn();
    render(<JobBar run={{ label: "Rendering", state: "error", message: "", fraction: null, error: "disk full" }} onCancel={() => undefined} onDismiss={onDismiss} />);
    expect(screen.getByText("disk full")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onDismiss).toHaveBeenCalled();
  });
});

describe("player", () => {
  function Harness() {
    const p = usePlayer();
    return (
      <>
        <button onClick={() => p.play("/a.wav", "Song A", { region: { start: 1, end: 2 } })}>play a</button>
        <button onClick={() => p.play("/b.wav", "Song B")}>play b</button>
        <PlayerBar />
      </>
    );
  }
  it("is hidden until something plays, then shows the title and closes", async () => {
    render(
      <PlayerProvider>
        <Harness />
      </PlayerProvider>,
    );
    expect(screen.queryByRole("region", { name: "Audio player" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("play a"));
    const bar = screen.getByRole("region", { name: "Audio player" });
    expect(within(bar).getByText("Song A")).toBeInTheDocument();
    await userEvent.click(screen.getByText("play b"));
    expect(within(bar).getByText("Song B")).toBeInTheDocument(); // one player: the new sound replaces the old
    await userEvent.click(within(bar).getByRole("button", { name: "Close player" }));
    expect(screen.queryByRole("region", { name: "Audio player" })).not.toBeInTheDocument();
  });
  it("plays once the browser has loaded the file, seeks, pauses and shows the overlap region", async () => {
    FakeAudio.install();
    render(
      <PlayerProvider>
        <Harness />
      </PlayerProvider>,
    );
    await userEvent.click(screen.getByText("play a"));
    const a = FakeAudio.last;
    expect(a.src).toMatch(/\/a\.wav$/);
    expect(a.playCalls).toBe(0); // waits for the metadata
    await act(async () => a.loaded(20));
    expect(a.playCalls).toBe(1);
    expect(screen.getByText("0:00.0 / 0:20.0")).toBeInTheDocument();
    expect(screen.getByTitle("the overlap")).toHaveStyle({ left: "5%" });
    fireEvent.change(screen.getByLabelText("Seek"), { target: { value: "7.5" } });
    expect(a.currentTime).toBe(7.5);
    await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(a.paused).toBe(true);
    await userEvent.click(screen.getByText("play a")); // same file again: no reload, plays from where asked
    expect(a.loadCalls).toBe(1);
    await act(async () => a.fail());
    expect(screen.getByText("This audio could not be played.")).toBeInTheDocument();
  });
  it("needs its provider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(() => render(<PlayerBar />)).toThrow(/PlayerProvider/);
    spy.mockRestore();
  });
});

describe("ProjectList", () => {
  const summary = { id: "p_one", name: "Friday set", created: 1, updated: 1_700_000_000, tracks: 3 };
  it("lists projects with links, and explains an empty list", async () => {
    m("listProjects").mockResolvedValue([summary]);
    const { unmount } = render(<ProjectList />);
    const link = await screen.findByRole("link", { name: /Friday set/ });
    expect(link).toHaveAttribute("href", "#/p/p_one");
    expect(link).toHaveTextContent("3 tracks");
    unmount();
    m("listProjects").mockResolvedValue([]);
    render(<ProjectList />);
    expect(await screen.findByText(/Nothing here yet/)).toBeInTheDocument();
  });
  it("creates a project and opens it", async () => {
    m("listProjects").mockResolvedValue([]);
    m("createProject").mockResolvedValue(project({ id: "p_new" }));
    render(<ProjectList />);
    await userEvent.type(await screen.findByLabelText("New project name"), "Saturday");
    await userEvent.click(screen.getByRole("button", { name: "New mix" }));
    expect(api.createProject).toHaveBeenCalledWith("Saturday");
    await waitFor(() => expect(window.location.hash).toBe("#/p/p_new"));
  });
  it("deletes only after you confirm", async () => {
    m("listProjects").mockResolvedValue([summary]);
    m("deleteProject").mockResolvedValue(undefined);
    const confirm = vi.spyOn(window, "confirm");
    render(<ProjectList />);
    const del = await screen.findByRole("button", { name: "Delete Friday set" });
    confirm.mockReturnValueOnce(false);
    await userEvent.click(del);
    expect(api.deleteProject).not.toHaveBeenCalled();
    confirm.mockReturnValueOnce(true);
    await userEvent.click(del);
    await waitFor(() => expect(api.deleteProject).toHaveBeenCalledWith("p_one"));
  });
  it("shows a connection problem with a retry", async () => {
    m("listProjects").mockRejectedValueOnce(new Error("Failed to fetch")).mockResolvedValue([]);
    render(<ProjectList />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to fetch");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(/Nothing here yet/)).toBeInTheDocument();
  });
});

describe("App routing", () => {
  it("shows the project list at the root and a project at #/p/<id>", async () => {
    m("listProjects").mockResolvedValue([]);
    m("health").mockResolvedValue({ ok: true, version: "0", stretch_backend: "cli", home: "" });
    m("getProject").mockResolvedValue(project());
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Your mixes" })).toBeInTheDocument();
    await act(async () => {
      window.location.hash = "#/p/p_abc123";
    });
    expect(await screen.findByRole("textbox", { name: "Project name" })).toHaveValue("Test mix");
    await act(async () => {
      window.location.hash = "";
    });
    expect(await screen.findByRole("heading", { name: "Your mixes" })).toBeInTheDocument();
  });
  it("ignores malformed project ids", async () => {
    m("listProjects").mockResolvedValue([]);
    window.location.hash = "#/p/../../etc";
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Your mixes" })).toBeInTheDocument();
    expect(api.getProject).not.toHaveBeenCalled();
  });
  it("recovers from a crash inside a page", async () => {
    m("listProjects").mockImplementation(() => {
      throw new Error("kaboom");
    });
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(<App />);
    expect(await screen.findByText("Something broke")).toBeInTheDocument();
    expect(screen.getByText("kaboom")).toBeInTheDocument();
    spy.mockRestore();
  });
});
