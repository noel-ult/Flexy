export type Target = {
  os: "arch";
  architecture: "x86_64";
  label: string;
};

export const ARCH_X86_64_TARGET: Target = {
  os: "arch",
  architecture: "x86_64",
  label: "Arch Linux · x86_64",
};

export type JobStatus =
  | "uploaded"
  | "analyzing"
  | "analysis_complete"
  | "ready_to_build"
  | "queued"
  | "building"
  | "verifying"
  | "succeeded"
  | "blocked"
  | "failed"
  | "environment_unavailable"
  | "expired"
  | "unknown";

export type FindingLevel = "info" | "warning" | "blocker" | "success";

export type Finding = {
  id?: string;
  level: FindingLevel;
  title: string;
  detail: string;
  code?: string;
};

export type Dependency = {
  name: string;
  version?: string;
  mappedTo?: string;
  status: "supported" | "unsupported" | "unknown";
  detail?: string;
};

export type Executable = {
  path: string;
  kind: string;
  architecture?: string;
  interpreter?: string;
  compatible: boolean;
  detail?: string;
};

export type PackageAnalysis = {
  packageName?: string;
  packageVersion?: string;
  packageArchitecture?: string;
  executableTypes: string[];
  executables: Executable[];
  dependencies: Dependency[];
  findings: Finding[];
  blockers: Finding[];
  recipe?: {
    id: string;
    version?: string;
    supported: boolean;
    detail?: string;
  };
  limits?: {
    uploadBytes?: number;
    extractedBytes?: number;
    fileCount?: number;
  };
};

export type LogEntry = {
  timestamp?: string;
  level?: "debug" | "info" | "warning" | "error";
  message: string;
  step?: string;
};

export type VerificationCheck = {
  name: "package_creation" | "installation" | "launch" | "functionality" | string;
  status: "passed" | "failed" | "unverified" | "skipped" | "pending";
  detail: string;
};

export type Artifact = {
  kind: "package" | "report" | string;
  name: string;
  available: boolean;
  sizeBytes?: number;
};

export type JobError = {
  code?: string;
  message: string;
  detail?: string;
};

export type ConversionJob = {
  id: string;
  status: JobStatus;
  target: Target;
  createdAt?: string;
  updatedAt?: string;
  packageName?: string;
  packageVersion?: string;
  analysis?: PackageAnalysis;
  logs: LogEntry[];
  verification: VerificationCheck[];
  artifacts: Artifact[];
  error?: JobError;
  expiresAt?: string;
};

export type JobEvent = {
  type: "job" | "log" | "heartbeat" | "error" | string;
  job?: ConversionJob;
  log?: LogEntry;
  message?: string;
};

export const ACTIVE_STATUSES = new Set<JobStatus>([
  "uploaded",
  "analyzing",
  "analysis_complete",
  "queued",
  "building",
  "verifying",
]);

export const TERMINAL_STATUSES = new Set<JobStatus>([
  "ready_to_build",
  "succeeded",
  "blocked",
  "failed",
  "environment_unavailable",
  "expired",
]);

export function canStartBuild(job: ConversionJob | null): boolean {
  return Boolean(
    job &&
      (job.status === "ready_to_build" || job.status === "analysis_complete") &&
      job.analysis?.recipe?.supported &&
      job.analysis.blockers.length === 0,
  );
}

export function isBuildInProgress(job: ConversionJob | null): boolean {
  return Boolean(job && ["queued", "building", "verifying"].includes(job.status));
}

export function isAnalysisInProgress(job: ConversionJob | null): boolean {
  return Boolean(job && ["uploaded", "analyzing"].includes(job.status));
}
