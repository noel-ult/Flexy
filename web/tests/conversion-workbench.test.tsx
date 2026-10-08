import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConversionWorkbench } from "@/components/conversion-workbench";
import { createJob, streamJobEvents } from "@/lib/api";
import { ARCH_X86_64_TARGET, type ConversionJob, type JobEvent } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...original,
    createJob: vi.fn(),
    streamJobEvents: vi.fn(),
  };
});

const uploadedJob: ConversionJob = {
  id: "test-job-id",
  status: "ready_to_build",
  target: ARCH_X86_64_TARGET,
  logs: [],
  verification: [],
  artifacts: [],
};

async function uploadPackage() {
  const user = userEvent.setup();
  await user.upload(
    screen.getByLabelText("Choose a .deb package"),
    new File(["test fixture"], "fixture.deb", { type: "application/octet-stream" }),
  );
  await user.click(screen.getByRole("button", { name: /Analyze package/i }));
  await waitFor(() => expect(streamJobEvents).toHaveBeenCalled());
}

describe("ConversionWorkbench", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    vi.stubGlobal("fetch", vi.fn());
    vi.mocked(createJob).mockReset().mockResolvedValue({
      job: uploadedJob,
      capability: "test-session-capability",
    });
    vi.mocked(streamJobEvents).mockReset().mockResolvedValue(undefined);
  });

  it("makes the service boundary and compatibility limitations visible before upload", () => {
    render(<ConversionWorkbench />);

    expect(screen.getByRole("heading", { name: /Turn a supported Debian package/i })).toBeInTheDocument();
    expect(screen.getByText(/Changing package formats cannot make Windows or macOS binaries run on Linux/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Analyze package/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Drop a .deb file here/i })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Target operating system" })).toHaveValue("arch");
    expect(screen.getByRole("combobox", { name: "Target CPU architecture" })).toHaveValue("x86_64");
  });

  it("rejects a non-Debian upload before it reaches the API", async () => {
    // Bypass the file picker's accept filter to exercise application validation,
    // as a dropped file or a manually overridden picker can do in a browser.
    const user = userEvent.setup({ applyAccept: false });
    render(<ConversionWorkbench />);
    const input = screen.getByLabelText("Choose a .deb package");

    await user.upload(input, new File(["not a package"], "application.exe", { type: "application/octet-stream" }));

    expect(screen.getByRole("alert")).toHaveTextContent(/ending in .deb/i);
    expect(globalThis.fetch).not.toHaveBeenCalled();
    expect(createJob).not.toHaveBeenCalled();
  });

  it("appends live logs once and ignores events without a log", async () => {
    render(<ConversionWorkbench />);
    await uploadPackage();
    const onEvent = vi.mocked(streamJobEvents).mock.calls[0][2];
    const log = {
      timestamp: "2026-01-01T12:00:00Z",
      message: "Inspection finished without running package scripts.",
    };

    act(() => {
      onEvent({ type: "log", log });
      onEvent({ type: "log", log });
      onEvent({ type: "heartbeat" });
      onEvent({ type: "log" });
    });

    const output = screen.getByLabelText("Build log output");
    expect(within(output).getAllByText(log.message)).toHaveLength(1);
    expect(screen.getByText("1 event")).toBeInTheDocument();
  });

  it("retains the validated log when the event object changes before React applies the update", async () => {
    render(<ConversionWorkbench />);
    await uploadPackage();
    const onEvent = vi.mocked(streamJobEvents).mock.calls[0][2];
    const log = { message: "Stable event snapshot" };
    const event: JobEvent = { type: "log", log };

    act(() => {
      // Batch a first update so the next state callback can be deferred.
      onEvent({ type: "log", log: { message: "Earlier event" } });
      onEvent(event);
      event.log = undefined;
    });

    expect(within(screen.getByLabelText("Build log output")).getByText(log.message)).toBeInTheDocument();
    expect(screen.getByText("2 events")).toBeInTheDocument();
  });

  it("ignores a late log after its job has been cleared and aborts the stream", async () => {
    render(<ConversionWorkbench />);
    await uploadPackage();
    const [, , onEvent, signal] = vi.mocked(streamJobEvents).mock.calls[0];
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Start another package/i }));

    expect(signal.aborted).toBe(true);
    act(() => onEvent({ type: "log", log: { message: "Late log" } }));
    expect(screen.queryByText("Late log")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Analyze package/i })).toBeDisabled();
  });
});
