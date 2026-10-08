import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConversionWorkbench } from "@/components/conversion-workbench";

describe("ConversionWorkbench", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    vi.stubGlobal("fetch", vi.fn());
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
    const user = userEvent.setup();
    render(<ConversionWorkbench />);
    const input = screen.getByLabelText("Choose a .deb package");

    await user.upload(input, new File(["not a package"], "application.exe", { type: "application/octet-stream" }));

    expect(screen.getByRole("alert")).toHaveTextContent(/ending in .deb/i);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});
