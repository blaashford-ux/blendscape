import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../src/api/client";
import { SettingsPanel } from "../src/components/SettingsPanel";
import { project } from "./fixtures";

vi.mock("../src/api/client", async (orig) => {
  const real = await orig<typeof import("../src/api/client")>();
  return { ...real, api: { ...real.api, patchSettings: vi.fn() } };
});

const setup = (patch: Partial<ReturnType<typeof project>["settings"]> = {}) => {
  const p = project();
  Object.assign(p.settings, patch);
  const onProject = vi.fn();
  render(<SettingsPanel project={p} onProject={onProject} />);
  return { onProject };
};
beforeEach(() => {
  vi.mocked(api.patchSettings).mockResolvedValue(project());
});

describe("SettingsPanel", () => {
  it("sends one setting at a time and hands the new project back", async () => {
    const { onProject } = setup();
    await userEvent.selectOptions(screen.getByLabelText("Mix points"), "latest");
    expect(api.patchSettings).toHaveBeenCalledWith("p_abc123", { mix_points: "latest" });
    await vi.waitFor(() => expect(onProject).toHaveBeenCalled());
  });
  it("changes phrase length, style, bit depth and sample rate", async () => {
    setup();
    await userEvent.selectOptions(screen.getByLabelText("Phrase length"), "4");
    expect(api.patchSettings).toHaveBeenLastCalledWith("p_abc123", { phrase_bars: 4 });
    await userEvent.selectOptions(screen.getByLabelText("Default blend style"), "crossfade");
    expect(api.patchSettings).toHaveBeenLastCalledWith("p_abc123", { style: "crossfade" });
    await userEvent.selectOptions(screen.getByLabelText("Bit depth"), "16");
    expect(api.patchSettings).toHaveBeenLastCalledWith("p_abc123", { bit_depth: 16 });
    await userEvent.selectOptions(screen.getByLabelText("Sample rate"), "48000");
    expect(api.patchSettings).toHaveBeenLastCalledWith("p_abc123", { sample_rate: 48000 });
  });
  it("switches the overlap between beats and seconds", async () => {
    setup();
    expect(screen.getByLabelText("Beats (32 = 8 bars)")).toHaveValue(32);
    await userEvent.click(screen.getByLabelText("by seconds"));
    expect(api.patchSettings).toHaveBeenLastCalledWith("p_abc123", { overlap_seconds: 8 });
  });
  it("shows the seconds field when the overlap is set by seconds, and can go back to beats", async () => {
    setup({ overlap_seconds: 6 });
    expect(screen.getByLabelText("Seconds")).toHaveValue(6);
    await userEvent.click(screen.getByLabelText("by beats"));
    expect(api.patchSettings).toHaveBeenLastCalledWith("p_abc123", { clear_overlap_seconds: true });
  });
  it("only sends a number when you finish typing, and clamps it", async () => {
    setup();
    const box = screen.getByLabelText("Beats (32 = 8 bars)");
    await userEvent.clear(box);
    await userEvent.type(box, "16");
    expect(api.patchSettings).not.toHaveBeenCalled();
    fireEvent.blur(box);
    expect(api.patchSettings).toHaveBeenLastCalledWith("p_abc123", { overlap_beats: 16 });
    const cap = screen.getByLabelText(/Most of a song left unplayed/);
    await userEvent.clear(cap);
    await userEvent.type(cap, "9999{Enter}");
    expect(api.patchSettings).toHaveBeenLastCalledWith("p_abc123", { max_cut_seconds: 120 });
  });
  it("puts the old value back if the box is emptied", async () => {
    setup();
    const box = screen.getByLabelText("Peak ceiling (dB)");
    await userEvent.clear(box);
    fireEvent.blur(box);
    expect(box).toHaveValue(-1);
    expect(api.patchSettings).not.toHaveBeenCalled();
  });
  it("toggles the limiter", async () => {
    setup();
    await userEvent.click(screen.getByLabelText(/Limiter on/));
    expect(api.patchSettings).toHaveBeenLastCalledWith("p_abc123", { limiter: { enabled: false } });
  });
  it("shows the server's explanation when a value is refused", async () => {
    vi.mocked(api.patchSettings).mockRejectedValue(new Error("overlap_seconds must be at most 60"));
    setup();
    await userEvent.selectOptions(screen.getByLabelText("Mix points"), "latest");
    expect(await screen.findByRole("alert")).toHaveTextContent("overlap_seconds must be at most 60");
  });
});
