import {
  ARCH_X86_64_TARGET,
  type Artifact,
  type ConversionJob,
  type Dependency,
  type Executable,
  type Finding,
  type FindingLevel,
  type JobEvent,
  type JobStatus,
  type LogEntry,
  type PackageAnalysis,
  type Target,
  type VerificationCheck,
} from "@/lib/types";

// An unset build argument means same-origin, not the visitor's localhost.
const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL || "").replace(/\/$/, "");

type UnknownRecord = Record<string, unknown>;

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

function asRecord(value: unknown): UnknownRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as UnknownRecord)
    : {};
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function humaniseCode(value: unknown): string | undefined {
  const code = asString(value);
  if (!code) return undefined;
  return code
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function normaliseStatus(value: unknown): JobStatus {
  const aliases: Record<string, JobStatus> = {
    ready: "ready_to_build",
    unsupported: "blocked",
    build_queued: "queued",
  };
  if (typeof value === "string" && aliases[value]) return aliases[value];
  const known = new Set<JobStatus>([
    "uploaded",
    "analyzing",
    "analysis_complete",
    "ready_to_build",
    "queued",
    "building",
    "verifying",
    "succeeded",
    "blocked",
    "failed",
    "environment_unavailable",
    "expired",
  ]);
  return typeof value === "string" && known.has(value as JobStatus)
    ? (value as JobStatus)
    : "unknown";
}

function normaliseFinding(value: unknown, fallbackLevel: FindingLevel = "info"): Finding {
  const item = asRecord(value);
  const rawLevel = asString(item.level) ?? asString(item.severity);
  const level: FindingLevel = ["info", "warning", "blocker", "success"].includes(rawLevel ?? "")
    ? (rawLevel as FindingLevel)
    : fallbackLevel;
  return {
    id: asString(item.id) ?? asString(item.code),
    level,
    title: asString(item.title) ?? asString(item.summary) ?? humaniseCode(item.code) ?? "Compatibility finding",
    detail: asString(item.detail) ?? asString(item.message) ?? "No additional detail was provided.",
    code: asString(item.code),
  };
}

function normaliseDependency(value: unknown, mappings: Map<string, string> = new Map()): Dependency {
  const item = asRecord(value);
  const rawStatus = asString(item.status);
  const directMappedTo =
    asString(item.mapped_to) ??
    asString(item.mappedTo) ??
    asString(item.arch_package) ??
    asString(item.arch);
  const alternatives = asArray(item.alternatives).map(asRecord);
  const rawAlternatives = alternatives
    .map((alternative) => asString(alternative.name))
    .filter((name): name is string => Boolean(name));
  const mappedTo =
    directMappedTo ??
    alternatives
      .map((alternative) => {
        const name = asString(alternative.name);
        const constraint = asString(alternative.constraint) ?? "";
        return name ? mappings.get(`${name}\u0000${constraint}`) ?? mappings.get(`${name}\u0000`) : undefined;
      })
      .find((candidate): candidate is string => Boolean(candidate));
  return {
    name: (asString(item.name) ?? asString(item.package) ?? asString(item.raw) ?? rawAlternatives.join(" | ")) || "Unknown dependency",
    version: asString(item.version) ?? asString(item.constraint),
    mappedTo,
    status:
      rawStatus === "supported" || rawStatus === "unsupported"
        ? rawStatus
        : item.unsupported === true
          ? "unsupported"
          : mappedTo
            ? "supported"
            : "unknown",
    detail: asString(item.detail) ?? asString(item.reason),
  };
}

function dependencyMappings(recipe: UnknownRecord): Map<string, string> {
  const mappings = new Map<string, string>();
  for (const rawMapping of asArray(recipe.mappedDependencies ?? recipe.mapped_dependencies)) {
    const mapping = asRecord(rawMapping);
    const debian = asString(mapping.debian);
    const constraint = asString(mapping.constraint) ?? "";
    const arch = asString(mapping.arch) ?? asString(mapping.mappedTo) ?? asString(mapping.mapped_to);
    if (!debian || !arch) continue;
    mappings.set(`${debian}\u0000${constraint}`, arch);
    if (!mappings.has(`${debian}\u0000`)) mappings.set(`${debian}\u0000`, arch);
  }
  return mappings;
}

function normaliseExecutable(value: unknown): Executable {
  const item = asRecord(value);
  const kind = asString(item.kind) ?? asString(item.type) ?? "Unknown";
  const architecture = asString(item.architecture) ?? asString(item.arch);
  return {
    path: asString(item.path) ?? "Unknown path",
    kind,
    architecture,
    interpreter: asString(item.interpreter),
    // This is a static format/architecture result only; the UI separately
    // explains that it is not a desktop-compatibility claim.
    compatible: item.compatible === true || item.supported === true || (kind === "elf" && architecture === "x86_64"),
    detail: asString(item.detail) ?? asString(item.reason),
  };
}

function normaliseAnalysis(value: unknown): PackageAnalysis | undefined {
  if (!value) return undefined;
  const analysis = asRecord(value);
  const packageInfo = asRecord(analysis.package);
  const recipe = asRecord(analysis.recipe);
  const directBlockers = asArray(analysis.blockers).map((item) => normaliseFinding(item, "blocker"));
  const allFindings = asArray(analysis.findings).map((item) => normaliseFinding(item));
  const blockers = directBlockers.length
    ? directBlockers
    : allFindings.filter((finding) => finding.level === "blocker");
  const limits = asRecord(analysis.limits);
  const archive = asRecord(analysis.archive);
  const executables = asArray(analysis.executables).map(normaliseExecutable);
  const mappings = dependencyMappings(recipe);
  const explicitExecutableTypes = asArray(analysis.executable_types ?? analysis.executableTypes).filter(
    (item): item is string => typeof item === "string",
  );

  return {
    packageName:
      asString(analysis.package_name) ?? asString(analysis.packageName) ?? asString(analysis.name) ?? asString(packageInfo.name),
    packageVersion:
      asString(analysis.package_version) ?? asString(analysis.packageVersion) ?? asString(analysis.version) ?? asString(packageInfo.version),
    packageArchitecture:
      asString(analysis.package_architecture) ??
      asString(analysis.packageArchitecture) ??
      asString(analysis.architecture) ??
      asString(packageInfo.architecture),
    executableTypes: explicitExecutableTypes.length
      ? explicitExecutableTypes
      : [...new Set(executables.map((executable) => executable.kind).filter((kind) => kind !== "Unknown"))],
    executables,
    dependencies: asArray(analysis.dependencies).map((dependency) => normaliseDependency(dependency, mappings)),
    findings: allFindings,
    blockers,
    recipe: Object.keys(recipe).length
      ? {
          id: asString(recipe.id) ?? asString(recipe.selected) ?? asString(recipe.name) ?? "matched-recipe",
          version: asString(recipe.version),
          supported:
            recipe.supported === true ||
            recipe.available === true ||
            recipe.isSupported === true ||
            typeof recipe.selected === "string",
          detail: asString(recipe.detail) ?? asString(recipe.reason),
        }
      : undefined,
    limits: Object.keys(limits).length
      ? {
          uploadBytes:
            typeof limits.upload_bytes === "number"
              ? limits.upload_bytes
              : typeof limits.uploadBytes === "number"
                ? limits.uploadBytes
                : undefined,
          extractedBytes:
            typeof limits.extracted_bytes === "number"
              ? limits.extracted_bytes
              : typeof limits.extractedBytes === "number"
                ? limits.extractedBytes
                : typeof archive.expandedBytes === "number"
                  ? archive.expandedBytes
                  : undefined,
          fileCount:
            typeof limits.file_count === "number"
              ? limits.file_count
              : typeof limits.fileCount === "number"
                ? limits.fileCount
                : typeof archive.fileCount === "number"
                  ? archive.fileCount
                  : undefined,
        }
      : Object.keys(archive).length
        ? {
            extractedBytes: typeof archive.expandedBytes === "number" ? archive.expandedBytes : undefined,
            fileCount: typeof archive.fileCount === "number" ? archive.fileCount : undefined,
          }
        : undefined,
  };
}

function normaliseLog(value: unknown): LogEntry {
  const item = asRecord(value);
  const rawLevel = asString(item.level);
  return {
    timestamp: asString(item.timestamp) ?? asString(item.created_at) ?? asString(item.createdAt),
    level: rawLevel === "debug" || rawLevel === "info" || rawLevel === "warning" || rawLevel === "error" ? rawLevel : "info",
    message: asString(item.message) ?? asString(item.text) ?? "",
    step: asString(item.step),
  };
}

function normaliseVerification(value: unknown): VerificationCheck[] {
  if (Array.isArray(value)) {
    return value.map((entry) => {
      const item = asRecord(entry);
      const rawStatus = asString(item.status) ?? asString(item.state);
      return {
        name: normaliseVerificationName(asString(item.name) ?? asString(item.check) ?? "verification"),
        status: normaliseVerificationStatus(rawStatus),
        detail: asString(item.detail) ?? asString(item.message) ?? "No detail was provided.",
      };
    });
  }

  const checks = asRecord(value);
  return Object.entries(checks).map(([name, raw]) => {
    const item = asRecord(raw);
    const rawStatus = asString(item.status) ?? asString(item.state) ?? (typeof raw === "string" ? raw : undefined);
    return {
      name: normaliseVerificationName(name),
      status: normaliseVerificationStatus(rawStatus),
      detail: asString(item.detail) ?? asString(item.message) ?? "No detail was provided.",
    };
  });
}

function normaliseVerificationName(value: string): VerificationCheck["name"] {
  return value === "packageCreation" || value === "package_creation" ? "package_creation" : value;
}

function normaliseVerificationStatus(value: string | undefined): VerificationCheck["status"] {
  if (value === "not_run") return "pending";
  return ["passed", "failed", "unverified", "skipped", "pending"].includes(value ?? "")
    ? (value as VerificationCheck["status"])
    : "unverified";
}

function normaliseArtifact(value: unknown): Artifact {
  const item = asRecord(value);
  const rawKind = asString(item.kind) ?? asString(item.type) ?? "artifact";
  return {
    // The backend calls the generated Arch package an `artifact`; exposing it
    // as `package` keeps the browser action precise without changing storage.
    kind: rawKind === "artifact" ? "package" : rawKind,
    name: asString(item.name) ?? asString(item.filename) ?? "Download",
    available: item.available !== false && item.ready !== false,
    sizeBytes: typeof item.size_bytes === "number" ? item.size_bytes : typeof item.sizeBytes === "number" ? item.sizeBytes : undefined,
  };
}

/** Converts the documented snake_case API response into UI-friendly types. */
export function normaliseJob(value: unknown): ConversionJob {
  const envelope = asRecord(value);
  const job = asRecord(envelope.job ?? value);
  const target = asRecord(job.target);
  const targetOs = asString(target.os) ?? asString(job.target_os);
  const targetArchitecture = asString(target.architecture) ?? asString(target.arch) ?? asString(job.target_arch);
  const nestedError = asRecord(job.error);
  const error = Object.keys(nestedError).length
    ? nestedError
    : asString(job.error_message) || asString(job.errorMessage)
      ? {
          code: asString(job.error_code) ?? asString(job.errorCode),
          message: asString(job.error_message) ?? asString(job.errorMessage),
        }
      : {};
  const initialAnalysis = normaliseAnalysis(job.analysis ?? job.analysis_json ?? job.analysisJson);
  const rootBlockers = asArray(job.blockers ?? job.blockers_json ?? job.blockersJson).map((item) => normaliseFinding(item, "blocker"));
  const recipeId = asString(job.recipe_id) ?? asString(job.recipeId);
  const analysis = initialAnalysis
    ? {
        ...initialAnalysis,
        blockers: rootBlockers.length ? rootBlockers : initialAnalysis.blockers,
        recipe:
          initialAnalysis.recipe ??
          (recipeId
            ? {
                id: recipeId,
                supported: ["ready", "ready_to_build", "build_queued", "queued", "building", "succeeded"].includes(
                  asString(job.status) ?? "",
                ),
              }
            : undefined),
      }
    : undefined;

  return {
    id: asString(job.id) ?? "",
    status: normaliseStatus(job.status),
    target:
      targetOs === "arch" && targetArchitecture === "x86_64"
        ? ARCH_X86_64_TARGET
        : {
            os: "arch",
            architecture: "x86_64",
            label: `${targetOs ?? "Arch Linux"} · ${targetArchitecture ?? "x86_64"}`,
          },
    createdAt: asString(job.created_at) ?? asString(job.createdAt),
    updatedAt: asString(job.updated_at) ?? asString(job.updatedAt),
    packageName: asString(job.package_name) ?? asString(job.packageName),
    packageVersion: asString(job.package_version) ?? asString(job.packageVersion),
    analysis,
    logs: asArray(job.logs).map(normaliseLog),
    verification: normaliseVerification(job.verification ?? job.verification_results),
    artifacts: asArray(job.artifacts).map(normaliseArtifact),
    error: Object.keys(error).length
      ? {
          code: asString(error.code),
          message: asString(error.message) ?? "The conversion could not be completed.",
          detail: asString(error.detail),
        }
      : undefined,
    expiresAt: asString(job.expires_at) ?? asString(job.expiresAt),
  };
}

async function readError(response: Response): Promise<ApiError> {
  let payload: UnknownRecord = {};
  try {
    payload = asRecord(await response.json());
  } catch {
    // A gateway can return a non-JSON response; keep the UI message useful.
  }
  const detail = payload.detail;
  const detailText = typeof detail === "string" ? detail : asString(asRecord(detail).message);
  return new ApiError(
    asString(payload.message) ?? detailText ??
      `The API returned ${response.status}. The deployment may be missing its API route or backend services; this is not a package compatibility result.`,
    response.status,
    asString(payload.code) ?? asString(asRecord(detail).code),
  );
}

async function expectJson(response: Response): Promise<UnknownRecord> {
  if (!response.ok) throw await readError(response);
  try {
    return asRecord(await response.json());
  } catch {
    throw new ApiError("The API returned an invalid response. Check that /v1 reaches the Flexy backend, not the frontend page.", 502);
  }
}

async function apiFetch(url: string, options: RequestInit): Promise<Response> {
  try {
    return await fetch(url, options);
  } catch (error) {
    if (options.signal?.aborted) throw error;
    throw new ApiError("Cannot reach the Flexy API. Check the deployment's API route and backend services, then try again. No compatibility result was produced.", 0, "api_unreachable");
  }
}

function capabilityHeaders(capability: string): HeadersInit {
  return { "X-Job-Capability": capability };
}

export async function createJob(file: File, target: Target = ARCH_X86_64_TARGET): Promise<{ job: ConversionJob; capability: string }> {
  const form = new FormData();
  form.set("package", file);
  form.set("target_os", target.os);
  form.set("target_arch", target.architecture);

  const response = await apiFetch(`${API_BASE_URL}/v1/jobs`, {
    method: "POST",
    body: form,
    credentials: "omit",
  });
  const payload = await expectJson(response);
  const capability = asString(payload.capability) ?? asString(asRecord(payload.job).capability);
  if (!capability) throw new ApiError("The service did not return a job capability.", 502);

  const job = normaliseJob(payload);
  if (!job.id) throw new ApiError("The service did not return a job identifier.", 502);
  return { job, capability };
}

export async function getJob(jobId: string, capability: string): Promise<ConversionJob> {
  const response = await apiFetch(`${API_BASE_URL}/v1/jobs/${encodeURIComponent(jobId)}`, {
    headers: capabilityHeaders(capability),
    credentials: "omit",
    cache: "no-store",
  });
  return normaliseJob(await expectJson(response));
}

export async function startBuild(jobId: string, capability: string): Promise<ConversionJob> {
  const response = await apiFetch(`${API_BASE_URL}/v1/jobs/${encodeURIComponent(jobId)}/build`, {
    method: "POST",
    headers: capabilityHeaders(capability),
    credentials: "omit",
  });
  return normaliseJob(await expectJson(response));
}

export async function requestDownload(jobId: string, capability: string, kind: "package" | "report"): Promise<string> {
  // `artifact` is the stable API/storage name for the generated package. The
  // UI intentionally calls it a package, which is clearer to an end user.
  const apiKind = kind === "package" ? "artifact" : "report";
  const response = await apiFetch(`${API_BASE_URL}/v1/jobs/${encodeURIComponent(jobId)}/downloads/${apiKind}`, {
    method: "POST",
    headers: capabilityHeaders(capability),
    credentials: "omit",
  });
  const payload = await expectJson(response);
  const rawUrl = asString(payload.url) ?? asString(payload.download_url);
  if (!rawUrl) throw new ApiError("The service did not return a download URL.", 502);
  let url: URL;
  try {
    url = new URL(rawUrl, API_BASE_URL || window.location.origin);
  } catch {
    throw new ApiError("The service returned an invalid download URL.", 502);
  }
  const apiOrigin = new URL(API_BASE_URL || window.location.origin).origin;
  if ((url.protocol !== "https:" && url.protocol !== "http:") || url.origin !== apiOrigin || url.username || url.password) {
    throw new ApiError("The service returned an unsafe download URL.", 502);
  }
  return url.toString();
}

function parseEvent(raw: string): JobEvent | null {
  const data = raw
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim())
    .join("\n");
  const eventName = raw
    .split("\n")
    .find((line) => line.startsWith("event:"))
    ?.slice(6)
    .trim();
  if (!data) return eventName === "heartbeat" ? { type: "heartbeat" } : null;

  try {
    const payload = asRecord(JSON.parse(data));
    const candidate = payload.job ?? (payload.id ? payload : undefined);
    return {
      type: asString(payload.type) ?? eventName ?? "job",
      job: candidate ? normaliseJob(candidate) : undefined,
      log: payload.log ? normaliseLog(payload.log) : payload.message ? normaliseLog(payload) : undefined,
      message: asString(payload.message),
    };
  } catch {
    return { type: eventName ?? "message", message: data };
  }
}

/**
 * Fetch-based SSE keeps the secret capability in a header; EventSource cannot
 * safely attach that header and would require putting it into the URL.
 */
export async function streamJobEvents(
  jobId: string,
  capability: string,
  onEvent: (event: JobEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await apiFetch(`${API_BASE_URL}/v1/jobs/${encodeURIComponent(jobId)}/events`, {
    headers: { ...capabilityHeaders(capability), Accept: "text/event-stream" },
    credentials: "omit",
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw await readError(response);
  if (!response.body) throw new ApiError("Live progress is unavailable because the stream was empty.", 502);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let delimiter = buffer.indexOf("\n\n");
      while (delimiter >= 0) {
        const rawEvent = buffer.slice(0, delimiter);
        buffer = buffer.slice(delimiter + 2);
        const event = parseEvent(rawEvent);
        if (event) onEvent(event);
        delimiter = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}
