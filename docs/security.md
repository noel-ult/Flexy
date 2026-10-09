# Security model

Flexy is designed around the assumption that every uploaded archive is hostile.

## Input handling

- The API limits an upload to 50 MiB, extracted content to 250 MiB, and archive
  entries to 10,000. Job execution is limited to five minutes, one vCPU, and
  one GiB per build.
- Archive paths are normalized before extraction. Absolute paths, `..` traversal,
  unsafe symlinks/hardlinks, device nodes, malformed members, and oversized or
  excessive contents are rejected.
- Inspection reads metadata and executable headers only. Uploaded maintainer
  scripts and binaries are never executed during inspection.
- An unsupported ELF architecture/format, a dependency without an explicit
  mapping, a maintainer script, or a package/content hash without an approved
  recipe blocks the job before build scheduling.

## Execution isolation

### Opt-in data-only WASI packager

`BUILD_EXECUTOR=wasi` accepts only the exact reviewed demo recipe, reinspects it
and never executes uploaded code. The trusted archive writer runs in a new
Wasmtime/WASI store with only two scoped directory capabilities: read-only
staged input and fresh output. No credentials/environment or stdio are inherited;
no sockets, process execution, host root, or untrusted Wasm module is exposed.
The 128-KiB Wasm memory cap, fuel budget, epoch deadline, 1-MiB input cap and
verified output bound are additional to the worker container resource limits.
Trusted Python handles inspection, metadata, hashing, compression and private
artifact storage outside the guest; WASI isolation is not a claim that this
trusted service code runs in its own OS namespace. Keep the runtime patched.

This pathway produces an unsigned package and checks its data, not Linux
installation, application launch or functionality. Those three checks stay
`not_run`. No uploaded installation step is translated or executed. Job-owned
staging and partial output are cleaned after processing. Personal-use overrides
limit concurrency but do not authenticate visitors or impose global disk quotas.

The local runner uses Bubblewrap and fails closed when it cannot create a
disposable namespace. Production builds are intended to be individual Kubernetes
Jobs with all of the following properties:

- non-root user, read-only root filesystem, dropped Linux capabilities,
  `allowPrivilegeEscalation: false`, RuntimeDefault seccomp, and bounded CPU,
  memory, temporary storage, and active deadline;
- no `hostPath`, host PID/IPC/network namespace, privileged mode, Docker/CRI
  socket, host credentials, or Kubernetes service-account token;
- only staged recipe-approved input and a fresh output location; no host
  filesystem access;
- default-deny networking with explicit egress solely to the required artifact
  endpoint and API callback. The initial fixture needs no package-repository
  access. A future recipe must list each required source before any egress policy
  is added.

The checked-in Kustomize base intentionally denies external egress until an
operator writes exact endpoint CIDRs. This is a safe deployment failure, not a
reason to allow `0.0.0.0/0`.

### Docker / Dokploy profile

`compose.dokploy.yaml` is deliberately fail-closed for conversion builds:
`BUILD_EXECUTOR` defaults to `unavailable`. Docker's standard seccomp profile
normally denies the unprivileged `unshare` call that the Bubblewrap runner uses,
while this project deliberately drops `CAP_SYS_ADMIN`. The Dokploy service can
still accept and safely inspect uploads, but it must report an unavailable build
environment until an operator has security-reviewed and tested a worker-only,
host-specific sandbox policy.

Do not enable builds by using privileged containers, `CAP_SYS_ADMIN`,
`seccomp=unconfined`, a Docker/CRI socket, host networking, or host bind mounts.
Any approved custom policy must retain the non-root user, read-only root,
dropped capabilities, no host filesystem access, no network during the inner
build, and explicit CPU/memory/PID limits, then pass the real supported fixture
and unsupported-package checks described in [the Dokploy guide](dokploy.md).

## Access and retention

- Job IDs have high entropy and are not authorization. A job capability is
  required for every job action and held only in the current browser session.
- Download grants are random, hashed at rest, expire after 15 minutes by
  default, are scoped to either the report or package artifact, and are consumed
  after one successful authorization. A browser attachment URL necessarily
  contains the bearer grant, so configure proxies and access logs to redact
  `/v1/downloads/*` path values as defense in depth.
- The cleanup task removes expired rows and their upload/report/package objects
  after 24 hours by default. Configure an object-store expiration rule as a
  second line of defense.
- Do not put secrets in Git, ConfigMaps, image layers, browser environment
  variables, logs, or worker arguments. Use a cloud/Kubernetes secret manager
  and rotate database, Redis, and storage credentials.

## Operator checklist

Before enabling public access, verify the ingress upload limit, TLS, CORS origin,
access-log redaction for `/v1/downloads/*`, object-store privacy/encryption,
deletion lifecycle, queue authentication, database backups, namespace Pod
Security admission, NetworkPolicy enforcement, image signature/scanning policy,
and alerting for cleanup or worker failure.
