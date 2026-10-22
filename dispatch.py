#!/usr/bin/env python3
"""Handle one Amp Orb lifecycle job with the local Docker CLI."""

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
from functools import partial
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


class DockerFailure(DispatchError):
    def __init__(self, message: str, daemon_response: bool):
        super().__init__(message)
        self.daemon_response = daemon_response


@dataclass(frozen=True)
class Limits:
    cpus: float
    memory_bytes: int
    pids_limit: int
    max_running: int
    min_free_bytes: int
    max_used_bytes: int
    storage_path: Path

    @classmethod
    def load(cls):
        def positive(name):
            key = "PALKA_" + name
            try:
                value = int(os.environ.get(key, ""))
            except ValueError:
                raise DispatchError(key + " must be a positive integer") from None
            if value <= 0:
                raise DispatchError(key + " must be a positive integer")
            return value

        try:
            cpus = float(os.environ.get("PALKA_CPUS", ""))
        except ValueError:
            raise DispatchError("PALKA_CPUS must be a positive finite CPU count") from None
        if not math.isfinite(cpus) or cpus < 0.000000001:
            raise DispatchError("PALKA_CPUS must be a positive finite CPU count")
        memory = positive("MEMORY_BYTES")
        if memory < 6 * 1024**2:
            raise DispatchError("PALKA_MEMORY_BYTES must be at least 6 MiB")
        path = Path(os.environ.get("PALKA_STORAGE_PATH", ""))
        if not path.is_absolute() or not path.is_dir():
            raise DispatchError("PALKA_STORAGE_PATH must be an existing absolute directory")
        return cls(cpus, memory, positive("PIDS_LIMIT"), positive("MAX_RUNNING"),
                   positive("MIN_FREE_BYTES"), positive("MAX_USED_BYTES"), path)

    def check_container(self, host):
        if (host["NanoCpus"] != int(self.cpus * 1_000_000_000)
                or host["Memory"] != self.memory_bytes or host["MemorySwap"] != self.memory_bytes
                or host["PidsLimit"] != self.pids_limit
                or host["LogConfig"] != {"Type": "local", "Config": {"max-size": "5m", "max-file": "2"}}):
            raise DispatchError("existing Orb limits differ; restore its approved configuration before resume")

    def storage(self, docker):
        info = json.loads(docker("info", "--format", "{{json .}}"))
        if info["OSType"] != "linux" or info["CgroupVersion"] != "2" or not all(
            info[key] for key in ("MemoryLimit", "SwapLimit", "PidsLimit", "CpuCfsQuota")
        ):
            raise DispatchError("Docker requires Linux cgroup v2 with CPU, memory, swap, and PID limit support")
        if self.storage_path.stat().st_dev != Path(info["DockerRootDir"]).stat().st_dev:
            raise DispatchError("storage accounting path is not on the Docker data filesystem")
        usage = os.statvfs(self.storage_path)
        return {
            "total_bytes": usage.f_blocks * usage.f_frsize,
            "used_bytes": (usage.f_blocks - usage.f_bfree) * usage.f_frsize,
            "available_bytes": usage.f_bavail * usage.f_frsize,
            "scope": "whole-filesystem", "hard_disk_quota": False,
        }

    def check_storage(self, docker):
        usage = self.storage(docker)
        if usage["available_bytes"] < self.min_free_bytes:
            raise DispatchError("storage reserve reached; no new growth admitted")
        if usage["used_bytes"] >= self.max_used_bytes:
            raise DispatchError("storage usage budget reached; no new growth admitted")


def state_path() -> Path:
    root = Path(os.environ.get("PALKA_STATE_DIR") or Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "palka")
    if not root.is_absolute():
        raise DispatchError("Palka state directory must be an absolute local path")
    return root


@contextmanager
def lock(name: str, deadline: float):
    root = state_path()
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = root.stat()
        if root.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise DispatchError("Palka state directory must be private and owned by the dispatch user")
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

    limits = Limits.load() if operation == "spawn" or (operation == "pause" and os.environ.get("ORB_SNAPSHOT_ID")) else None
    try:
        budget = float(os.environ.get("PALKA_JOB_TIMEOUT_SECONDS", "240"))
    except ValueError:
        raise DispatchError("PALKA_JOB_TIMEOUT_SECONDS must be greater than zero and at most 240") from None
    if not math.isfinite(budget) or not 0 < budget <= 240:
        raise DispatchError("PALKA_JOB_TIMEOUT_SECONDS must be greater than zero and at most 240")
    started = time.monotonic()
    deadline = started + budget
    with lock("orb-" + orb_id, deadline) as state:
        complete = False
        try:
            dispatch(operation, orb_id, image, deadline, state, limits)
            complete = True
        finally:
            temporary = state / ("orb-" + orb_id + ".result.next")
            temporary.write_text(json.dumps({
                "operation": operation, "complete": complete,
                "seconds": round(time.monotonic() - started, 3),
            }) + "\n")
            temporary.replace(state / ("orb-" + orb_id + ".result"))


def docker_command(deadline: float, *args: str) -> str:
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
        stdout, stderr = process.communicate(timeout=remaining)
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
        # Recognize only the CLI's explicit Engine response prefix. A changed
        # format or a transport failure stays uncertain. Never emit the text.
        raise DockerFailure(f"Docker {args[0]} failed (exit {process.returncode})",
                            stderr.startswith("Error response from daemon:"))
    return stdout.strip()


def dispatch(operation: str, orb_id: str, image: str, deadline: float, state: Path, limits: Limits | None) -> None:
    docker = partial(docker_command, deadline)

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
            limits.check_container(container["HostConfig"])

    reservation = state / ("orb-" + orb_id + ".reservation")
    if operation == "spawn":
        with lock("admission", deadline):
            limits.check_storage(docker)
            occupied = set(docker("ps", "--filter", "label=dev.palka.managed=true", "--format", '{{.Label "dev.palka.orb-id"}}').splitlines())
            occupied.update(path.name[len("orb-"):-len(".reservation")] for path in state.glob("orb-*.reservation"))
            if orb_id not in occupied and len(occupied) >= limits.max_running:
                raise DispatchError("running-Orb limit reached; retained disks are unchanged")
            reservation.touch(mode=0o600)

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
        try:
            if action == "stop":
                docker("stop", "--time", "10", container_id)
            else:
                docker("start", container_id)
        except DockerFailure as error:
            if error.daemon_response:
                pending.unlink()
            raise
        pending.unlink()

    if pending.exists():
        previous = json.loads(pending.read_text())
        if previous.get("action") not in {"start", "stop"}:
            raise DispatchError("local recovery record is invalid; inspect local state")
        if container_id:
            if previous.get("container") != container_id:
                raise DispatchError("container changed during recovery; inspect local state")
            if operation == "delete":
                pass  # Removal targets this immutable ID, not a repeat start.
            elif previous["action"] == "start" and running:
                # Only one start was sent. The locked inspect observed its
                # completed running state; do not send a second request.
                pending.unlink()
            else:
                raise DispatchError("prior Docker transition is uncertain; operator quiescence is required before retry")
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
                "--cpus", str(limits.cpus), "--memory", str(limits.memory_bytes),
                "--memory-swap", str(limits.memory_bytes), "--pids-limit", str(limits.pids_limit),
                "--log-driver", "local", "--log-opt", "max-size=5m", "--log-opt", "max-file=2",
                "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777,size=" + str(min(limits.memory_bytes // 2, 256 * 1024**2)),
                "--entrypoint", "sleep", "--", image, "infinity",
            )
        check_disk()
        if not running:
            limits.check_storage(docker)
            transition("start")
        with lock("admission", deadline):
            reservation.unlink(missing_ok=True)
        docker("exec", "--env", "ORB_TOKEN", "--env", "AMP_URL", container_id, "bash", "-c", BOOTSTRAP)
    elif operation == "pause" and container_id:
        if snapshot_id:
            check_disk()
        if running:
            transition("stop")
        if snapshot_id:
            with lock("snapshot-" + digest, deadline):
                limits.check_storage(docker)
                snapshot_image()  # Refuse to replace an unowned tag.
                saved_id = docker(
                    "commit", "--change", "LABEL dev.palka.snapshot=true",
                    "--change", "LABEL dev.palka.snapshot-id=" + digest, container_id,
                )
                docker("image", "tag", saved_id, reference)
    elif operation == "delete" and container_id:
        docker("rm", "--force", "--volumes", container_id)
        pending.unlink(missing_ok=True)

    if operation in {"pause", "delete"}:
        with lock("admission", deadline):
            reservation.unlink(missing_ok=True)
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
