# Deployment guide

## Local Compose development

1. Copy `.env.example` to `.env` and replace the development passwords before
   exposing any port beyond localhost.
2. Run `docker compose up --build`.
3. Confirm `http://localhost:8000/healthz`, then use `http://localhost:3000`.

Compose keeps PostgreSQL, Redis, MinIO, uploads, artifacts, and worker staging in
named volumes. Its worker has no Docker socket, no privileged flag, no added
capabilities, an immutable root filesystem, and only the internal network. The
API has an edge network solely because the local browser calls it directly.

The backend/worker image is Arch-based and includes `base-devel`, Python, and
Bubblewrap so it can produce an actual Arch package. It runs as UID 10001; package
builds must still happen inside Bubblewrap as that non-root user. Do not replace
this with a root `makepkg` invocation.

The supported local build path is this dedicated worker image: Bubblewrap's
read-only runtime binds then refer to image content, not the Docker host. Running
`BubblewrapRunner` directly from a developer workstation instead reads a small
read-only subset of that workstation's runtime (`/usr`, `/bin`, and libraries),
so treat it as a test convenience rather than a production isolation boundary.
Production builders must use a dedicated image/root filesystem and no host mounts.
Accordingly, the source-only backend example defaults `BUILD_EXECUTOR` to
`unavailable`; Compose explicitly selects its dedicated Bubblewrap runner image.

Some Docker hosts prohibit nested unprivileged user namespaces. Test this before
claiming local build support:

```sh
docker compose run --rm --no-deps worker \
  bwrap --unshare-user --unshare-pid --unshare-net --ro-bind / / --proc /proc true
```

If it fails, preserve the fail-closed behavior and use a suitable Linux runner or
the Kubernetes isolation model. Do not use privileged mode, `--security-opt
seccomp=unconfined`, or a Docker socket as a workaround.

## Kubernetes production integration contract

The v1 end-to-end-tested build path is the local Bubblewrap runner. The
Kubernetes assets in this repository establish the required security envelope
(RBAC, namespace policy, network policy, resource limits, and cleanup), but the
Kubernetes Job handoff/callback transport is deliberately fail-closed until an
operator supplies and verifies the executor integration. Do not present this
overlay as a verified production conversion path merely because it renders and
applies: an unavailable executor must surface as `environment_unavailable`.

Once that integration is implemented and tested, use the following prerequisites.

Use a cluster with enforced Pod Security Admission, a NetworkPolicy-capable CNI,
an ingress controller, TLS automation, private managed PostgreSQL/Redis/object
storage, and a secret manager. Build and publish immutable API/worker, builder,
and web images first. The web image bakes `NEXT_PUBLIC_API_BASE_URL` at build
time, so build it for the public same-origin URL used by your ingress.

Create `flexy-runtime-secrets` through External Secrets, Sealed Secrets, or an
equivalent secret-manager controller. It must contain these string keys:

| Key | Purpose |
| --- | --- |
| `DATABASE_URL` | private PostgreSQL connection URL |
| `REDIS_URL` | private Redis connection URL |
| `S3_ACCESS_KEY` | object storage credential |
| `S3_SECRET_KEY` | object storage credential |

Never use `flexy-runtime-secrets.example.yaml` as a deployed secret or commit a
copy containing production values.

## Applying the Kustomize manifests

1. Replace every `REPLACE_*` image value and every `example.invalid` URL in
   `infra/k8s/overlays/production/kustomization.yaml`. Pin images by digest in a
   production change, not a mutable tag.
2. Copy `infra/k8s/base/networkpolicy-external-egress.example.yaml` into the
   production overlay, replace the RFC 5737 documentation CIDRs with exact
   PostgreSQL, Redis, object-storage, and Kubernetes API endpoint CIDRs, and add
   it to that overlay's `resources`. Split policies by component where the CNI
   supports it; builders must not receive database, Redis, or general Internet
   access. The template's `migrator` policy deliberately permits PostgreSQL only;
   do not grant migration Jobs Redis or object-storage access.
3. Provision TLS secret `flexy-tls`, configure the ingress class if it is not
   `nginx`, and make its body-size limit no greater than the API's 50 MiB limit.
   Configure the ingress/application access log format to redact the path token
   on `/v1/downloads/*`; download grants are one-time, but they should not be
   retained in ordinary logs.
4. Render before applying. The checked-in `flexy-migrate` Job runs the versioned
   Alembic migration; wait for it to complete before relying on API readiness.

   ```sh
   kustomize build infra/k8s/overlays/production > /tmp/flexy.yaml
   kubectl apply --server-side --dry-run=server -f /tmp/flexy.yaml
   kubectl apply --server-side -f /tmp/flexy.yaml
   kubectl wait --for=condition=complete job/flexy-migrate -n flexy --timeout=180s
   ```

   Production sets `AUTO_CREATE_SCHEMA=false`; do not turn it on to bypass a
   failed migration. Compose keeps it true only because it is a disposable local
   development profile.

The base namespace uses the `restricted` Pod Security profile. The API has no
Kubernetes token. Only `flexy-worker` can create/read/delete `batch/jobs` and
read their pod logs; it cannot read Secrets or create arbitrary Pods. Generated
build Jobs must use `flexy-builder`, whose token is disabled.

The checked-in policies intentionally prevent external egress. A deployment that
has not added exact dependency policies will report its build environment as
unavailable rather than quietly run with broad egress. This is expected and
should be resolved by narrowing the policy, never by adding `0.0.0.0/0`.

## Build Job contract

The Kubernetes executor must create one Job per approved conversion with:

- `app.kubernetes.io/component: builder`, `serviceAccountName: flexy-builder`,
  `automountServiceAccountToken: false`, `restartPolicy: Never`,
  `backoffLimit: 0`, `activeDeadlineSeconds: 300`, and a short TTL after finish;
- a non-root RuntimeDefault-seccomp container with read-only root, no Linux
  capabilities, no host mounts, and explicit 1 vCPU / 1 GiB limits;
- only read-only recipe-owned tooling and a new `emptyDir` workspace; it receives
  a single scoped input grant plus scoped output/report callback grant, never
  storage credentials or a host filesystem;
- egress only to the necessary artifact endpoint and API callback. The initial
  fixture has no package-manager network dependency.

The worker Role in `infra/k8s/base/rbac.yaml` and the builder NetworkPolicy are
the cluster-side guardrails; the executor must not generate a weaker spec. If the
configured executor is not implemented or cannot verify the required environment,
the UI must return an honest environment-unavailable result.

## Retention and operations

`flexy-cleanup` runs `python -m app.worker cleanup` hourly and removes jobs whose
24-hour retention window has passed. Configure the artifact bucket with a one-day
expiration/lifecycle rule as defense in depth (MinIO Compose does this during
initialization). Lifecycle rules often run asynchronously, so they do not replace
the application cleanup task.

Monitor API/worker health, queue depth, rejected analysis reasons, build duration,
runner-availability errors, failed cleanup executions, object-store lifecycle
status, and the Kubernetes Job failure rate. Back up only metadata required by
your retention policy; do not retain uploaded application packages longer than
documented without a separate privacy and legal review.
