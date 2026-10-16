import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api/client";
import { MachineSettings } from "../src/components/MachineSettings";
import type { AppConfig } from "../src/api/types";

vi.mock("../src/api/client", async (orig) => {
  const real = await orig<typeof import("../src/api/client")>();
  return { ...real, api: { ...real.api, config: vi.fn(), setConfig: vi.fn() } };
});

const cfg = (over: Partial<AppConfig> = {}): AppConfig => ({
  rubberband: null,
  rubberband_in_use: null,
  rubberband_version: null,
  export_dir: null,
  ui_bundled: true,
  ...over,
});

beforeEach(() => {
  vi.mocked(api.config).mockResolvedValue(cfg());
  vi.mocked(api.setConfig).mockResolvedValue(cfg());
});

describe("MachineSettings", () => {
  it("says Rubber Band is missing and saves a path without a restart", async () => {
    const onChanged = vi.fn();
    render(<MachineSettings onChanged={onChanged} />);
    expect(await screen.findByText(/Not found yet/)).toBeInTheDocument();
    vi.mocked(api.setConfig).mockResolvedValue(cfg({ rubberband: "C:\\rb\\rubberband.exe", rubberband_in_use: "C:\\rb\\rubberband.exe", rubberband_version: "3.3.0" }));
    await userEvent.type(screen.getByLabelText("Rubber Band program"), "C:\\rb\\rubberband.exe");
    await userEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);
    expect(api.setConfig).toHaveBeenCalledWith({ rubberband: "C:\\rb\\rubberband.exe" });
    expect(await screen.findByText(/In use: C:\\rb\\rubberband.exe \(3\.3\.0\)/)).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });
  it("saves and clears the export folder", async () => {
    render(<MachineSettings />);
    await screen.findByLabelText("Export folder");
    await userEvent.type(screen.getByLabelText("Export folder"), "D:\\Mixes");
    await userEvent.click(screen.getAllByRole("button", { name: "Save" })[1]);
    expect(api.setConfig).toHaveBeenCalledWith({ export_dir: "D:\\Mixes" });
    vi.mocked(api.setConfig).mockResolvedValue(cfg());
    vi.mocked(api.config).mockResolvedValue(cfg({ export_dir: "D:\\Mixes" }));
  });
  it("shows why a path was refused", async () => {
    vi.mocked(api.setConfig).mockRejectedValue(new ApiError(422, "no such file: nope", []));
    render(<MachineSettings />);
    await screen.findByLabelText("Rubber Band program");
    await userEvent.type(screen.getByLabelText("Rubber Band program"), "nope");
    await userEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);
    expect(await screen.findByRole("alert")).toHaveTextContent("no such file: nope");
  });
});
