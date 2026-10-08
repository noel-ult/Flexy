# Flexy

Flexy is a browser-accessible, deliberately narrow package-conversion service. Its
first real workflow accepts a Linux `.deb`, analyzes it without executing package
code, and can create an Arch Linux `x86_64` package only when an exact supported
recipe matches.

It does **not** make Windows or macOS applications run on Linux. Repackaging a
binary cannot change its operating-system ABI, missing libraries, licensing, or
desktop integration. A successful package build is not a claim that a desktop app
will launch or work correctly on a user's machine.

## Current support

The v1 recipe supports only the included redistributable fixture:

- `fixtures/flexy-demo_1.0.0_amd64.deb`, a small MIT-licensed Linux x86_64 CLI
  program with a pinned file hash and an explicit `libc6` → `glibc` mapping.

`fixtures/unsupported-demo_1.0.0_amd64.deb` deliberately includes an unmapped
dependency and a maintainer script. It must be rejected during analysis and cannot
start a build. Similar-looking, modified, or arbitrary `.deb` files are not
silently converted.

The target selector currently exposes only Arch Linux `x86_64`. RPM conversion,
source rebuilds, Wine workflows, other CPU architectures, generic dependency
translation, and maintainer-script translation are future extension points, not
current capabilities.

## Run locally

Prerequisites: Docker Engine with Compose v2, enough disk for the images, and a
host/container runtime that permits unprivileged user namespaces for Bubblewrap.
The worker intentionally fails closed if its isolated build environment is not
available; it never switches to an unsandboxed build.

```sh
cp .env.example .env
docker compose up --build
```

Open <http://localhost:3000>, upload the supported fixture, and start its build
after analysis marks the recipe supported. The API is available at
<http://localhost:8000>. Compose starts PostgreSQL, Redis, and a private MinIO
bucket; MinIO's console is bound to <http://localhost:9001> for local debugging.

For a development machine where Bubblewrap is disabled by policy, analysis still
works but the UI must report the build environment as unavailable. Do not work
around that by enabling a host mount, Docker socket, privileged container, or an
unsandboxed executor.

Useful commands:

```sh
make config       # render and validate the Compose model
make check        # backend checks, frontend checks, and Kubernetes rendering
make down         # stop local services (keeps named volumes)
docker compose down -v  # intentionally delete local service data
```

The last command deletes local PostgreSQL, MinIO, upload, artifact, and work
volumes. It is only appropriate when that local data is no longer needed.

## What happens to an upload

1. The API gives each job an unguessable ID and a browser-held capability; the
   capability is stored hashed server-side.
2. Inspection parses the Debian archive and ELF metadata without running
   maintainer scripts or uploaded executables. It enforces upload size, expanded
   size, file-count, traversal, link, binary, dependency, and recipe checks.
3. A Redis/Dramatiq worker starts a short-lived, resource-limited isolated build
   only for a recipe-approved job. Uploaded applications are never installed on
   the API host.
4. The result separately records package creation, installation verification,
   launch verification, and functionality testing. An unrun or failed check stays
   unrun or failed; a package alone is not desktop compatibility evidence.
5. Downloads are private and require a scoped, short-lived, single-use download
   token. Jobs, uploads, reports, and package artifacts expire after 24 hours by
   default.

See [the architecture](docs/architecture.md), [deployment guide](docs/deployment.md),
the [Dokploy deployment guide](docs/dokploy.md), and [security model](docs/security.md)
for the operational detail.

## Dokploy / Docker production note

[`compose.dokploy.yaml`](compose.dokploy.yaml) is the Docker Compose profile for
Dokploy. Select its native **GitHub** provider, repository `noel-ult/Flexy`, branch
`main`, and Compose path `compose.dokploy.yaml` (not Custom Git or an Application
service for the entire stack). A root [`Dockerfile`](Dockerfile) also provides
the **web frontend only** for a Dockerfile-based Application deployment; it is
identical to `infra/docker/web.Dockerfile`. That image listens on port `3000`
and does not start the API, worker, database, queue, or artifact storage. It needs
an independently deployed Flexy backend. Set `NEXT_PUBLIC_API_BASE_URL` as a
**build argument** to that backend's public origin; setting it only as a runtime
environment variable does not change the browser bundle. A successful frontend
build alone is not a working conversion service.

The [Dokploy guide](docs/dokploy.md) includes a safe helper for copying
the saved environment and switching the earlier failed setup to GitHub without
deleting its services, credentials, or volumes. MinIO images use pinned release
tags from Quay; verify registry access from the deployment server before rollout.

It exposes only the routed web/API services, keeps state in named
volumes, gates the API and worker on a migration, and defaults
`BUILD_EXECUTOR=unavailable`. This is intentional: Docker's standard seccomp
profile commonly blocks Bubblewrap's nested namespace setup. The deployed
service can safely analyze packages and report that builds are unavailable, but
an operator must provide and validate a narrowly reviewed worker sandbox policy
before setting `BUILD_EXECUTOR=bwrap`. Never use privileged mode,
`seccomp=unconfined`, a Docker socket, or host mounts as a workaround.

See [Dokploy deployment](docs/dokploy.md) for the exact native Domains,
same-origin `/v1`, secret, named-volume backup, migration, and host-preflight
steps. The Docker images should also be pinned/scanned/signed as part of a
production release process; their current mutable base/dependency resolution is
not a reproducible release lock.

## Automated checks

GitHub Actions runs frontend typechecking, lint, interaction tests, the production
build, and a root-Dockerfile build with a read-only frontend container smoke test.
It also runs backend lint/tests and deployment-helper regression tests. These
checks do not deploy to Dokploy or prove API connectivity, sandbox availability,
or desktop compatibility; environment-dependent tests can be skipped explicitly.

For the frontend checks locally (Node.js 22):

```bash
cd web
npm install --ignore-scripts --no-audit --no-fund
npm run typecheck
npm run lint
npm test
npm run build
```

## Project layout

- `web/` — Next.js + TypeScript browser UI.
- `backend/` — FastAPI API, inspection/conversion domain, Dramatiq workers, and
  artifact access controls.
- `fixtures/` — supported and intentionally unsupported `.deb` test fixtures.
- `infra/` — local container images and fail-closed Kubernetes/Kustomize assets.

## Verification vocabulary

Flexy reports these independently:

| Result | Meaning |
| --- | --- |
| Package creation | An Arch package archive was produced. |
| Installation verification | The archive installed in a disposable test root. |
| Launch verification | The recipe-declared noninteractive command ran in the disposable sandbox using the installed entrypoint. |
| Functionality testing | The narrow fixture-specific headless assertion passed. |

None of these verifies arbitrary GUI behavior, hardware integration, user data,
or compatibility with every Arch installation. The browser runs the remote
conversion service; a downloaded application runs only on the user's own
computer after they choose to install it.
