import { describe, expect, it, vi } from "vitest";
import { normaliseJob, streamJobEvents } from "@/lib/api";

describe("job API adapter", () => {
  it("normalises the backend's ready status, supported recipe, and package artifact", () => {
    const job = normaliseJob({
      id: "opaque-job-id",
      status: "ready",
      target_os: "arch",
      target_arch: "x86_64",
      analysis: {
        package: { name: "flexy-demo", version: "1.0.0", architecture: "amd64" },
        executables: [{ path: "usr/bin/flexy-demo", kind: "elf", architecture: "x86_64", compatible: true }],
        dependencies: [{ raw: "libc6 (>= 2.36)", constraint: ">= 2.36", status: "supported", arch: "glibc" }],
        recipe: { id: "flexy-demo-1", supported: true },
        archive: { expandedBytes: 2345, fileCount: 4 },
        findings: [],
      },
      verification: { packageCreation: { state: "not_run" } },
      artifacts: [{ kind: "artifact", name: "flexy-demo-1.0.0-1-x86_64.pkg.tar.zst", available: true }],
    });

    expect(job.status).toBe("ready_to_build");
    expect(job.target.label).toBe("Arch Linux · x86_64");
    expect(job.analysis).toMatchObject({ packageName: "flexy-demo", packageArchitecture: "amd64" });
    expect(job.analysis?.dependencies[0]).toMatchObject({ mappedTo: "glibc", status: "supported" });
    expect(job.analysis?.recipe?.supported).toBe(true);
    expect(job.artifacts[0]).toMatchObject({ kind: "package", available: true });
    expect(job.verification[0]).toMatchObject({ name: "package_creation", status: "pending" });
  });

  it("uses a header-authenticated fetch stream instead of exposing a capability in a URL", async () => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(`event: status
data: {"id":"job-1","status":"unsupported"}

`));
        controller.close();
      },
    });
    const fetchMock = vi.fn().mockResolvedValue(new Response(body, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const events: unknown[] = [];

    await streamJobEvents("job-1", "secret-capability", (event) => events.push(event), new AbortController().signal);

    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/v1/jobs/job-1/events");
    expect(url).not.toContain("secret-capability");
    expect(new Headers(options.headers).get("X-Job-Capability")).toBe("secret-capability");
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ type: "status", job: { status: "blocked" } });
  });
});
