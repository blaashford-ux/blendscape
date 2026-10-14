import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, uploadTrack } from "../src/api/client";
import { TrackList } from "../src/components/TrackList";
import { PlayerProvider } from "../src/state/usePlayer";
import { project, track } from "./fixtures";

vi.mock("../src/api/client", async (orig) => {
  const real = await orig<typeof import("../src/api/client")>();
  return {
    ...real,
    uploadTrack: vi.fn(),
    api: {
      ...real.api,
      reorder: vi.fn(),
      deleteTrack: vi.fn(),
      setDownbeat: vi.fn(),
      addTrackByPath: vi.fn(),
    },
  };
});

const setup = (p = project()) => {
  const onProject = vi.fn();
  const onTracksChanged = vi.fn();
  render(
    <PlayerProvider>
      <TrackList project={p} onProject={onProject} onTracksChanged={onTracksChanged} />
    </PlayerProvider>,
  );
  return { onProject, onTracksChanged, p };
};

beforeEach(() => {
  vi.mocked(api.reorder).mockResolvedValue(project());
  vi.mocked(api.deleteTrack).mockResolvedValue(undefined);
  vi.mocked(api.setDownbeat).mockResolvedValue(project());
  vi.mocked(api.addTrackByPath).mockResolvedValue(track(9));
});

describe("TrackList", () => {
  it("lists the tracks in order with tempo and bar-line confidence", () => {
    setup();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(within(items[0]).getByText("song0.wav")).toBeInTheDocument();
    expect(within(items[1]).getByText(/121\.0 BPM/)).toBeInTheDocument();
    expect(within(items[0]).getByText(/sure 90%/)).toBeInTheDocument();
  });

  it("moves a track with the arrow buttons", async () => {
    const { onProject } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Move song1.wav up" }));
    expect(api.reorder).toHaveBeenCalledWith("p_abc123", ["t_1", "t_0", "t_2"]);
    await waitFor(() => expect(onProject).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: "Move song0.wav down" }));
    expect(api.reorder).toHaveBeenLastCalledWith("p_abc123", ["t_1", "t_0", "t_2"]);
  });

  it("cannot move the first track up or the last one down", () => {
    setup();
    expect(screen.getByRole("button", { name: "Move song0.wav up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move song2.wav down" })).toBeDisabled();
  });

  it("reorders by dragging one track onto another", async () => {
    setup();
    const items = screen.getAllByRole("listitem");
    const dt = { setData: vi.fn(), effectAllowed: "", types: [] as string[], files: [] as File[] };
    fireEvent.dragStart(items[2], { dataTransfer: dt });
    fireEvent.dragOver(items[0], { dataTransfer: dt });
    fireEvent.drop(items[0], { dataTransfer: dt });
    await waitFor(() => expect(api.reorder).toHaveBeenCalledWith("p_abc123", ["t_2", "t_0", "t_1"]));
  });

  it("does nothing when a track is dropped where it already is", async () => {
    setup();
    const items = screen.getAllByRole("listitem");
    const dt = { setData: vi.fn(), effectAllowed: "", types: [] as string[], files: [] as File[] };
    fireEvent.dragStart(items[1], { dataTransfer: dt });
    fireEvent.drop(items[1], { dataTransfer: dt });
    await new Promise((r) => setTimeout(r, 20));
    expect(api.reorder).not.toHaveBeenCalled();
  });

  it("removes a track and tells the parent", async () => {
    const { onTracksChanged } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Remove song1.wav" }));
    expect(api.deleteTrack).toHaveBeenCalledWith("p_abc123", "t_1");
    await waitFor(() => expect(onTracksChanged).toHaveBeenCalledWith(false));
  });

  it("shifts the bar line one beat at a time, wrapping around the bar", async () => {
    setup(); // detected downbeat is beat 1
    await userEvent.click(screen.getAllByRole("button", { name: /one beat earlier/ })[0]);
    expect(api.setDownbeat).toHaveBeenLastCalledWith("p_abc123", "t_0", 0);
    await userEvent.click(screen.getAllByRole("button", { name: /one beat later/ })[0]);
    expect(api.setDownbeat).toHaveBeenLastCalledWith("p_abc123", "t_0", 2);
  });

  it("wraps from beat 0 back to 3", async () => {
    const t = track(0);
    t.analysis!.downbeat = 0;
    setup(project({ tracks: [t, track(1)] }, 2));
    await userEvent.click(screen.getAllByRole("button", { name: /one beat earlier/ })[0]);
    expect(api.setDownbeat).toHaveBeenLastCalledWith("p_abc123", "t_0", 3);
  });

  it("flags an unsure bar line and offers to go back to automatic once it was set by hand", async () => {
    const unsure = track(0);
    unsure.analysis!.downbeat_confidence = 0.3;
    const manual = track(1);
    manual.analysis!.downbeat_manual = true;
    setup(project({ tracks: [unsure, manual] }, 2));
    expect(screen.getByText(/Unsure where the bars start/)).toBeInTheDocument();
    expect(screen.getByText("set by you")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "auto" }));
    expect(api.setDownbeat).toHaveBeenCalledWith("p_abc123", "t_1", null);
  });

  it("explains a track that is not analysed yet", () => {
    setup(project({ tracks: [track(0, { analysed: false, analysis: undefined })] }, 1));
    expect(screen.getByText(/not analysed yet/)).toBeInTheDocument();
  });

  it("warns about a file that went missing", () => {
    setup(project({ tracks: [track(0, { missing: true })] }, 1));
    expect(screen.getByText(/no longer at its original location/)).toBeInTheDocument();
  });

  it("uploads chosen files one after another and then asks the parent to analyse", async () => {
    const order: string[] = [];
    vi.mocked(uploadTrack).mockImplementation(async (_id, f, onProgress) => {
      onProgress?.(0.5);
      order.push(f.name);
      return track(5);
    });
    const { onTracksChanged } = setup(project({ tracks: [] }, 0));
    const input = screen.getByTestId("file-input") as HTMLInputElement;
    await userEvent.upload(input, [new File(["a"], "one.wav", { type: "audio/wav" }), new File(["b"], "two.wav", { type: "audio/wav" })]);
    await waitFor(() => expect(onTracksChanged).toHaveBeenCalledWith(true));
    expect(order).toEqual(["one.wav", "two.wav"]);
    expect(onTracksChanged).toHaveBeenCalledTimes(1);
  });

  it("keeps going when one upload fails and shows why", async () => {
    vi.mocked(uploadTrack)
      .mockRejectedValueOnce(new Error("bad.wav is not an audio file"))
      .mockResolvedValueOnce(track(5));
    const { onTracksChanged } = setup(project({ tracks: [] }, 0));
    await userEvent.upload(screen.getByTestId("file-input"), [new File(["a"], "bad.wav", { type: "audio/wav" }), new File(["b"], "good.wav", { type: "audio/wav" })]);
    expect(await screen.findByText("bad.wav is not an audio file")).toBeInTheDocument();
    await waitFor(() => expect(onTracksChanged).toHaveBeenCalledWith(true));
  });

  it("refuses files that are clearly not audio", async () => {
    const { onTracksChanged } = setup(project({ tracks: [] }, 0));
    const input = screen.getByTestId("file-input");
    fireEvent.change(input, { target: { files: [new File(["x"], "notes.txt", { type: "text/plain" })] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Drop audio files");
    expect(uploadTrack).not.toHaveBeenCalled();
    expect(onTracksChanged).not.toHaveBeenCalled();
  });

  it("accepts files dropped onto the panel", async () => {
    vi.mocked(uploadTrack).mockResolvedValue(track(5));
    const { onTracksChanged } = setup(project({ tracks: [] }, 0));
    const panel = screen.getByRole("region", { name: "Tracks" });
    const f = new File(["a"], "dropped.flac", { type: "audio/flac" });
    fireEvent.dragOver(panel, { dataTransfer: { types: ["Files"], files: [f] } });
    expect(panel).toHaveClass("file-drag");
    fireEvent.drop(panel, { dataTransfer: { types: ["Files"], files: [f] } });
    await waitFor(() => expect(onTracksChanged).toHaveBeenCalledWith(true));
    expect(vi.mocked(uploadTrack).mock.calls[0][1].name).toBe("dropped.flac");
  });

  it("adds a file by path, stripping quotes pasted from Explorer", async () => {
    const { onTracksChanged } = setup(project({ tracks: [] }, 0));
    await userEvent.click(screen.getByRole("button", { name: /add by file path/ }));
    await userEvent.type(screen.getByLabelText("File path"), '"S:\\music\\one.wav"');
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(api.addTrackByPath).toHaveBeenCalledWith("p_abc123", "S:\\music\\one.wav");
    await waitFor(() => expect(onTracksChanged).toHaveBeenCalledWith(true));
  });

  it("shows the server's reason when a path can't be added", async () => {
    vi.mocked(api.addTrackByPath).mockRejectedValue(new Error("no such file: nope.wav"));
    setup(project({ tracks: [] }, 0));
    await userEvent.click(screen.getByRole("button", { name: /add by file path/ }));
    await userEvent.type(screen.getByLabelText("File path"), "nope.wav");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("no such file: nope.wav");
  });

  it("tells a new user what to do first", () => {
    setup(project({ tracks: [] }, 0));
    expect(screen.getByText(/Add the songs you want to blend/)).toBeInTheDocument();
  });
});
