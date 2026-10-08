const capabilityKey = (jobId: string) => `flexy:job-capability:${jobId}`;
const latestJobKey = "flexy:latest-job";

/**
 * Capabilities are intentionally browser-session scoped. They are never put in
 * routes, query strings, local storage, or diagnostic output.
 */
export function saveJobCapability(jobId: string, capability: string): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(capabilityKey(jobId), capability);
  window.sessionStorage.setItem(latestJobKey, jobId);
}

export function loadJobCapability(jobId: string): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(capabilityKey(jobId));
}

export function loadLatestJobId(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(latestJobKey);
}

export function forgetJobCapability(jobId: string): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.removeItem(capabilityKey(jobId));
  if (window.sessionStorage.getItem(latestJobKey) === jobId) {
    window.sessionStorage.removeItem(latestJobKey);
  }
}
