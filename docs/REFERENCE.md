# Flexy source reference


Source reviewed: [`d65f2a770c31`](https://github.com/noel-ult/Flexy/commit/d65f2a770c31b1c60fba0fccb0eb6e463ea8c949).

This reference records the checked-in source, scripts, configuration names, and route declarations. It is a navigation aid; service availability and successful test execution require separate verification.

## Package commands

Run each command in the directory containing its package manifest. A declared script is not necessarily a passing check; guides call out placeholders and known mismatches.

### web/package.json

| Script | Shell command |
| --- | --- |
| `dev` | `next dev` |
| `build` | `next build` |
| `start` | `next start` |
| `lint` | `eslint . --max-warnings=0` |
| `typecheck` | `tsc --noEmit` |
| `test` | `vitest run` |
| `test:e2e` | `playwright test` |

Manifest: [web/package.json](../web/package.json).

## Configuration names

Names below are declared by source or example files. Example-only entries may be stale; consult the linked reader and project guide before relying on them. Secret values are intentionally omitted.

| Name | Declared/read in |
| --- | --- |
| `ALLOW_DEMO_INLINE_QUEUE` | [.env.example](../.env.example), [backend/app/config.py](../backend/app/config.py) |
| `API_PORT` | [.env.example](../.env.example) |
| `APP_BASE_URL` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py), [infra/ci/.env.example](../infra/ci/.env.example), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `ARTIFACT_BACKEND` | [.env.example](../.env.example), [backend/app/config.py](../backend/app/config.py), [infra/ci/.env.example](../infra/ci/.env.example), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `ARTIFACT_DIR` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py) |
| `AUTO_CREATE_SCHEMA` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py) |
| `BUILD_EXECUTOR` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py), [infra/ci/.env.example](../infra/ci/.env.example), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `BWRAP_PATH` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py) |
| `CLEANUP_INTERVAL_SECONDS` | [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `DATABASE_URL` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py), [infra/ci/.env.example](../infra/ci/.env.example), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `DOWNLOAD_TOKEN_TTL_SECONDS` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `FLEXY_API_UPSTREAM` | [web/lib/api-proxy.ts](../web/lib/api-proxy.ts) |
| `FLEXY_BUILDER_IMAGE` | [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py) |
| `FLEXY_E2E_URL` | [web/playwright.config.ts](../web/playwright.config.ts) |
| `FLEXY_EXPECT_WASI` | [web/e2e/conversion.spec.ts](../web/e2e/conversion.spec.ts) |
| `FLEXY_RECIPE_DIR` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py) |
| `FRONTEND_ORIGIN` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py), [infra/ci/.env.example](../infra/ci/.env.example), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `JOB_RETENTION_HOURS` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `JOB_TIMEOUT_SECONDS` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `LOG_LIMIT_BYTES` | [.env.example](../.env.example), [backend/app/config.py](../backend/app/config.py), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `MAX_EXTRACTION_BYTES` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `MAX_FILE_COUNT` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `MAX_UPLOAD_BYTES` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `MINIO_CONSOLE_PORT` | [.env.example](../.env.example) |
| `MINIO_ROOT_PASSWORD` | [.env.example](../.env.example), [infra/ci/.env.example](../infra/ci/.env.example), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `MINIO_ROOT_USER` | [.env.example](../.env.example), [infra/ci/.env.example](../infra/ci/.env.example), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `NEXT_PUBLIC_API_BASE_URL` | [.env.example](../.env.example), [infra/ci/.env.example](../infra/ci/.env.example), [infra/dokploy/.env.example](../infra/dokploy/.env.example), [web/lib/api.ts](../web/lib/api.ts) |
| `POSTGRES_DB` | [.env.example](../.env.example), [infra/ci/.env.example](../infra/ci/.env.example), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `POSTGRES_PASSWORD` | [.env.example](../.env.example), [infra/ci/.env.example](../infra/ci/.env.example), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `POSTGRES_USER` | [.env.example](../.env.example), [infra/ci/.env.example](../infra/ci/.env.example), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `QUEUE_MODE` | [.env.example](../.env.example), [backend/app/config.py](../backend/app/config.py) |
| `REDIS_PASSWORD` | [infra/ci/.env.example](../infra/ci/.env.example), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `REDIS_URL` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py), [infra/ci/.env.example](../infra/ci/.env.example), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `REMOTE_BUILD_TOKEN` | [backend/app/config.py](../backend/app/config.py) |
| `S3_ACCESS_KEY` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py), [infra/ci/.env.example](../infra/ci/.env.example), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `S3_BUCKET` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py), [infra/ci/.env.example](../infra/ci/.env.example), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `S3_ENDPOINT` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py), [infra/ci/.env.example](../infra/ci/.env.example), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `S3_SECRET_KEY` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py), [infra/ci/.env.example](../infra/ci/.env.example), [infra/dokploy/.env.example](../infra/dokploy/.env.example) |
| `S3_SERVER_SIDE_ENCRYPTION` | [.env.example](../.env.example), [backend/app/config.py](../backend/app/config.py), [infra/ci/.env.example](../infra/ci/.env.example) |
| `UPLOAD_DIR` | [.env.example](../.env.example), [backend/.env.example](../backend/.env.example), [backend/app/config.py](../backend/app/config.py) |
| `WEB_PORT` | [.env.example](../.env.example) |
| `WORK_DIR` | [.env.example](../.env.example), [backend/app/config.py](../backend/app/config.py) |

## HTTP route declarations

Paths are **as declared in each source file**. Router/blueprint mount prefixes from the entry point must be prepended; the project guide describes the active entry point and common prefixes. Alternate entry points (such as SQLite) are listed separately by file. Authentication, request bodies, and response schemas live in the linked handlers/models.

| Source | Method | Declared path | Handler |
| --- | --- | --- | --- |
| [backend/app/main.py](../backend/app/main.py) | GET | `/healthz` | `healthz` |
| [backend/app/main.py](../backend/app/main.py) | GET | `/readyz` | `readyz` |
| [backend/app/main.py](../backend/app/main.py) | POST | `/v1/jobs` | `create_job` |
| [backend/app/main.py](../backend/app/main.py) | GET | `/v1/jobs/{job_id}` | `get_job` |
| [backend/app/main.py](../backend/app/main.py) | GET | `/v1/jobs/{job_id}/events` | `job_events` |
| [backend/app/main.py](../backend/app/main.py) | POST | `/v1/jobs/{job_id}/build` | `start_build` |
| [backend/app/main.py](../backend/app/main.py) | POST | `/v1/jobs/{job_id}/downloads/{artifact}` | `mint_download` |
| [backend/app/main.py](../backend/app/main.py) | GET | `/v1/downloads/{token}` | `download` |
| [backend/app/remote_builds.py](../backend/app/remote_builds.py) | POST | `/poll` | `poll` |
| [backend/app/remote_builds.py](../backend/app/remote_builds.py) | GET | `/{job_id}/source` | `source` |
| [backend/app/remote_builds.py](../backend/app/remote_builds.py) | POST | `/{job_id}/log` | `log` |
| [backend/app/remote_builds.py](../backend/app/remote_builds.py) | POST | `/{job_id}/complete` | `complete` |

## File-based pages and handlers

- [web/app/page.tsx](../web/app/page.tsx)
- [web/app/v1/[...path]/route.ts](../web/app/v1/%5B...path%5D/route.ts)

## Existing documentation

Historical plans/reports describe their own revision; use current source when they disagree.

- [README.md](../README.md)
- [backend/requirements-dev.txt](../backend/requirements-dev.txt)
- [backend/requirements.txt](../backend/requirements.txt)
- [docs/architecture.md](../docs/architecture.md)
- [docs/deployment.md](../docs/deployment.md)
- [docs/dokploy.md](../docs/dokploy.md)
- [docs/native-worker.md](../docs/native-worker.md)
- [docs/security.md](../docs/security.md)
- [fixtures/README.md](../fixtures/README.md)

## Source and configuration map

Generated lockfiles, binary assets, compiled output, vendored dependencies, and cached bytecode are omitted. Test files remain listed so coverage can be located.

- [.env.example](../.env.example)
- [.github/workflows/checks.yml](../.github/workflows/checks.yml)
- [Dockerfile](../Dockerfile)
- [Makefile](../Makefile)
- [backend/.env.example](../backend/.env.example)
- [backend/app/__init__.py](../backend/app/__init__.py)
- [backend/app/config.py](../backend/app/config.py)
- [backend/app/db.py](../backend/app/db.py)
- [backend/app/domain.py](../backend/app/domain.py)
- [backend/app/inspection/__init__.py](../backend/app/inspection/__init__.py)
- [backend/app/inspection/deb.py](../backend/app/inspection/deb.py)
- [backend/app/main.py](../backend/app/main.py)
- [backend/app/models.py](../backend/app/models.py)
- [backend/app/native_artifact.py](../backend/app/native_artifact.py)
- [backend/app/queue.py](../backend/app/queue.py)
- [backend/app/recipes.py](../backend/app/recipes.py)
- [backend/app/remote_builds.py](../backend/app/remote_builds.py)
- [backend/app/repository.py](../backend/app/repository.py)
- [backend/app/runners/__init__.py](../backend/app/runners/__init__.py)
- [backend/app/runners/bwrap.py](../backend/app/runners/bwrap.py)
- [backend/app/runners/kubernetes.py](../backend/app/runners/kubernetes.py)
- [backend/app/runners/wasi.py](../backend/app/runners/wasi.py)
- [backend/app/security.py](../backend/app/security.py)
- [backend/app/services.py](../backend/app/services.py)
- [backend/app/storage.py](../backend/app/storage.py)
- [backend/app/worker.py](../backend/app/worker.py)
- [backend/fixtures/recipes/flexy-demo-1.0.0.json](../backend/fixtures/recipes/flexy-demo-1.0.0.json)
- [backend/migrations/env.py](../backend/migrations/env.py)
- [backend/migrations/versions/0001_initial.py](../backend/migrations/versions/0001_initial.py)
- [backend/migrations/versions/0002_remote_worker.py](../backend/migrations/versions/0002_remote_worker.py)
- [backend/pyproject.toml](../backend/pyproject.toml)
- [backend/requirements-dev.txt](../backend/requirements-dev.txt)
- [backend/requirements.txt](../backend/requirements.txt)
- [backend/tests/__init__.py](../backend/tests/__init__.py)
- [backend/tests/helpers.py](../backend/tests/helpers.py)
- [backend/tests/test_api_contract.py](../backend/tests/test_api_contract.py)
- [backend/tests/test_deb_inspection.py](../backend/tests/test_deb_inspection.py)
- [backend/tests/test_jobs_and_artifacts.py](../backend/tests/test_jobs_and_artifacts.py)
- [backend/tests/test_recipes.py](../backend/tests/test_recipes.py)
- [backend/tests/test_remote_builds.py](../backend/tests/test_remote_builds.py)
- [backend/tests/test_runner_integration.py](../backend/tests/test_runner_integration.py)
- [backend/tests/test_security.py](../backend/tests/test_security.py)
- [backend/tests/test_storage.py](../backend/tests/test_storage.py)
- [backend/tests/test_wasi_repack.py](../backend/tests/test_wasi_repack.py)
- [compose.check.yaml](../compose.check.yaml)
- [compose.dokploy.yaml](../compose.dokploy.yaml)
- [compose.personal.yaml](../compose.personal.yaml)
- [compose.yaml](../compose.yaml)
- [fixtures/recipes/flexy-demo-1.0.0.json](../fixtures/recipes/flexy-demo-1.0.0.json)
- [fixtures/source/flexy-demo.c](../fixtures/source/flexy-demo.c)
- [fixtures/source/unsupported-demo.c](../fixtures/source/unsupported-demo.c)
- [infra/ci/.env.example](../infra/ci/.env.example)
- [infra/dokploy/.env.example](../infra/dokploy/.env.example)
- [infra/k8s/base/api.yaml](../infra/k8s/base/api.yaml)
- [infra/k8s/base/cleanup-cronjob.yaml](../infra/k8s/base/cleanup-cronjob.yaml)
- [infra/k8s/base/frontend.yaml](../infra/k8s/base/frontend.yaml)
- [infra/k8s/base/ingress.yaml](../infra/k8s/base/ingress.yaml)
- [infra/k8s/base/kustomization.yaml](../infra/k8s/base/kustomization.yaml)
- [infra/k8s/base/limits.yaml](../infra/k8s/base/limits.yaml)
- [infra/k8s/base/migrate-job.yaml](../infra/k8s/base/migrate-job.yaml)
- [infra/k8s/base/namespace.yaml](../infra/k8s/base/namespace.yaml)
- [infra/k8s/base/networkpolicies.yaml](../infra/k8s/base/networkpolicies.yaml)
- [infra/k8s/base/networkpolicy-external-egress.example.yaml](../infra/k8s/base/networkpolicy-external-egress.example.yaml)
- [infra/k8s/base/rbac.yaml](../infra/k8s/base/rbac.yaml)
- [infra/k8s/base/runtime-config.yaml](../infra/k8s/base/runtime-config.yaml)
- [infra/k8s/base/serviceaccounts.yaml](../infra/k8s/base/serviceaccounts.yaml)
- [infra/k8s/base/services.yaml](../infra/k8s/base/services.yaml)
- [infra/k8s/base/worker.yaml](../infra/k8s/base/worker.yaml)
- [infra/k8s/overlays/production/flexy-runtime-secrets.example.yaml](../infra/k8s/overlays/production/flexy-runtime-secrets.example.yaml)
- [infra/k8s/overlays/production/kustomization.yaml](../infra/k8s/overlays/production/kustomization.yaml)
- [scripts/build_fixtures.py](../scripts/build_fixtures.py)
- [scripts/dokploy_compose.py](../scripts/dokploy_compose.py)
- [scripts/dokploy_env.py](../scripts/dokploy_env.py)
- [scripts/native_bridge.py](../scripts/native_bridge.py)
- [scripts/native_guest.py](../scripts/native_guest.py)
- [scripts/test_dokploy_compose.py](../scripts/test_dokploy_compose.py)
- [scripts/test_dokploy_env.py](../scripts/test_dokploy_env.py)
- [scripts/test_dokploy_images.py](../scripts/test_dokploy_images.py)
- [scripts/test_native_bridge.py](../scripts/test_native_bridge.py)
- [web/.eslintrc.json](../web/.eslintrc.json)
- [web/app/globals.css](../web/app/globals.css)
- [web/app/layout.tsx](../web/app/layout.tsx)
- [web/app/page.tsx](../web/app/page.tsx)
- [web/app/v1/[...path]/route.ts](../web/app/v1/%5B...path%5D/route.ts)
- [web/components/conversion-workbench.tsx](../web/components/conversion-workbench.tsx)
- [web/e2e/conversion.spec.ts](../web/e2e/conversion.spec.ts)
- [web/eslint.config.mjs](../web/eslint.config.mjs)
- [web/lib/api-proxy.ts](../web/lib/api-proxy.ts)
- [web/lib/api.ts](../web/lib/api.ts)
- [web/lib/session.ts](../web/lib/session.ts)
- [web/lib/types.ts](../web/lib/types.ts)
- [web/next-env.d.ts](../web/next-env.d.ts)
- [web/next.config.ts](../web/next.config.ts)
- [web/package.json](../web/package.json)
- [web/playwright.config.ts](../web/playwright.config.ts)
- [web/tests/api-proxy.test.ts](../web/tests/api-proxy.test.ts)
- [web/tests/api.test.ts](../web/tests/api.test.ts)
- [web/tests/conversion-workbench.test.tsx](../web/tests/conversion-workbench.test.tsx)
- [web/tests/setup.ts](../web/tests/setup.ts)
- [web/tsconfig.json](../web/tsconfig.json)
- [web/vitest.config.ts](../web/vitest.config.ts)
