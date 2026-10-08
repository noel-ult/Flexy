# Dokploy deployment

This guide deploys Flexy as a **Docker Compose** service in Dokploy. The
checked-in profile is production-ready for safe upload, inspection, reports,
retention, and artifact access, but it intentionally defaults builds to
`environment_unavailable`. Docker's stock seccomp policy commonly blocks the
namespace setup Bubblewrap needs. Do not claim the conversion path works until
an operator has supplied a narrowly reviewed host policy and passed the actual
fixture check below.

## Before creating the service

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

1. In Dokploy, create a **Docker Compose** service from this repository and set
   its Compose Path to `compose.dokploy.yaml`.
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
   `FRONTEND_ORIGIN`, and `NEXT_PUBLIC_API_BASE_URL` must exactly match the web
   origin (scheme and host, no trailing slash).
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
