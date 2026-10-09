"""Outbound HTTPS bridge to an offline laptop Arch VM. No host package execution.

Requires Python 3.11+, a pinned loopback SSH host key, and a private token file.
The only guest command is reviewed native_guest.py; remote jobs cannot choose it.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LIMIT = 1024 * 1024
RECIPE = "flexy-demo-1.0.0-amd64-arch-x86_64"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError(
            "Worker redirects are forbidden; credentials remain on the configured host."
        )


def private_file(path):
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise RuntimeError(
            "Worker credentials and SSH host keys must be owned private regular files."
        )


class Bridge:
    def __init__(self, origin, state):
        url = urllib.parse.urlsplit(origin)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise RuntimeError(
                "Backend must be an HTTPS origin, without credentials/path/query."
            )
        if (
            state.is_symlink()
            or state.stat().st_uid != os.getuid()
            or state.stat().st_mode & 0o077
        ):
            raise RuntimeError("Worker state directory must be owned private (0700).")
        self.state = state
        for name in ("worker-token", "id_ed25519", "known_hosts"):
            private_file(state / name)
        self.token = (state / "worker-token").read_text().strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", self.token):
            raise RuntimeError("Worker token must be a strong URL-safe secret.")
        self.origin = origin.rstrip("/")
        self.http = urllib.request.build_opener(
            NoRedirect, urllib.request.ProxyHandler({})
        )
        self.ssh = [
            "ssh",
            "-F",
            "/dev/null",
            "-i",
            str(state / "id_ed25519"),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "ForwardAgent=no",
            "-o",
            "ClearAllForwardings=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UserKnownHostsFile=" + str(state / "known_hosts"),
            "-o",
            "ConnectTimeout=5",
            "-o",
            "ServerAliveInterval=5",
            "-o",
            "ServerAliveCountMax=2",
            "-p",
            "22222",
            "flexy@127.0.0.1",
        ]

    def request(self, path, body=None, lease=None, binary=False):
        headers = {
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json",
        }
        if lease:
            headers["X-Build-Lease"] = lease
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.origin + path, data=data, headers=headers)
        with self.http.open(request, timeout=15) as response:
            content = response.read(LIMIT + 1)
            if len(content) > LIMIT:
                raise RuntimeError("Worker response exceeded limit.")
            return content if binary else json.loads(content)

    def check_guest(self):
        subprocess.run(
            self.ssh
            + [
                (
                    'test "$(uname -m)" = x86_64 && test -x /usr/bin/bwrap '
                    "&& test -x /usr/bin/makepkg && test -f /home/flexy/runner/native_guest.py"
                )
            ],
            check=True,
            timeout=10,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def execute(self, job):
        job_id, lease = job["id"], job["lease"]
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{32}", job_id)
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", lease)
            or job["recipeId"] != RECIPE
            or not 0 < job["size"] <= LIMIT
        ):
            raise RuntimeError("Invalid native claim contract.")
        path = "/v1/native-worker/" + job_id
        source = self.request(path + "/source", lease=lease, binary=True)
        if (
            len(source) != job["size"]
            or hashlib.sha256(source).hexdigest() != job["sha256"]
        ):
            raise RuntimeError("Native source digest mismatch.")
        # Reviewed guest command; stdin carries only data. A host-owned temporary
        # file avoids pipe deadlocks. No downloaded scripts are run on the laptop.
        with tempfile.TemporaryFile() as incoming, tempfile.TemporaryFile() as errors:
            incoming.write(source)
            incoming.seek(0)
            process = subprocess.Popen(
                self.ssh
                + [
                    "timeout --kill-after=2s 45s python3 /home/flexy/runner/native_guest.py"
                ],
                stdin=incoming,
                stdout=subprocess.PIPE,
                stderr=errors,
            )
            final = None
            used = 0
            started = time.monotonic()
            try:
                assert process.stdout is not None
                while True:
                    line = process.stdout.readline(2 * LIMIT + 1)
                    if not line:
                        break
                    used += len(line)
                    if used > 2 * LIMIT or time.monotonic() - started > 75:
                        raise RuntimeError("Native output/duration limit exceeded.")
                    event = json.loads(line)
                    if "log" in event:
                        self.request(
                            path + "/log", {"message": event["log"][:2000]}, lease
                        )
                    elif "result" in event and final is None:
                        final = event["result"]
                    else:
                        raise RuntimeError("Invalid guest evidence stream.")
                process.wait(timeout=5)
                if process.returncode != 0 or final is None:
                    raise RuntimeError("Native guest failed without a complete result.")
                self.request(path + "/complete", final, lease)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="https://flexy.noelbiju.in")
    parser.add_argument(
        "--state", type=Path, default=Path.home() / ".local/state/flexy-vm"
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    bridge = Bridge(args.origin, args.state)
    with (args.state / "bridge.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                bridge.check_guest()
                claimed = bridge.request("/v1/native-worker/poll", {})["job"]
                if claimed:
                    print(
                        "Native job claimed; executing only inside offline VM.",
                        flush=True,
                    )
                    bridge.execute(claimed)
                    print("Native result accepted by backend.", flush=True)
                elif args.once:
                    print(
                        "VM healthy; authenticated backend worker connection established."
                    )
            except Exception as error:  # noqa: BLE001 - fail closed, never expose HTTP secrets
                # Never print HTTP request objects, bearer tokens or response bodies.
                print(
                    f"Worker check failed safely ({type(error).__name__}); no success claimed.",
                    flush=True,
                )
                if args.once:
                    raise SystemExit(1) from None
            if args.once:
                return
            time.sleep(5)


if __name__ == "__main__":
    main()
