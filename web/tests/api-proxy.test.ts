// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { proxyApi } from "@/lib/api-proxy";

describe("same-origin API proxy", () => {
  it("fails honestly without a configured backend, without issuing a request", async () => {
    vi.stubEnv("FLEXY_API_UPSTREAM", "");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const response = await proxyApi(new Request("https://web.example/v1/jobs"));
    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({ code: "backend_not_configured" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["file:///tmp/data", "http://user:secret@api:8000", "http://api:8000/path", "http://api:8000?target=other"])(
    "rejects unsafe or ambiguous upstream configuration %s", async (value) => {
      vi.stubEnv("FLEXY_API_UPSTREAM", value);
      expect((await proxyApi(new Request("https://web.example/v1/jobs"))).status).toBe(503);
    },
  );

  it("streams uploads only to the fixed backend, omitting browser credentials", async () => {
    vi.stubEnv("FLEXY_API_UPSTREAM", "http://api:8000");
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ id: "job" }, { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    const request = new Request("https://untrusted-host.example/v1/jobs?target=https://other.example", {
      method: "POST", body: "multipart-data",
      headers: { "Content-Type": "multipart/form-data; boundary=test", "X-Job-Capability": "scoped-secret",
        Cookie: "session=private", Authorization: "Bearer unrelated-secret", Host: "attacker.example" },
    });
    const response = await proxyApi(request);
    expect(response.status).toBe(202);
    const [url, options] = fetchMock.mock.calls[0] as [URL, RequestInit & { duplex: string }];
    expect(url.origin).toBe("http://api:8000");
    expect(url.pathname).toBe("/v1/jobs");
    expect(options.body).toBe(request.body);
    expect(options.duplex).toBe("half");
    const headers = new Headers(options.headers);
    expect(headers.get("x-job-capability")).toBe("scoped-secret");
    expect(headers.has("cookie")).toBe(false);
    expect(headers.has("authorization")).toBe(false);
    expect(headers.has("host")).toBe(false);
    expect(options.redirect).toBe("manual");
    expect(options.cache).toBe("no-store");
  });

  it("streams SSE and downloads without forwarding cookies or compressed lengths", async () => {
    vi.stubEnv("FLEXY_API_UPSTREAM", "http://api:8000");
    const body = "event: status\ndata: {\"status\":\"ready\"}\n\n";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { headers: {
      "Content-Type": "text/event-stream", "X-Accel-Buffering": "no", "Set-Cookie": "private=secret",
      "Content-Length": "999", "Content-Encoding": "gzip",
      "Content-Disposition": 'attachment; filename="compatibility-report.json"',
    } })));
    const response = await proxyApi(new Request("https://web.example/v1/jobs/job/events"));
    expect(await response.text()).toBe(body);
    expect(response.headers.get("x-accel-buffering")).toBe("no");
    expect(response.headers.get("content-disposition")).toContain("compatibility-report.json");
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.has("set-cookie")).toBe(false);
    expect(response.headers.has("content-length")).toBe(false);
    expect(response.headers.has("content-encoding")).toBe(false);
  });

  it("does not follow a backend redirect or leak its location", async () => {
    vi.stubEnv("FLEXY_API_UPSTREAM", "http://api:8000");
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 302, headers: { Location: "http://other/secret" } }));
    vi.stubGlobal("fetch", fetchMock);
    const response = await proxyApi(new Request("https://web.example/v1/jobs"));
    expect(response.status).toBe(502);
    expect(await response.json()).toMatchObject({ code: "backend_redirect" });
    expect(response.headers.has("location")).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("returns a readable failure without disclosing internal connection details", async () => {
    vi.stubEnv("FLEXY_API_UPSTREAM", "http://api:8000");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("credentials and internal address")));
    const response = await proxyApi(new Request("https://web.example/v1/jobs"));
    expect(response.status).toBe(502);
    expect(await response.json()).toMatchObject({ code: "backend_unreachable" });
  });

  it("propagates disconnect cancellation to the backend", async () => {
    vi.stubEnv("FLEXY_API_UPSTREAM", "http://api:8000");
    const controller = new AbortController();
    const fetchMock = vi.fn().mockResolvedValue(Response.json({}));
    vi.stubGlobal("fetch", fetchMock);
    await proxyApi(new Request("https://web.example/v1/jobs", { signal: controller.signal }));
    controller.abort();
    expect((fetchMock.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(true);
  });
});
