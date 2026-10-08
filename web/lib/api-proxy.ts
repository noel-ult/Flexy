/** Streaming same-origin bridge. The upstream is operator configuration only. */
export async function proxyApi(request: Request): Promise<Response> {
  const failure = (status: number, code: string, message: string) =>
    Response.json({ code, message }, { status, headers: { "Cache-Control": "no-store" } });
  const configured = process.env.FLEXY_API_UPSTREAM;
  if (!configured) {
    return failure(503, "backend_not_configured",
      "The Flexy backend is not configured. Deploy the API, worker, database, queue and storage, then set FLEXY_API_UPSTREAM on the web service or route /v1 directly to the API. A frontend-only deployment cannot analyze packages.");
  }
  let upstream: URL;
  try {
    upstream = new URL(configured);
    if (!["http:", "https:"].includes(upstream.protocol) || upstream.username || upstream.password ||
        upstream.pathname !== "/" || upstream.search || upstream.hash) throw new Error("Invalid upstream");
  } catch {
    return failure(503, "backend_not_configured", "FLEXY_API_UPSTREAM must be the HTTP(S) origin of the deployed Flexy API. No request was forwarded.");
  }
  const incoming = new URL(request.url);
  // Never resolve a browser-supplied URL against the upstream (open proxy/SSRF).
  upstream.pathname = incoming.pathname;
  upstream.search = incoming.search;
  const headers = new Headers();
  for (const name of ["content-type", "accept", "x-job-capability"]) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  const options: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
    redirect: "manual",
    cache: "no-store",
    signal: AbortSignal.any([request.signal, AbortSignal.timeout(330_000)]),
  };
  if (request.method !== "GET" && request.method !== "HEAD" && request.body) {
    options.body = request.body;
    options.duplex = "half";
  }
  try {
    const response = await fetch(upstream, options);
    if (response.status >= 300 && response.status < 400) {
      await response.body?.cancel();
      return failure(502, "backend_redirect", "The API returned an unexpected redirect. Check the backend route configuration.");
    }
    const outgoing = new Headers({ "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
    for (const name of ["content-type", "content-disposition", "x-accel-buffering"]) {
      const value = response.headers.get(name);
      if (value !== null) outgoing.set(name, value);
    }
    return new Response(response.body, { status: response.status, headers: outgoing });
  } catch {
    return failure(502, "backend_unreachable", "The web service could not reach the Flexy backend. Check that the API and its dependencies are running and reachable from the web service. No compatibility result is claimed.");
  }
}
