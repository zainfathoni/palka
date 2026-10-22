#!/usr/bin/env python3
"""Handle one Amp Orb lifecycle job with the local Docker CLI."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time


BOOTSTRAP = """set -euo pipefail
command -v amp >/dev/null
umask 077
state=/tmp/.palka-bootstrap
mkdir -p "$state"
exec 9>"$state/lock"
flock -x -w 5 9
start_time() {
    local stat
    local -a fields
    read -r stat < "/proc/$1/stat"
    read -ra fields <<< "${stat##*) }"
    printf '%s' "${fields[19]}"
}
export -f start_time
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
setsid bash -euc '
    state=/tmp/.palka-bootstrap
    stamp=$(start_time "$$")
    printf "%s %s\\n" "$$" "$stamp" > "$state/pid.next"
    mv "$state/pid.next" "$state/pid"
    exec 9>&-
    exec amp orb bootstrap
' </dev/null >/dev/null 2>&1 &
pid=$!
sleep 0.1
kill -0 "$pid"
"""


class DispatchError(Exception):
    """A safe message that can appear in provider and thread logs."""


@contextmanager
def lock(name: str, deadline: float):
    root = Path(os.environ.get("PALKA_STATE_DIR") or Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "palka")
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(root / (name + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError:
        raise DispatchError("local state lock could not open") from None
    try:
        while True:
            if time.monotonic() >= deadline:
                raise DispatchError("job timed out waiting for local state lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        yield root
    finally:
        # Keep the file. Removing it can give waiting jobs different lock inodes.
        os.close(fd)


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

    try:
        budget = float(os.environ.get("PALKA_JOB_TIMEOUT_SECONDS", "240"))
    except ValueError:
        raise DispatchError("PALKA_JOB_TIMEOUT_SECONDS must be greater than zero and at most 240") from None
    if not math.isfinite(budget) or not 0 < budget <= 240:
        raise DispatchError("PALKA_JOB_TIMEOUT_SECONDS must be greater than zero and at most 240")
    deadline = time.monotonic() + budget
    with lock("orb-" + orb_id, deadline) as state:
        dispatch(operation, orb_id, image, deadline, state)


def dispatch(operation: str, orb_id: str, image: str, deadline: float, state: Path) -> None:

    def docker(*args: str) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DispatchError("job timed out")
        try:
            process = subprocess.Popen(
                ["docker", *args],
                env={**os.environ, "AMP_URL": os.environ.get("AMP_URL", "")},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, errors="replace", start_new_session=True,
            )
        except OSError:
            raise DispatchError("Docker CLI could not start") from None
        try:
            stdout, _ = process.communicate(timeout=remaining)
        except BaseException as error:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            if isinstance(error, subprocess.TimeoutExpired):
                raise DispatchError("Docker command timed out; inspect the Orb before retrying") from None
            raise
        if process.returncode:
            # Docker errors can include input values or output from the image.
            raise DispatchError(f"Docker {args[0]} failed (exit {process.returncode})")
        return stdout.strip()

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

    pending = state / ("orb-" + orb_id + ".pending")

    def transition(action: str) -> None:
        temporary = pending.with_suffix(".next")
        with temporary.open("w") as record:
            json.dump({"action": action, "container": container_id}, record)
            record.flush()
            os.fsync(record.fileno())
        temporary.replace(pending)
        directory = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        if action == "stop":
            docker("stop", "--time", "10", container_id)
        else:
            docker("start", container_id)
        pending.unlink()

    if pending.exists():
        previous = json.loads(pending.read_text())
        if previous.get("action") not in {"start", "stop"}:
            raise DispatchError("local recovery record is invalid; inspect local state")
        if container_id:
            if previous.get("container") != container_id:
                raise DispatchError("container changed during recovery; inspect local state")
            transition(previous["action"])
            running = previous["action"] == "start"
        else:
            pending.unlink()

    snapshot_id = os.environ.get("ORB_SNAPSHOT_ID", "")
    digest = hashlib.sha256(snapshot_id.encode()).hexdigest()
    reference = "palka-snapshot:" + digest

    def snapshot_image() -> str:
        found = docker("image", "ls", "--no-trunc", "--quiet", "--filter", "reference=" + reference)
        if not found:
            return ""
        saved = json.loads(docker("image", "inspect", reference))[0]
        labels = saved["Config"].get("Labels") or {}
        if labels.get("dev.palka.snapshot") != "true" or labels.get("dev.palka.snapshot-id") != digest:
            raise DispatchError("snapshot name is in use by an unowned resource")
        return saved["Id"]

    def check_disk() -> None:
        current = json.loads(docker("inspect", container_id))[0]
        if current["Config"].get("Volumes") or any(mount["Type"] != "tmpfs" for mount in current["Mounts"]):
            raise DispatchError("mounted disk content is not supported; use an image without VOLUME declarations")
        for value in current["Config"].get("Env") or []:
            key, _, secret = value.partition("=")
            if key in {"ORB_TOKEN", "AMP_ORB_PROVIDER_TOKEN"} and secret:
                raise DispatchError("image configuration contains a credential; use a clean base image")

    if operation == "spawn":
        if not container_id:
            if snapshot_id:
                with lock("snapshot-" + digest, deadline):
                    image = snapshot_image() or image
            container_id = docker(
                "create", "--init", "--platform", "linux/amd64", "--name", name,
                "--label", "dev.palka.managed=true", "--label", "dev.palka.orb-id=" + orb_id,
                "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777",
                "--entrypoint", "sleep", "--", image, "infinity",
            )
        check_disk()
        if not running:
            transition("start")
        docker("exec", "--env", "ORB_TOKEN", "--env", "AMP_URL", container_id, "bash", "-c", BOOTSTRAP)
    elif operation == "pause" and container_id:
        if snapshot_id:
            check_disk()
        if running:
            transition("stop")
        if snapshot_id:
            with lock("snapshot-" + digest, deadline):
                snapshot_image()  # Refuse to replace an unowned tag.
                saved_id = docker(
                    "commit", "--change", "LABEL dev.palka.snapshot=true",
                    "--change", "LABEL dev.palka.snapshot-id=" + digest, container_id,
                )
                docker("image", "tag", saved_id, reference)
    elif operation == "delete" and container_id:
        docker("rm", "--force", "--volumes", container_id)

    print(f"palka: {operation} complete")


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise DispatchError("job interrupted; retry to check the owned resource")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        main()
    except DispatchError as error:
        print(f"palka: {error}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError, TypeError):
        print("palka: runtime or local state could not be read or saved; inspect it before retrying", file=sys.stderr)
        sys.exit(1)
