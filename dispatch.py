#!/usr/bin/env python3
"""Handle one Amp Orb lifecycle job with the local Docker CLI."""

import json
import os
import re
import subprocess
import sys
import time


BOOTSTRAP = """set -euo pipefail
command -v amp >/dev/null
umask 077
state=/tmp/.palka-bootstrap
mkdir -p "$state"
start_time() {
    local stat
    local -a fields
    read -r stat < "/proc/$1/stat"
    read -ra fields <<< "${stat##*) }"
    printf '%s' "${fields[19]}"
}
if [[ -f "$state/pid" ]]; then
    read -r old stamp < "$state/pid"
    [[ "$old" =~ ^[0-9]+$ && "$stamp" =~ ^[0-9]+$ ]] && (( old > 1 )) || exit 1
    if current=$(start_time "$old" 2>/dev/null) && [[ "$current" == "$stamp" ]]; then
        kill -TERM -- "-$old" || true
        for ((i=0; i<20; i++)); do
            kill -0 -- "-$old" 2>/dev/null || break
            sleep 0.05
        done
        if kill -0 -- "-$old" 2>/dev/null; then
            kill -KILL -- "-$old"
        fi
    fi
fi
setsid amp orb bootstrap </dev/null >/dev/null 2>&1 &
pid=$!
stamp=$(start_time "$pid")
printf '%s %s\\n' "$pid" "$stamp" > "$state/pid"
sleep 0.1
kill -0 "$pid"
"""


class DispatchError(Exception):
    """A safe message that can appear in provider and thread logs."""


def main() -> None:
    if len(sys.argv) != 1:
        raise DispatchError("dispatch takes no arguments")
    operation = os.environ.get("ORB_OPERATION", "")
    orb_id = os.environ.get("ORB_ID", "")
    if operation not in {"spawn", "pause", "delete"}:
        raise DispatchError("ORB_OPERATION must be spawn, pause, or delete")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", orb_id):
        raise DispatchError("ORB_ID must be 1 to 128 letters, digits, dots, underscores, or hyphens; start with a letter or digit")
    image = os.environ.get("ORB_IMAGE", "")
    if operation == "spawn":
        if not image or image.startswith("-"):
            raise DispatchError("spawn requires ORB_IMAGE")
        if not os.environ.get("ORB_TOKEN"):
            raise DispatchError("spawn requires ORB_TOKEN")

    deadline = time.monotonic() + 240

    def docker(*args: str) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DispatchError("job timed out")
        try:
            result = subprocess.run(
                ["docker", *args],
                env={**os.environ, "AMP_URL": os.environ.get("AMP_URL", "")},
                capture_output=True,
                text=True,
                timeout=remaining,
            )
        except subprocess.TimeoutExpired:
            raise DispatchError("Docker command timed out; inspect the Orb before retrying") from None
        except OSError:
            raise DispatchError("Docker CLI could not start") from None
        if result.returncode:
            # Docker errors can include input values or output from the image.
            raise DispatchError(f"Docker {args[0]} failed (exit {result.returncode})")
        return result.stdout.strip()

    name = "palka-" + orb_id
    container_id = docker(
        "container", "ls", "--all", "--no-trunc",
        "--filter", "name=^/" + re.escape(name) + "$", "--format", "{{.ID}}",
    )
    running = False
    if container_id:
        container = json.loads(docker("inspect", container_id))[0]
        labels = container["Config"].get("Labels") or {}
        if labels.get("dev.palka.managed") != "true" or labels.get("dev.palka.orb-id") != orb_id:
            raise DispatchError("container name is in use by an unowned resource")
        running = container["State"]["Running"]

    if operation == "spawn":
        if not container_id:
            container_id = docker(
                "create", "--init", "--platform", "linux/amd64", "--name", name,
                "--label", "dev.palka.managed=true", "--label", "dev.palka.orb-id=" + orb_id,
                "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777",
                "--entrypoint", "sleep", "--", image, "infinity",
            )
        if not running:
            docker("start", container_id)
        docker("exec", "--env", "ORB_TOKEN", "--env", "AMP_URL", container_id, "bash", "-c", BOOTSTRAP)
    elif operation == "pause" and container_id and running:
        docker("stop", "--time", "10", container_id)
    elif operation == "delete" and container_id:
        docker("rm", "--force", "--volumes", container_id)

    if os.environ.get("ORB_SNAPSHOT_ID") and operation in {"spawn", "pause"}:
        print("palka: project snapshots are not supported in this version", file=sys.stderr)
    print(f"palka: {operation} complete")


if __name__ == "__main__":
    try:
        main()
    except DispatchError as error:
        print(f"palka: {error}", file=sys.stderr)
        sys.exit(1)
