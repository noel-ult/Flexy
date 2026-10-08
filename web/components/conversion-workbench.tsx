"use client";

import {
  type ChangeEvent,
  type DragEvent,
  type KeyboardEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ApiError,
  createJob,
  getJob,
  requestDownload,
  startBuild,
  streamJobEvents,
} from "@/lib/api";
import {
  ARCH_X86_64_TARGET,
  ACTIVE_STATUSES,
  canStartBuild,
  isAnalysisInProgress,
  isBuildInProgress,
  type ConversionJob,
  type Finding,
  type FindingLevel,
  type LogEntry,
  type VerificationCheck,
} from "@/lib/types";
import {
  forgetJobCapability,
  loadJobCapability,
  loadLatestJobId,
  saveJobCapability,
} from "@/lib/session";

const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

type BusyAction = "upload" | "build" | "package" | "report" | null;

const STATUS_LABELS: Record<ConversionJob["status"], string> = {
  uploaded: "Upload received",
  analyzing: "Analyzing safely",
  analysis_complete: "Analysis complete",
  ready_to_build: "Ready to build",
  queued: "Build queued",
  building: "Building in isolation",
  verifying: "Verifying package",
  succeeded: "Build complete",
  blocked: "Unsupported",
  failed: "Failed",
  environment_unavailable: "Build environment unavailable",
  expired: "Expired",
  unknown: "Updating",
};

const VERIFICATION_ORDER: Array<VerificationCheck["name"]> = [
  "package_creation",
  "installation",
  "launch",
  "functionality",
];

const VERIFICATION_LABELS: Record<string, string> = {
  package_creation: "Package creation",
  installation: "Installation verification",
  launch: "Launch verification",
  functionality: "Functionality testing",
};

const WORKFLOW_STEPS = [
  { number: 1, label: "Upload", detail: "Choose a .deb package" },
  { number: 2, label: "Inspect", detail: "Review compatibility" },
  { number: 3, label: "Build", detail: "Verify and download" },
] as const;

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403) {
      return "This browser session no longer has access to that job. Upload the package again to start a new job.";
    }
    if (error.status === 413) {
      return "That package is larger than the service upload limit of 50 MiB.";
    }
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "Something unexpected happened. Please try again.";
}

function mergeJob(current: ConversionJob | null, incoming: ConversionJob): ConversionJob {
  if (!current || current.id !== incoming.id) return incoming;
  return {
    ...current,
    ...incoming,
    analysis: incoming.analysis ?? current.analysis,
    logs: incoming.logs.length ? incoming.logs : current.logs,
    verification: incoming.verification.length ? incoming.verification : current.verification,
    artifacts: incoming.artifacts.length ? incoming.artifacts : current.artifacts,
    error: incoming.error ?? current.error,
  };
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function formatDate(isoDate?: string): string | undefined {
  if (!isoDate) return undefined;
  const date = new Date(isoDate);
  return Number.isNaN(date.getTime())
    ? undefined
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function statusTone(status: ConversionJob["status"]): "neutral" | "working" | "success" | "danger" | "warning" {
  if (status === "succeeded") return "success";
  if (status === "blocked" || status === "failed" || status === "environment_unavailable" || status === "expired") return "danger";
  if (status === "ready_to_build" || status === "analysis_complete") return "warning";
  if (status === "uploaded" || status === "analyzing" || status === "queued" || status === "building" || status === "verifying") {
    return "working";
  }
  return "neutral";
}

function findingIcon(level: FindingLevel): string {
  if (level === "success") return "✓";
  if (level === "warning") return "!";
  if (level === "blocker") return "×";
  return "i";
}

function checkStatus(check: VerificationCheck | undefined): VerificationCheck["status"] {
  return check?.status ?? "pending";
}

function checkDetail(check: VerificationCheck | undefined): string {
  return check?.detail ?? "Not run yet.";
}

function hasArtifact(job: ConversionJob, kind: "package" | "report"): boolean {
  return job.artifacts.some((artifact) => artifact.kind === kind && artifact.available);
}

function StatusPill({ status }: { status: ConversionJob["status"] }) {
  return <span className={`status-pill status-pill--${statusTone(status)}`}>{STATUS_LABELS[status]}</span>;
}

function SectionHeading({
  eyebrow,
  title,
  detail,
  action,
  id,
}: {
  eyebrow?: string;
  title: string;
  detail?: string;
  action?: ReactNode;
  id?: string;
}) {
  return (
    <div className="section-heading">
      <div>
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h2 id={id}>{title}</h2>
        {detail ? <p className="section-detail">{detail}</p> : null}
      </div>
      {action}
    </div>
  );
}

export function ConversionWorkbench() {
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<ConversionJob | null>(null);
  const [capability, setCapability] = useState<string | null>(null);
  const [busy, setBusy] = useState<BusyAction>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [streamState, setStreamState] = useState<"connecting" | "live" | "offline" | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const updateJob = useCallback((incoming: ConversionJob) => {
    setJob((current) => mergeJob(current, incoming));
  }, []);

  const validateFile = useCallback((candidate: File): string | null => {
    if (!candidate.name.toLowerCase().endsWith(".deb")) {
      return "Choose a Debian package file ending in .deb.";
    }
    if (candidate.size === 0) return "That package is empty.";
    if (candidate.size > MAX_UPLOAD_BYTES) {
      return "That package exceeds the 50 MiB upload limit.";
    }
    return null;
  }, []);

  const chooseFile = useCallback(
    (candidate: File | null) => {
      if (!candidate) return;
      const validationError = validateFile(candidate);
      if (validationError) {
        setError(validationError);
        return;
      }
      setFile(candidate);
      setError(null);
      setNotice(null);
    },
    [validateFile],
  );

  const onFileChange = (event: ChangeEvent<HTMLInputElement>) => chooseFile(event.target.files?.[0] ?? null);

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsDragging(false);
    chooseFile(event.dataTransfer.files?.[0] ?? null);
  };

  const onDropZoneKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      fileInputRef.current?.click();
    }
  };

  const refreshJob = useCallback(
    async (jobId: string, jobCapability: string, quiet = false) => {
      try {
        const updated = await getJob(jobId, jobCapability);
        updateJob(updated);
        return updated;
      } catch (refreshError) {
        if (!quiet) setError(describeError(refreshError));
        throw refreshError;
      }
    },
    [updateJob],
  );

  // A refresh is intentionally possible only in the same browser session; job
  // capabilities never appear in routes or in persistent browser storage.
  useEffect(() => {
    const latestJobId = loadLatestJobId();
    if (!latestJobId) return;
    const sessionCapability = loadJobCapability(latestJobId);
    if (!sessionCapability) return;
    setCapability(sessionCapability);
    void refreshJob(latestJobId, sessionCapability, true).catch(() => {
      setNotice("Your previous job could not be restored. Its session access may have expired.");
    });
  }, [refreshJob]);

  // EventSource cannot send the capability header, so this uses fetch-based SSE.
  useEffect(() => {
    const jobId = job?.id;
    if (!jobId || !capability || job.status === "expired") return;
    const controller = new AbortController();
    setStreamState("connecting");
    void streamJobEvents(
      jobId,
      capability,
      (event) => {
        setStreamState("live");
        if (event.job?.id) updateJob(event.job);
        // The stream deliberately carries only state/log deltas, never the full
        // inspection report. Fetch the owned job after a status transition so a
        // newly-ready recipe cannot appear buildable before its findings load.
        if (event.type === "status" && event.job?.id) {
          void refreshJob(event.job.id, capability, true).catch(() => {
            setNotice("The status changed, but the latest compatibility report is still loading.");
          });
        }
        if (event.log) {
          setJob((current) => {
            if (!current || current.id !== jobId) return current;
            const duplicate = current.logs.some(
              (entry) => entry.timestamp === event.log?.timestamp && entry.message === event.log?.message,
            );
            return duplicate ? current : { ...current, logs: [...current.logs, event.log] };
          });
        }
        if (event.type === "error" && event.message) setNotice(event.message);
      },
      controller.signal,
    ).catch((streamError) => {
      if (controller.signal.aborted) return;
      setStreamState("offline");
      // Polling below remains available if a reverse proxy does not support SSE.
      setNotice(`Live updates are reconnecting. ${describeError(streamError)}`);
    });
    return () => controller.abort();
  }, [capability, job?.id, job?.status, refreshJob, updateJob]);

  // Polling is a resilient fallback for deployments that buffer SSE responses.
  useEffect(() => {
    const jobId = job?.id;
    if (!jobId || !capability || !ACTIVE_STATUSES.has(job.status)) return;
    const poll = () => void refreshJob(jobId, capability, true).catch(() => undefined);
    const timer = window.setInterval(poll, 3_000);
    return () => window.clearInterval(timer);
  }, [capability, job?.id, job?.status, refreshJob]);

  const submitUpload = async () => {
    if (!file || busy) return;
    const validationError = validateFile(file);
    if (validationError) {
      setError(validationError);
      return;
    }
    setError(null);
    setNotice(null);
    setBusy("upload");
    try {
      const response = await createJob(file, ARCH_X86_64_TARGET);
      setJob(response.job);
      setCapability(response.capability);
      saveJobCapability(response.job.id, response.capability);
      setNotice("Upload received. The service is inspecting it without running its contents.");
    } catch (uploadError) {
      setError(describeError(uploadError));
    } finally {
      setBusy(null);
    }
  };

  const requestBuild = async () => {
    if (!job || !capability || busy) return;
    setError(null);
    setNotice(null);
    setBusy("build");
    try {
      const updated = await startBuild(job.id, capability);
      updateJob(updated);
      setNotice("Build queued in a disposable Linux environment.");
    } catch (buildError) {
      setError(describeError(buildError));
    } finally {
      setBusy(null);
    }
  };

  const download = async (kind: "package" | "report") => {
    if (!job || !capability || busy) return;
    setError(null);
    setBusy(kind);
    try {
      const url = await requestDownload(job.id, capability, kind);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "";
      anchor.rel = "noreferrer";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    } catch (downloadError) {
      setError(describeError(downloadError));
    } finally {
      setBusy(null);
    }
  };

  const reset = () => {
    if (job) forgetJobCapability(job.id);
    setFile(null);
    setJob(null);
    setCapability(null);
    setBusy(null);
    setError(null);
    setNotice(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const phase = useMemo(() => {
    if (!job) return 1;
    if (isAnalysisInProgress(job)) return 2;
    if (isBuildInProgress(job) || job.status === "succeeded" || job.status === "failed" || job.status === "environment_unavailable") return 3;
    return 2;
  }, [job]);

  const reportExpires = formatDate(job?.expiresAt);
  const analysis = job?.analysis;
  const recipeIsSupported = analysis?.recipe?.supported === true;

  return (
    <main className="page-shell">
      <header className="site-header">
        <a className="brand" href="#main-workbench" aria-label="Flexy home">
          <span className="brand-mark" aria-hidden="true">F</span>
          <span>Flexy</span>
        </a>
        <span className="header-note">Remote Linux package preparation</span>
      </header>

      <section className="hero" aria-labelledby="page-title">
        <div className="hero-copy">
          <p className="eyebrow">Linux package preparation, with honest results</p>
          <h1 id="page-title">Turn a supported Debian package into an Arch package.</h1>
          <p>
            Flexy analyzes your <code>.deb</code> remotely, then builds only when a reviewed conversion recipe applies.
            Your downloaded application runs on your own computer—not in this browser.
          </p>
        </div>
        <aside className="honesty-note" aria-label="Compatibility limitation">
          <span className="honesty-note__icon" aria-hidden="true">i</span>
          <p>
            Changing package formats cannot make Windows or macOS binaries run on Linux. A successful package build alone
            does not prove the desktop application will work.
          </p>
        </aside>
      </section>

      <div className="workflow" aria-label="Conversion workflow">
        {WORKFLOW_STEPS.map((step) => (
          <div className={`workflow-step ${phase === step.number ? "workflow-step--active" : ""} ${phase > step.number ? "workflow-step--complete" : ""}`} key={step.number}>
            <span className="workflow-step__number">{phase > step.number ? "✓" : step.number}</span>
            <span>
              <strong>{step.label}</strong>
              <small>{step.detail}</small>
            </span>
          </div>
        ))}
      </div>

      <div id="main-workbench" className="workbench" tabIndex={-1}>
        <section className="panel upload-panel" aria-labelledby="upload-title">
          <SectionHeading id="upload-title" eyebrow="01 · Package" title="Upload a Debian package" detail="Maximum 50 MiB. Files are treated as untrusted and are never installed on this web server." />

          <input ref={fileInputRef} className="visually-hidden" type="file" aria-label="Choose a .deb package" accept=".deb,application/vnd.debian.binary-package" onChange={onFileChange} />
          <div
            className={`drop-zone ${isDragging ? "drop-zone--dragging" : ""} ${file ? "drop-zone--selected" : ""}`}
            role="button"
            tabIndex={0}
            aria-describedby="upload-help"
            onClick={() => fileInputRef.current?.click()}
            onKeyDown={onDropZoneKeyDown}
            onDragEnter={(event) => {
              event.preventDefault();
              setIsDragging(true);
            }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={() => setIsDragging(false)}
            onDrop={onDrop}
          >
            <span className="drop-zone__icon" aria-hidden="true">⇧</span>
            {file ? (
              <span className="selected-file">
                <strong>{file.name}</strong>
                <small>{formatBytes(file.size)} · Ready for inspection</small>
              </span>
            ) : (
              <span>
                <strong>Drop a <code>.deb</code> file here</strong>
                <small>or select it from your computer</small>
              </span>
            )}
            <span className="text-link">Browse files</span>
          </div>
          <p id="upload-help" className="field-help">The file is checked before any build is offered. Maintainer scripts are not run during inspection.</p>

          <div className="target-grid" aria-label="Target selection">
            <label>
              <span>Target operating system</span>
              <select defaultValue="arch" aria-label="Target operating system">
                <option value="arch">Arch Linux</option>
              </select>
            </label>
            <label>
              <span>CPU architecture</span>
              <select defaultValue="x86_64" aria-label="Target CPU architecture">
                <option value="x86_64">x86_64</option>
              </select>
            </label>
            <p className="target-limit">Only this target is enabled in the initial release. Other Linux formats, source rebuilds, and Wine workflows are not implied support.</p>
          </div>

          <div className="panel-actions">
            {job ? <button className="button button--quiet" type="button" onClick={reset}>Start another package</button> : null}
            <button className="button button--primary" type="button" disabled={!file || busy !== null} onClick={submitUpload}>
              {busy === "upload" ? "Uploading and analyzing…" : job ? "Analyze this package instead" : "Analyze package"}
            </button>
          </div>
        </section>

        {error ? (
          <div className="alert alert--error" role="alert">
            <strong>We could not continue.</strong>
            <span>{error}</span>
          </div>
        ) : null}
        {notice ? (
          <div className="alert alert--notice" role="status" aria-live="polite">
            {notice}
          </div>
        ) : null}

        <section className="panel status-panel" aria-labelledby="status-title" aria-live="polite">
          <SectionHeading
            eyebrow="02 · Compatibility"
            id="status-title"
            title="Package findings"
            detail={job ? "Results come from static package inspection. Uploaded application scripts are never executed." : "Upload a package to inspect its metadata, executable formats, dependencies, and blockers."}
            action={job ? <StatusPill status={job.status} /> : undefined}
          />

          {!job ? <EmptyAnalysis /> : <AnalysisResults job={job} />}
        </section>

        {job ? (
          <section className="panel build-panel" aria-labelledby="build-title">
            <SectionHeading
              eyebrow="03 · Isolated build"
              id="build-title"
              title="Build and verification"
              detail="The service uses a disposable Linux environment. It reports each verification level separately rather than inferring desktop compatibility."
              action={streamState ? <span className={`stream-state stream-state--${streamState}`}>{streamState === "live" ? "Live updates" : streamState === "connecting" ? "Connecting updates" : "Polling updates"}</span> : undefined}
            />
            <BuildControls
              job={job}
              busy={busy}
              recipeIsSupported={recipeIsSupported}
              onBuild={requestBuild}
              onDownload={download}
            />
            <VerificationResults checks={job.verification} />
            <BuildLogs logs={job.logs} />
            {reportExpires ? <p className="retention-note">This job’s uploaded file and generated artifacts are scheduled for deletion after <strong>{reportExpires}</strong>.</p> : null}
          </section>
        ) : null}
      </div>

      <section className="safety-footer" aria-labelledby="safety-title">
        <div>
          <p className="eyebrow">Designed for a narrow, safer first release</p>
          <h2 id="safety-title">What Flexy will—and will not—do</h2>
        </div>
        <ul>
          <li>Only reviewed recipes and explicit dependency mappings can start a build.</li>
          <li>Unsupported binaries, dependencies, links, and installation steps are rejected with a reason.</li>
          <li>A package creation result is separate from install, launch, and functionality verification.</li>
        </ul>
      </section>
    </main>
  );
}

function EmptyAnalysis() {
  return (
    <div className="empty-state">
      <span className="empty-state__icon" aria-hidden="true">⌁</span>
      <div>
        <h3>No package inspected yet</h3>
        <p>Once uploaded, you’ll see the package architecture, executable types, dependency mappings, and any compatibility blockers here.</p>
      </div>
    </div>
  );
}

function AnalysisResults({ job }: { job: ConversionJob }) {
  const analysis = job.analysis;
  if (!analysis) {
    return (
      <div className="analysis-pending">
        <span className="progress-dot" aria-hidden="true" />
        <p>
          {job.status === "blocked" || job.status === "failed"
            ? "The service did not produce a complete analysis. See the reason below or in the log."
            : "Inspecting archive metadata and contents without executing package code…"}
        </p>
      </div>
    );
  }

  const findings = [...analysis.blockers, ...analysis.findings.filter((finding) => !analysis.blockers.some((blocker) => blocker.code && blocker.code === finding.code))];
  const packageTitle = [analysis.packageName ?? job.packageName, analysis.packageVersion ?? job.packageVersion].filter(Boolean).join(" ");

  return (
    <div className="analysis-results">
      <div className="summary-grid">
        <SummaryItem label="Package" value={packageTitle || "Not declared"} />
        <SummaryItem label="Declared architecture" value={analysis.packageArchitecture ?? "Not declared"} />
        <SummaryItem label="Executable types" value={analysis.executableTypes.length ? analysis.executableTypes.join(", ") : "No executables found"} />
        <SummaryItem
          label="Conversion recipe"
          value={analysis.recipe ? (analysis.recipe.supported ? `${analysis.recipe.id} (supported)` : "No supported recipe") : "No recipe matched"}
          tone={analysis.recipe?.supported ? "success" : "danger"}
        />
      </div>

      {analysis.recipe?.detail ? <p className="recipe-detail">{analysis.recipe.detail}</p> : null}

      <div className="analysis-columns">
        <article className="analysis-card">
          <h3>Dependencies</h3>
          {analysis.dependencies.length ? (
            <ul className="data-list">
              {analysis.dependencies.map((dependency, index) => (
                <li key={`${dependency.name}-${index}`}>
                  <span>
                    <strong>{dependency.name}{dependency.version ? ` ${dependency.version}` : ""}</strong>
                    <small>{dependency.detail ?? (dependency.mappedTo ? `Maps to ${dependency.mappedTo}` : "No approved Arch mapping")}</small>
                  </span>
                  <StatusMark status={dependency.status} />
                </li>
              ))}
            </ul>
          ) : <p className="muted">No package dependencies were reported.</p>}
        </article>

        <article className="analysis-card">
          <h3>Executables</h3>
          {analysis.executables.length ? (
            <ul className="data-list">
              {analysis.executables.map((executable, index) => (
                <li key={`${executable.path}-${index}`}>
                  <span>
                    <strong className="monospace">{executable.path}</strong>
                    <small>{[executable.kind, executable.architecture, executable.interpreter].filter(Boolean).join(" · ") || executable.detail || "No binary metadata"}</small>
                  </span>
                  <StatusMark status={executable.compatible ? "supported" : "unsupported"} />
                </li>
              ))}
            </ul>
          ) : <p className="muted">No executable payloads were reported.</p>}
        </article>
      </div>

      <article className={`findings ${analysis.blockers.length ? "findings--blocked" : "findings--clear"}`}>
        <h3>{analysis.blockers.length ? "Compatibility blockers" : "Compatibility findings"}</h3>
        {findings.length ? (
          <ul>
            {findings.map((finding, index) => <FindingRow finding={finding} key={`${finding.code ?? finding.title}-${index}`} />)}
          </ul>
        ) : (
          <p>
            No blockers were found in static inspection. This only means the supported recipe may be eligible; it does not guarantee launch or desktop functionality.
          </p>
        )}
      </article>

      {job.error ? (
        <div className="analysis-error">
          <strong>{job.error.message}</strong>
          {job.error.detail ? <span>{job.error.detail}</span> : null}
        </div>
      ) : null}
    </div>
  );
}

function SummaryItem({ label, value, tone }: { label: string; value: string; tone?: "success" | "danger" }) {
  return (
    <div className={`summary-item ${tone ? `summary-item--${tone}` : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function StatusMark({ status }: { status: "supported" | "unsupported" | "unknown" }) {
  const labels = { supported: "Supported", unsupported: "Unsupported", unknown: "Not mapped" };
  return <span className={`status-mark status-mark--${status}`}>{labels[status]}</span>;
}

function FindingRow({ finding }: { finding: Finding }) {
  return (
    <li className={`finding finding--${finding.level}`}>
      <span className="finding__icon" aria-hidden="true">{findingIcon(finding.level)}</span>
      <span>
        <strong>{finding.title}</strong>
        <small>{finding.detail}</small>
      </span>
    </li>
  );
}

function BuildControls({
  job,
  busy,
  recipeIsSupported,
  onBuild,
  onDownload,
}: {
  job: ConversionJob;
  busy: BusyAction;
  recipeIsSupported: boolean;
  onBuild: () => void;
  onDownload: (kind: "package" | "report") => void;
}) {
  const buildAvailable = canStartBuild(job);
  const building = isBuildInProgress(job);
  const packageAvailable = hasArtifact(job, "package");
  const reportAvailable = hasArtifact(job, "report");
  const blockedReason = job.status === "blocked" || (job.analysis && !recipeIsSupported)
    ? "This package cannot be built because no supported recipe applies or a blocker was found."
    : job.status === "failed"
      ? "The last build failed. Review the readable build log and report for the reason."
      : null;

  return (
    <div className="build-controls">
      <div>
        {blockedReason ? <p className="build-explanation">{blockedReason}</p> : null}
        {job.status === "environment_unavailable" ? <p className="build-explanation">The required isolated build environment is not available. No build was run; try again when the service reports it as available.</p> : null}
        {job.status === "expired" ? <p className="build-explanation">This job and its artifacts have expired. Start a new upload to continue.</p> : null}
        {job.status === "succeeded" ? <p className="build-explanation build-explanation--success">Package creation finished. Read the separate verification results before treating it as compatible.</p> : null}
        {building ? <p className="build-explanation">The build has no host filesystem or host credentials. Progress is shown below as it arrives.</p> : null}
      </div>
      <div className="button-group">
        <button className="button button--primary" type="button" disabled={!buildAvailable || busy !== null} onClick={onBuild}>
          {busy === "build" ? "Queueing build…" : building ? "Build in progress" : "Start isolated build"}
        </button>
        <button className="button button--secondary" type="button" disabled={!packageAvailable || busy !== null} onClick={() => onDownload("package")}>
          {busy === "package" ? "Preparing…" : "Download Arch package"}
        </button>
        <button className="button button--secondary" type="button" disabled={!reportAvailable || busy !== null} onClick={() => onDownload("report")}>
          {busy === "report" ? "Preparing…" : "Download report"}
        </button>
      </div>
    </div>
  );
}

function VerificationResults({ checks }: { checks: VerificationCheck[] }) {
  return (
    <section className="verification" aria-labelledby="verification-title">
      <div className="subsection-heading">
        <h3 id="verification-title">Verification results</h3>
        <p>These are deliberately distinct checks. A package is not called desktop-compatible merely because it builds.</p>
      </div>
      <div className="verification-grid">
        {VERIFICATION_ORDER.map((name) => {
          const check = checks.find((item) => item.name === name);
          const status = checkStatus(check);
          return (
            <article className={`verification-card verification-card--${status}`} key={name}>
              <span className="verification-card__indicator" aria-hidden="true">
                {status === "passed" ? "✓" : status === "failed" ? "×" : status === "skipped" ? "–" : "·"}
              </span>
              <div>
                <h4>{VERIFICATION_LABELS[name]}</h4>
                <p>{checkDetail(check)}</p>
                <span>{status === "pending" ? "Not run" : status}</span>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function BuildLogs({ logs }: { logs: LogEntry[] }) {
  return (
    <details className="logs" open={logs.length > 0}>
      <summary>
        <span>Build logs</span>
        <small>{logs.length ? `${logs.length} event${logs.length === 1 ? "" : "s"}` : "No logs yet"}</small>
      </summary>
      <div className="log-output" aria-live="polite" aria-label="Build log output">
        {logs.length ? (
          logs.map((entry, index) => (
            <p key={`${entry.timestamp ?? ""}-${entry.message}-${index}`} className={`log-line log-line--${entry.level ?? "info"}`}>
              {entry.timestamp ? <time dateTime={entry.timestamp}>{formatDate(entry.timestamp) ?? entry.timestamp}</time> : null}
              {entry.step ? <span className="log-step">{entry.step}</span> : null}
              <span>{entry.message}</span>
            </p>
          ))
        ) : <p className="muted">Logs will appear once analysis or building begins. The service never fabricates progress or successful results.</p>}
      </div>
    </details>
  );
}
