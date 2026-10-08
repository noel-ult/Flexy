# Dokploy deployment

This guide deploys Flexy as a **Docker Compose** service in Dokploy. The
checked-in profile is production-ready for safe upload, inspection, reports,
retention, and artifact access, but it intentionally defaults builds to
`environment_unavailable`. Docker's stock seccomp policy commonly blocks the
namespace setup Bubblewrap needs. Do not claim the conversion path works until
an operator has supplied a narrowly reviewed host policy and passed the actual
fixture check below.

## Before creating the service

### Dockerfile-based Application: frontend only

The repository-root `Dockerfile` builds the same non-root Next.js frontend as
`infra/docker/web.Dockerfile`. It exists for tools that automatically look for
`Dockerfile` at the repository root and prevents the missing-Dockerfile build
error. It listens on port `3000`.

This is **not** a replacement for the full Compose deployment: the API,
separate worker, PostgreSQL, Redis, and private object storage must be deployed
independently. By default the browser uses same-origin `/v1`. Set server-only
runtime `FLEXY_API_UPSTREAM` to the reachable private API origin (for example
`http://api:8000`) and ensure the web and API services share a network. This
runtime value is not baked into or exposed by the browser bundle. The web
proxy streams uploads, SSE and downloads and forwards only API-required headers,
not cookies or unrelated authorization. It cannot create a missing backend.
Alternatively route `/v1` directly to the API using Traefik.

For a separate **public** backend origin, pass `NEXT_PUBLIC_API_BASE_URL` as a
build argument. A runtime value alone cannot replace the baked-in browser URL.
If that backend is on another origin, its `FRONTEND_ORIGIN` must allow the
frontend origin. Do not claim upload, analysis or conversion works from a
frontend-only deployment. A missing proxy upstream returns a readable 503;
an unreachable upstream returns a readable 502, not a compatibility result.

For a complete stack on Dokploy, use `compose.dokploy.yaml` below. Adding the
root Dockerfile does not change existing env values, provider settings, routes,
or service definitions.

### Full-stack prerequisites

- Use a dedicated Linux deployment server or Dokploy remote server with enough
  CPU, memory, and disk for a disposable Arch build. Do not co-locate this
  untrusted-build workload with services you cannot isolate.
- Point a DNS A/AAAA record for a public origin such as `flexy.example.com` at
  the Dokploy server.
- Copy [`infra/dokploy/.env.example`](../infra/dokploy/.env.example) into the
  Dokploy Compose service's **Environment** tab and replace every
  `REPLACE_*` value with a unique secret. Keep it in Dokploy's secret-bearing
  environment store, not in Git, an image layer, or `NEXT_PUBLIC_*` variables.
- Generate URI-safe database and Redis passwords, then use each same value in
  its URL variable. A convenient generator is:

  ```sh
  openssl rand -base64 32 | tr '+/' '-_' | tr -d '='
  ```

  `DATABASE_URL` and `REDIS_URL` contain credentials, so treat both as
  secrets. Do not use `localhost` or Docker service names for any public URL.
- Leave `BUILD_EXECUTOR=unavailable`. It is the secure default for the supplied
  Docker profile, not a deployment error.

## Create the Compose service

### Start here: GitHub integration

Use **Docker Compose**, not an Application service or Custom Git provider.
Keep the existing project, service ID, saved environment, and volumes; there
is no reason to delete them to repair a failed image pull.

1. In Dokploy **Settings -> Git Providers**, connect a GitHub App and grant
   its installation access to `noel-ult/Flexy`. If already connected, reuse it.
2. Select the **GitHub** provider on **Flexy Compose** with these settings:

   | Setting | Value |
   | --- | --- |
   | Owner | `noel-ult` |
   | Repository | `Flexy` |
   | Branch | `main` |
   | Compose path | `compose.dokploy.yaml` |
   | Compose type | Docker Compose |
   | Isolated Deployments | Enabled |
   | Automatic deployment | Disabled during setup |

3. Keep the previously verified env and the two HTTPS routes. The local helper
   below can validate the App's repository/branch access, repair the failed
   Custom Git source, and verify those settings without regenerating secrets.
4. Review **Preview Compose**, then deploy. Inspect **Deployments** for build
   logs; the runtime log dropdown requires a real container, not the
   `select-a-container` placeholder. A failed image pull creates no application
   container to select.

GitHub integration still uses Git internally to fetch the repository; the
important distinction is that Dokploy uses its connected GitHub App, not the
Custom Git URL workflow. No shell Git command is needed to configure it.

### Migrate the existing Flexy Application

If you already saved the verified `flexy.noelbiju.in` environment in the
Application service, the local standard-library helper copies it without
regenerating credentials. It targets the existing Flexy project/environment;
do not use it for a different account or deployment.

```sh
python3 scripts/dokploy_compose.py --apply
```

Enter your active Dokploy API key at the hidden prompt. Never paste it into
chat, a command argument, a committed file, or an environment screenshot.
The key needs service creation/configuration, environment read/write, and
domain creation permissions for this project. The helper:

- Creates **Flexy Compose** on the same server, or resumes its marked migration
  service on a rerun. The original Application is not edited, stopped, or deleted.
- Uses **GitHub** source `noel-ult/Flexy`, branch `main`, Compose path
  `compose.dokploy.yaml`, Docker Compose mode, and isolated deployments.
  It checks accessible GitHub providers, repository access, and branch access
  before creating or updating a service. No Custom Git fallback is allowed.
  It reuses the source Application's provider (or the already configured Compose
  provider), or the only connected provider. Multiple providers require
  `--github-id PROVIDER_ID`; that ID is metadata, not an API key. Automatic
  deployment is disabled during setup.
- Copies the saved environment exactly, verifies all settings by reading them
  back, and creates the two HTTPS routes below. It preserves secrets and refuses
  conflicting Compose env, active-service reconfiguration, or existing routes.
- Keeps `BUILD_EXECUTOR=unavailable`. It never relaxes worker isolation.

The previously created Custom Git Compose service can be switched with the same
`--apply` command, even after a failed deployment. This exception permits only
GitHub source fields to change, only for the known Flexy URL/main branch, with
an unchanged env and exclusively failed/cancelled deployment history. It
preserves routes, service ID, named volumes, and deployment history. Running
services, successful deployment history, or unrelated configuration changes
are not reconfigured automatically. The original Application is untouched.

By default it does not deploy. Review **Preview Compose** and click **Deploy**.
Alternatively, after reviewing the profile, `--apply --deploy` explicitly
requests deployment; an accepted request does **not** prove build completion,
TLS, readiness, or a working conversion. Use the printed deployment-log URL.
Running without flags is read-only and checks the saved Compose configuration.

The API has no atomic conditional-update contract. Do not edit these services
concurrently with the helper. It stops rather than retrying an uncertain write;
inspect Dokploy first, then rerun to resume the marked service. If the host is
still assigned to the old Application, the helper preserves both services and
reports a conflict rather than deleting a route. Resolve that routing conflict
in Dokploy, then rerun. It checks conflicts within this environment; also check
other projects on the same proxy before deployment. No Cloudflare changes are
made. The helper uses the Dokploy v0.29.13 service/inventory shapes; an
unrecognized response stops the operation without a success claim.

### Manual setup

1. In Dokploy, create a **Docker Compose** service, select **GitHub** and its
   connected App, then select `noel-ult/Flexy`, branch `main`, and set Compose
   Path to `compose.dokploy.yaml`. Do not select Custom Git.
2. Turn on **Isolated Deployments**. Dokploy then creates a per-application
   network and connects Traefik to it; do not manually add `dokploy-network`.
3. In the **Domains** tab, use Dokploy's native Domains integration (not host
   port publishing) to create these two routes on the **same** public host:

   | Service | Public host and path | Container port |
   | --- | --- | --- |
   | `web` | `https://flexy.example.com/` | `3000` |
   | `api` | `https://flexy.example.com/v1` | `8000` |

   Enable TLS/Let's Encrypt for both. Do not strip `/v1`: the API owns that
   path. The browser calls the API at the same origin, so `APP_BASE_URL`,
   `FRONTEND_ORIGIN` must exactly match the web origin (scheme and host, no
   trailing slash). `NEXT_PUBLIC_API_BASE_URL` may be empty (recommended
   same-origin default) or match that origin. The Compose web service also
   configures `FLEXY_API_UPSTREAM=http://api:8000` and joins the private network,
   so `/v1` can be forwarded internally even if you route only `/` to `web`.
   Existing direct API routes remain supported; do not remove working routes
   just to use the internal proxy. Never set a public URL to `localhost:8000`.
4. Do **not** configure Dokploy Advanced → Ports and do not publish ports in
   Compose. PostgreSQL, Redis, MinIO, the migration process, and the worker
   must never have a public route. The web and API are reached only through
   Dokploy/Traefik.
5. Deploy, then inspect Dokploy's **Preview Compose** before accepting it. Only
   `web` and `api` may have public Traefik router labels; it must not add a
   Docker socket, host bind mount, privileged flag, or broad public port.

Dokploy writes values from its Environment tab to a deployment-local `.env`,
but Compose only sends values to a container when the Compose file explicitly
references them. The provided production Compose file does this intentionally;
do not add `env_file: .env` indiscriminately to every service.

## Image-pull failures

Both Compose profiles now build MinIO and its client from immutable GitHub
source commits. The full-stack CI check reproduced an unauthorized pull from
the previously configured Quay image; a successful frontend build would not
have detected that failure. Docker login or swapping to an unverified `latest`
image is not the fix.

`infra/docker/minio.Dockerfile` pins server commit
`0d7408fc9969caf07de6a8c3a84f9fbb10a6739e` (the existing
`RELEASE.2025-04-22T22-12-26Z`), and `minio-client.Dockerfile` pins
`b00526b153a31b36767991a4f5ce2cced435ee8e` (the existing client release).
The image-build host needs access to Docker Hub's official Go/Debian images,
`codeload.github.com`, `proxy.golang.org`, `sum.golang.org`, and required
module sources. This is image compilation, not permission for uploaded jobs
to access the internet. Private runtime networking is unchanged.

MinIO and `mc` are separate AGPL-3.0 software, not relicensed as Flexy or its
MIT demo. Each image includes its license and exact source archive under
`/usr/share/minio` or `/usr/share/minio-client`; retain the Dockerfiles and
provide corresponding source as required when distributing images. This
retains the already-selected releases; it is not a claim of current upstream
maintenance or a complete dependency/security audit. For production, evaluate
a maintained private S3 service and keep storage updates under review.

A manifest check proves registry access/architecture metadata, not container
startup or full layer availability. The subsequent actual deployment must pull
both images and pass the migration/storage/API health gates. If the exact tags
are no longer accessible, stop and provision a reviewed image mirror/build or
supported S3 storage; do not pick an arbitrary third-party image or `latest`.
These are historical releases, not a guarantee of current security support.
Mirror and security-review them for a durable production deployment. This
registry-only correction does not change credentials, data, or worker isolation.

## Data, migrations, and backups

The production Compose file uses Docker **named volumes** for PostgreSQL,
Redis, MinIO, and transient worker state. Named volumes survive redeployments
and work with Dokploy Volume Backups. Do not substitute absolute host-path bind
mounts: Dokploy can clean repository paths during deployments, and a build
worker must not see arbitrary host files.

- Back up PostgreSQL and the MinIO artifact volume using Dokploy's Volume
  Backups or an equivalent encrypted, access-controlled backup process.
- Redis persistence protects queued work across a Redis restart, but it is not
  a substitute for PostgreSQL backups.
- Do not back up transient worker scratch data or preserve expired uploads as a
  backup policy. Flexy's retention worker and the MinIO lifecycle rule target a
  24-hour retention period; verify that rule after the first deployment.
- The `migrate` service runs `alembic upgrade head` after PostgreSQL is ready.
  API and worker startup are gated on its successful completion. Keep
  `AUTO_CREATE_SCHEMA=false`; a failed migration should block readiness rather
  than be bypassed by runtime `create_all`.

After a deployment, check the API readiness endpoint from the API container or
Dokploy service console (it is deliberately not a separate public route):

```sh
docker exec API_CONTAINER python -c \
  "from urllib.request import urlopen; print(urlopen('http://127.0.0.1:8000/readyz').read().decode())"
```

Replace `API_CONTAINER` with the actual container ID or name. `/healthz` proves
the HTTP process is running. `/readyz` additionally proves that the API can
reach PostgreSQL. Review the `migrate`, `minio-init`, API, and worker logs in
Dokploy before sending traffic to the service.

## Bubblewrap host preflight and explicit enablement

Flexy deliberately uses no Docker socket, no privileged container, no
`seccomp=unconfined`, and no host filesystem bind mount to work around nested
namespace failures. Docker's stock seccomp profile generally denies `unshare`
without `CAP_SYS_ADMIN`; Flexy drops that capability. Therefore simply changing
`BUILD_EXECUTOR` to `bwrap` does **not** make a normal Dokploy worker safe or
functional.

The checked-in Compose profile intentionally does not ship a broad or untested
seccomp exception. An operator who needs Docker-hosted conversion must first
design, security-review, version-pin, and deploy a worker-only seccomp/AppArmor
policy that permits only the namespace behavior Bubblewrap requires while
retaining the dropped capabilities, non-root UID, read-only root filesystem,
network isolation, PID/memory limits, and no Docker socket. This is host- and
runtime-specific work; it is not accomplished by `seccomp=unconfined`, adding
`CAP_SYS_ADMIN`, privileged mode, host networking, or a host mount.

Only after that policy is installed, validate the exact deployed worker as its
non-root UID:

After deployment, identify the worker container in Dokploy or with `docker ps`,
then run this as the worker's non-root UID:

```sh
docker exec --user 10001:10001 WORKER_CONTAINER \
  /usr/bin/unshare --user --map-root-user --net true
```

Replace `WORKER_CONTAINER` with the actual container ID or name. A failure here
means conversion builds are not available on that host. Keep
`BUILD_EXECUTOR=unavailable` and use a suitable Linux host or separately
designed isolated builder instead.

Only after the namespace preflight passes and the custom policy review is
complete, set `BUILD_EXECUTOR=bwrap` in Dokploy, redeploy, and run the decisive
end-to-end check:

1. Open the web domain and upload `fixtures/flexy-demo_1.0.0_amd64.deb`.
2. Confirm analysis finds the exact recipe, start the build, and wait for the
   package, installation, launch, and functionality results.
3. Upload `fixtures/unsupported-demo_1.0.0_amd64.deb` and confirm it is
   rejected before a build can start.

If the first fixture reports `environment_unavailable`, restore/retain the
default unavailable executor and fix the deployment host; do not claim a
successful conversion. A successful fixture build is only the narrow
package/CLI verification reported by Flexy. It does not promise that arbitrary
desktop applications, Windows binaries, or macOS binaries will run on Linux.

## Operating the deployment

- Redeploy whenever `NEXT_PUBLIC_API_BASE_URL` changes: Next.js embeds it while
  building the web image.
- Set an upstream/WAF request-body cap at or below `MAX_UPLOAD_BYTES` (50 MiB
  by default). The API independently enforces the limit, but perimeter limits
  prevent unnecessary proxy buffering.
- Keep the API download path out of ordinary request logs where possible;
  scoped download grants are sensitive bearer values even though they expire.
- Alert on migration failures, `/readyz` failures, queue backlog, worker exits,
  `environment_unavailable` build results, failed cleanup, and MinIO lifecycle
  failures.
- Use Dokploy's native Domains and a current TLS certificate; do not expose
  MinIO's console for convenience.
- The current Dockerfiles intentionally need a release-management pass before a
  regulated or reproducible production rollout: pin base-image digests and
  package/dependency lockfiles, generate an SBOM, scan/sign the resulting
  images, and rebuild on security updates. Do not substitute unreviewed mutable
  tags for a tested image digest.

For Dokploy behavior referenced above, see its official guides for
[Docker Compose](https://docs.dokploy.com/docs/core/docker-compose),
[Domains](https://docs.dokploy.com/docs/core/docker-compose/domains),
[Isolated Deployments](https://docs.dokploy.com/docs/core/docker-compose/utilities),
and [Volume Backups](https://docs.dokploy.com/docs/core/volume-backups).
