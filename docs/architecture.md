# Architecture

Flexy keeps untrusted package data away from the browser host and separates
inspection from build execution.

```text
Browser (job capability)
        │ HTTPS: upload/status/SSE/download grant
        ▼
FastAPI API ───── PostgreSQL (job state, hashed capabilities, token hashes)
        │                         │
        ├──── private object storage (uploads, reports, artifacts)
        ▼
Redis ──► Dramatiq worker ──► disposable isolated build
                                      │
                                      └── private artifact storage / API callback
```

## Trust boundaries

- A `.deb` is untrusted data. The inspection path parses `ar`, control, data,
  ELF, and link metadata with bounded readers; it does not execute package
  scripts, `postinst`, package binaries, or installer hooks.
- A recipe is trusted, reviewed application configuration. It identifies an
  exact supported package/version/content hash, declares allowed staged paths,
  and maps only explicitly approved Debian dependencies to Arch dependencies.
- The web browser receives a random capability when it creates a job. The API
  stores a one-way hash, requires the capability for status/build/events, and
  issues separate short-lived, artifact-scoped download tokens.
- PostgreSQL, Redis, and object storage are private service dependencies. Object
  keys and buckets are never made publicly readable; the API authorizes and
  streams downloads.

## Job lifecycle

`analyzing` → `ready` or `unsupported` → `build_queued` → `building` →
`succeeded`, `failed`, or `environment_unavailable` → `expired`/deleted.

An unsupported package never enters the build queue. A runner that cannot prove
the required isolation is available returns `environment_unavailable`; it is not
replaced with a less safe executor.

## Extensibility boundary

The domain separates package readers, recipe selectors, target builders, and
execution backends. Adding RPM input, a source-build recipe, or a Wine workflow
means adding an explicit analyzer and recipe/runner contract. It must not weaken
the `.deb` allowlist or turn generic package conversion into an implicit promise
of cross-platform compatibility.
