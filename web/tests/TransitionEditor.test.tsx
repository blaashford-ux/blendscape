import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api/client";
import { TransitionEditor } from "../src/components/TransitionEditor";
import { PlayerProvider } from "../src/state/usePlayer";
import type { JobRun } from "../src/state/useJobRunner";
import { clearPeaksCache } from "../src/state/useProject";
import { peaks, project, transition } from "./fixtures";

vi.mock("../src/api/client", async (orig) => {
  const real = await orig<typeof import("../src/api/client")>();
  return {
    ...real,
    api: {
      ...real.api,
      waveform: vi.fn(),
      patchTransition: vi.fn(),
      useAlternative: vi.fn(),
      resetTransition: vi.fn(),
    },
  };
});

function setup(over: { tr?: Parameters<typeof transition>[1]; phraseBars?: number; run?: JobRun | null } = {}) {
  const p = project();
  p.plan!.transitions[0] = transition(0, over.tr);
  if (over.phraseBars) p.settings.phrase_bars = over.phraseBars;
  const onProject = vi.fn();
  const preview = { run: over.run ?? null, onStart: vi.fn(), onCancel: vi.fn() };
  render(
    <PlayerProvider>
      <TransitionEditor project={p} index={0} onProject={onProject} preview={preview} />
    </PlayerProvider>,
  );
  return { p, onProject, preview };
}

beforeEach(() => {
  clearPeaksCache();
  vi.mocked(api.waveform).mockResolvedValue(peaks());
  vi.mocked(api.patchTransition).mockImplementation(async () => project());
  vi.mocked(api.useAlternative).mockResolvedValue(project());
  vi.mocked(api.resetTransition).mockResolvedValue(project());
});

const click = (name: string | RegExp) => userEvent.click(screen.getByRole("button", { name }));

describe("TransitionEditor: what it shows", () => {
  it("names the transition and shows where each song leaves and enters", () => {
    setup();
    expect(screen.getByRole("heading", { name: /Transition 1/ })).toHaveTextContent("song0.wav → song1.wav");
    expect(screen.getByText("score 0.87")).toBeInTheDocument();
    expect(screen.getByText("A leaves at the start of its outro")).toBeInTheDocument();
    expect(screen.getByTestId("out-time")).toHaveTextContent("1:20.3");
    expect(screen.getByTestId("in-time")).toHaveTextContent("0:00.8");
    expect(screen.getByTestId("overlap-beats")).toHaveTextContent("32 beats");
    expect(screen.getByText(/16\.0 s overlap/)).toBeInTheDocument();
  });
  it("says whether the outgoing song plays to its end", () => {
    setup();
    expect(screen.getByText("song0.wav plays to its end.")).toBeInTheDocument();
  });
  it("warns when part of the outgoing song is skipped", () => {
    setup({ tr: { unplayed_tail_s: 7.66 } });
    expect(screen.getByText("The last 7.7 s of song0.wav is not played.")).toBeInTheDocument();
  });
  it("lists warnings from the planner", () => {
    setup({ tr: { warnings: ["Tempo gap 12% is over 8%: expect audible stretching."] } });
    expect(screen.getByText(/Tempo gap 12%/)).toBeInTheDocument();
  });
  it("draws both songs' waveforms", async () => {
    setup();
    expect(screen.getAllByTestId("waveform")).toHaveLength(2);
    await waitFor(() => expect(api.waveform).toHaveBeenCalledTimes(2));
  });
});

describe("TransitionEditor: moving the mix points", () => {
  it("moves where the outgoing song leaves by a bar or a phrase", async () => {
    setup();
    await click("song0.wav leaves at one bar later");
    expect(api.patchTransition).toHaveBeenLastCalledWith("p_abc123", 0, { out_beat: 164 });
    await click("song0.wav leaves at one bar earlier");
    expect(api.patchTransition).toHaveBeenLastCalledWith("p_abc123", 0, { out_beat: 156 });
    await click("song0.wav leaves at one phrase later");
    expect(api.patchTransition).toHaveBeenLastCalledWith("p_abc123", 0, { out_beat: 192 }); // 8-bar phrase = 32 beats
    await click("song0.wav leaves at one phrase earlier");
    expect(api.patchTransition).toHaveBeenLastCalledWith("p_abc123", 0, { out_beat: 128 });
  });
  it("uses the project's phrase length", async () => {
    setup({ phraseBars: 4 });
    await click("song0.wav leaves at one phrase later");
    expect(api.patchTransition).toHaveBeenLastCalledWith("p_abc123", 0, { out_beat: 176 }); // 4 bars = 16 beats
  });
  it("moves where the incoming song enters, and never below the start", async () => {
    setup();
    await click("song1.wav enters at one bar later");
    expect(api.patchTransition).toHaveBeenLastCalledWith("p_abc123", 0, { in_beat: 5 });
    await click("song1.wav enters at one bar earlier");
    expect(api.patchTransition).toHaveBeenLastCalledWith("p_abc123", 0, { in_beat: 0 }); // 1 - 4 clamps to 0
  });
  it("hands the updated project back to the parent", async () => {
    const { onProject } = setup();
    await click("song0.wav leaves at one bar later");
    await waitFor(() => expect(onProject).toHaveBeenCalledTimes(1));
  });
});

describe("TransitionEditor: overlap and style", () => {
  it("lengthens and shortens by a bar, and has presets", async () => {
    setup();
    await click("Lengthen the overlap by one bar");
    expect(api.patchTransition).toHaveBeenLastCalledWith("p_abc123", 0, { overlap_beats: 36 });
    await click("Shorten the overlap by one bar");
    expect(api.patchTransition).toHaveBeenLastCalledWith("p_abc123", 0, { overlap_beats: 28 });
    await click("16");
    expect(api.patchTransition).toHaveBeenLastCalledWith("p_abc123", 0, { overlap_beats: 16 });
    expect(screen.getByRole("button", { name: "32" })).toHaveClass("on");
  });
  it("won't shorten below one bar", () => {
    setup({ tr: { overlap_beats: 4, overlap_s: 2 } });
    expect(screen.getByRole("button", { name: "Shorten the overlap by one bar" })).toBeDisabled();
  });
  it("converts seconds to whole bars at the outgoing song's tempo", async () => {
    setup(); // song0 is 120 BPM: 8 s = 16 beats
    expect(screen.getByRole("button", { name: "Set" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Overlap in seconds"), "8");
    await userEvent.click(screen.getByRole("button", { name: "Set" }));
    expect(api.patchTransition).toHaveBeenLastCalledWith("p_abc123", 0, { overlap_beats: 16 });
  });
  it("switches style, and only offers EQ timing for the bass swap", async () => {
    setup();
    expect(screen.getByText("Fine-tune the frequency swap")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Simple crossfade"));
    expect(api.patchTransition).toHaveBeenLastCalledWith("p_abc123", 0, { style: "crossfade" });
  });
  it("hides the frequency swap controls for a crossfade", () => {
    setup({ tr: { style: "crossfade" } });
    expect(screen.queryByText("Fine-tune the frequency swap")).not.toBeInTheDocument();
    expect(screen.getByText(/No tempo matching/)).toBeInTheDocument();
  });
});

describe("TransitionEditor: frequency swap timing", () => {
  it("sends slider changes after a short pause, one request for a burst of changes", async () => {
    setup();
    await userEvent.click(screen.getByText("Fine-tune the frequency swap"));
    const slider = screen.getByLabelText(/Bass swaps at/) as HTMLInputElement;
    fireEvent.change(slider, { target: { value: "0.3" } });
    fireEvent.change(slider, { target: { value: "0.35" } });
    expect(api.patchTransition).not.toHaveBeenCalled();
    await waitFor(() => expect(api.patchTransition).toHaveBeenCalledTimes(1), { timeout: 2000 });
    expect(api.patchTransition).toHaveBeenCalledWith("p_abc123", 0, { eq: { bass_swap_center: 0.35 } });
  });
  it("keeps the start of a range before its end", async () => {
    setup();
    await userEvent.click(screen.getByText("Fine-tune the frequency swap"));
    fireEvent.change(screen.getByLabelText("Mids come in: start"), { target: { value: "0.9" } });
    await waitFor(() => expect(api.patchTransition).toHaveBeenCalled(), { timeout: 2000 });
    const sent = vi.mocked(api.patchTransition).mock.calls[0][2].eq!.mid_in!;
    expect(sent).toBeDefined();
    expect(sent[0]).toBeLessThan(sent[1]);
  });
});

describe("TransitionEditor: alternatives, reset, errors, preview", () => {
  it("lists the planner's other choices and applies one", async () => {
    setup();
    await userEvent.click(screen.getByText(/Other places the planner considered \(2\)/));
    expect(screen.getByText("B enters at a section start")).toBeInTheDocument();
    await click("Use alternative 2");
    expect(api.useAlternative).toHaveBeenCalledWith("p_abc123", 0, 1);
  });
  it("resets an edited transition to the planner's choice", async () => {
    setup({ tr: { edited: true } });
    expect(screen.getByText("edited")).toBeInTheDocument();
    await click(/Reset to the planner's choice/);
    expect(api.resetTransition).toHaveBeenCalledWith("p_abc123", 0);
  });
  it("has nothing to reset until you change something", () => {
    setup();
    expect(screen.getByRole("button", { name: /Reset to the planner's choice/ })).toBeDisabled();
    expect(screen.queryByText("edited")).not.toBeInTheDocument();
  });
  it("shows why an edit was refused, with every problem listed", async () => {
    vi.mocked(api.patchTransition).mockRejectedValue(new ApiError(422, "invalid plan", ["out_beat 999 runs past the end", "overlaps collide"]));
    const { onProject } = setup();
    await click("song0.wav leaves at one phrase later");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("invalid plan");
    expect(alert).toHaveTextContent("out_beat 999 runs past the end");
    expect(alert).toHaveTextContent("overlaps collide");
    expect(onProject).not.toHaveBeenCalled();
    // and it stays usable
    vi.mocked(api.patchTransition).mockResolvedValue(project());
    await click("song0.wav leaves at one bar later");
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });
  it("starts a preview, and can cancel one in progress", async () => {
    const { preview } = setup();
    await click(/Preview this transition/);
    expect(preview.onStart).toHaveBeenCalled();
  });
  it("disables the preview button and offers cancel while rendering", async () => {
    const { preview } = setup({ run: { label: "Rendering a preview", state: "running", message: "", fraction: 0.4 } });
    expect(screen.getByRole("button", { name: /Preview this transition/ })).toBeDisabled();
    await click("Cancel preview");
    expect(preview.onCancel).toHaveBeenCalled();
  });
});

describe("TransitionEditor: dragging markers", () => {
  // the waveforms are 600 px wide in jsdom terms; the strip shows SPAN_BEFORE (30 s) + overlap + 12 s
  const rect = { left: 0, top: 0, width: 600, height: 92, right: 600, bottom: 92, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
  beforeEach(() => {
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue(rect);
  });
  // track0: 120 bpm, offset 0.3 s, bar lines on beats 1, 5, 9, ... (see fixtures): beat k is at 0.3 + k/2 s. Strip A starts at out_time - 30 = 50.3 s
  // and spans 58 s here (16 s overlap + 42), so 600 px = 58 s.
  const pxAt = (t: number) => ((t - 50.3) / 58) * 600;
  const drag = (id: string, toT: number, opts: { shiftKey?: boolean } = {}) => {
    const h = screen.getByTestId(`handle-${id}`);
    fireEvent.pointerDown(h, { clientX: 0, pointerId: 1, ...opts });
    fireEvent.pointerMove(h, { clientX: pxAt(toT), pointerId: 1, ...opts });
    fireEvent.pointerUp(h, { clientX: pxAt(toT), pointerId: 1, ...opts });
  };

  it("has a handle on each of the four markers", () => {
    setup();
    for (const id of ["out", "end-a", "in", "end-b"]) expect(screen.getByTestId(`handle-${id}`)).toBeInTheDocument();
  });
  it("dragging 'leaves' saves the nearest bar line", async () => {
    setup();
    drag("out", 0.3 + 169 / 2 + 0.4); // just past beat 169, a bar line
    await waitFor(() => expect(api.patchTransition).toHaveBeenCalledWith("p_abc123", 0, { out_beat: 169 }));
  });
  it("snaps to single beats while Shift is held", async () => {
    setup();
    drag("out", 0.3 + 167 / 2, { shiftKey: true });
    await waitFor(() => expect(api.patchTransition).toHaveBeenCalledWith("p_abc123", 0, { out_beat: 167 }));
  });
  it("dragging 'overlap ends' sets the overlap in whole bars, at least one", async () => {
    setup();
    drag("end-a", 0.3 + (160 + 16) / 2); // 16 beats after the start
    await waitFor(() => expect(api.patchTransition).toHaveBeenCalledWith("p_abc123", 0, { overlap_beats: 16 }));
    vi.mocked(api.patchTransition).mockClear();
    drag("end-a", 0.3 + 150 / 2); // before the start: clamps to a bar
    await waitFor(() => expect(api.patchTransition).toHaveBeenCalledWith("p_abc123", 0, { overlap_beats: 4 }));
  });
  it("shows the new numbers while dragging, and saves nothing if you drop it where it was", async () => {
    setup();
    const h = screen.getByTestId("handle-out");
    fireEvent.pointerDown(h, { clientX: 0, pointerId: 1 });
    fireEvent.pointerMove(h, { clientX: pxAt(0.3 + 153 / 2), pointerId: 1 });
    expect(screen.getByText(/song0\.wav leaves at 1:16\.8/)).toBeInTheDocument();
    const back = { clientX: pxAt(0.3 + 160 / 2), pointerId: 1, shiftKey: true }; // beat 160 is where it started
    fireEvent.pointerMove(h, back);
    fireEvent.pointerUp(h, back);
    await waitFor(() => expect(screen.queryByText(/ · overlap \d+ beats/)).not.toBeInTheDocument());
    expect(api.patchTransition).not.toHaveBeenCalled();
  });
  it("a cancelled drag changes nothing", () => {
    setup();
    const h = screen.getByTestId("handle-in");
    fireEvent.pointerDown(h, { clientX: 100, pointerId: 1 });
    fireEvent.pointerCancel(h, { pointerId: 1 });
    expect(api.patchTransition).not.toHaveBeenCalled();
  });
});
