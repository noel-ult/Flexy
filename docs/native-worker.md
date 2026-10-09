# Offline laptop Arch worker

`BUILD_EXECUTOR=remote` is opt-in and supports **only the exact Flexy demo recipe**.
The VPS keeps inspection, job metadata and private artifacts. A laptop bridge
makes outbound HTTPS requests to `/v1/native-worker`; no VPS database, Redis,
MinIO, SSH or Docker ports are exposed. Only one native build runs at a time;
at most three jobs may wait/run. An offline bridge rejects new builds with a
readable error. Jobs time out after a 120-second lease/wait, not fake success.

## Prerequisites and trust boundary

Prepare a patched **x86_64 Arch VM**, 2 GiB RAM, 2 vCPUs, with makepkg, pacman,
fakeroot, Bubblewrap and unshare. Validate nested user/mount/PID/network
namespaces before enabling the executor. Run QEMU unprivileged, restrict its
filesystem to its own disk directory, use `restrict=on` networking and only
`hostfwd=tcp:127.0.0.1:22222-:22`. Do not share host folders, USB devices,
credentials or engine sockets. Do not enable guest Internet access.

The trusted guest code below must be installed by the operator, **not downloaded
from a job**. The bridge must retain an SSH host key pinned during trusted VM
provisioning. Guest account is `flexy`; never reuse your laptop login/key.

Copy this reviewed subset to `/home/flexy/runner` inside the guest:

- `backend/app/{__init__,config,recipes}.py`
- `backend/app/inspection/{__init__,deb}.py`
- `backend/app/runners/{__init__,bwrap,kubernetes}.py`
- `backend/fixtures/recipes/flexy-demo-1.0.0.json`
- `scripts/native_guest.py` as `/home/flexy/runner/native_guest.py`

No API/storage secrets or complete repository/environment files enter the VM.
The guest command consumes `.deb` data on stdin and reinspects the exact recipe;
it will not accept a remote command, PKGBUILD, URL or script. Native work is
limited to 30 seconds, 1 MiB source/expanded data, 100 files and 64 KiB logs.
Only job-owned guest directories are writable and are deleted afterward.

## Backend configuration

Run Alembic migrations through the existing migration service. Set only in your
Flexy backend service:

```dotenv
BUILD_EXECUTOR=remote
REMOTE_BUILD_TOKEN=<a-new-unique-43-character-URL-safe-secret>
```

Generate the token with `secrets.token_urlsafe(32)` into a private file; never
commit or paste it into chat. Deploy the same configuration to API and the
existing inspection worker. The inspection worker does not consume native jobs.
The default remains fail-closed (`unavailable`). No shared VPS security policy
changes, privileged containers or extra public ports are required.

## Laptop configuration and operation

Use a private `~/.local/state/flexy-vm` directory (0700), with private regular
files (0600) `worker-token`, `id_ed25519` and `known_hosts`. The token must match
the backend value. Keep it on the laptop filesystem, **outside QEMU's sandbox**.

Start your VM, then from this repository:

```bash
python3 scripts/native_bridge.py --once
python3 scripts/native_bridge.py
```

The default backend is `https://flexy.noelbiju.in`; use `--origin` for another
HTTPS origin. Redirects and environment HTTP proxies are disabled to avoid
forwarding credentials elsewhere. The bridge opens no inbound port. It executes
only a fixed guest command via pinned loopback SSH. Ctrl+C stops polling; already
claimed work must finish or expire before another claim. VM disk/SSD must remain
mounted. Never disconnect the SSD until the VM has shut down cleanly.

An optional user service is supplied in `infra/systemd/flexy-native-bridge.service`.
It assumes the checkout is `~/Flexy` and caps the host bridge at 128 MiB and
one quarter CPU (the separate VM has its own 2-GiB/2-vCPU budget). Install it in
`~/.config/systemd/user`, reload user units and start it with
`systemctl --user start flexy-native-bridge`. It need not be enabled at login;
start the VM first. Inspect with `systemctl --user status flexy-native-bridge`
and stop it with `systemctl --user stop flexy-native-bridge` before VM shutdown.

Upload the supported `.deb`, analyze it, then start a new build. The existing
website displays live logs, four separate results and scoped package/report
downloads. Old reports remain unchanged: local verification does not retroactively
prove that an earlier website job ran these checks.

## What the checks prove

- Package creation: real makepkg output; server statically rechecks bounded
  archive paths, file list, dependency metadata, exact recipe hashes/modes.
- Installation: pacman installs in an empty disposable verification root with
  dependency resolution **disabled**. This does not test a clean Arch dependency
  installation. The VM's trusted runtime provides glibc to subsequent checks.
- Launch: the exact demo `--version` command succeeds with expected output.
- Functionality: the exact demo `--self-test` succeeds with expected output.

Worker results are authenticated operator attestations, not independently
cryptographic proof of execution. Protect/rotate the dedicated worker token.
The server rejects replayed, expired or cross-job leases and inconsistent results.
Neither passing CLI checks nor a successful package guarantees desktop behavior,
arbitrary application support or Windows/macOS compatibility.

Public traffic can still consume the existing inspection resources and limited
native queue. Add access control/rate limits before broader use on a shared VPS.
One-job concurrency and queue limits are not user authentication or disk quotas.
Disconnect/disable by stopping the laptop bridge and setting the backend executor
back to `wasi` (package creation only) or `unavailable`, then redeploying Flexy.
