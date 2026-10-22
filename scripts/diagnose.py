#!/usr/bin/env python3
"""Report selected local runtime facts without activation or raw runtime output."""

from functools import partial
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dispatch import DispatchError, Limits, docker_command, state_path


def report():
    docker = partial(docker_command, time.monotonic() + 20)
    result = {"python_version": platform.python_version(), "runtime_reachable": False}
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5,
    )
    value = revision.stdout.strip()
    result["source_revision"] = value if re.fullmatch(r"[0-9a-f]{40,64}", value) else "unknown"
    try:
        version = json.loads(docker("version", "--format", "{{json .}}"))
        result["runtime_reachable"] = True
        for side in ("Client", "Server"):
            value = version[side]["Version"]
            match = re.match(r"[0-9]+(?:\.[0-9]+){1,3}", value)
            result["docker_" + side.lower() + "_version"] = match[0] if match else "unknown"
        limits = Limits.load()
        result["limits"] = {
            key: value for key, value in vars(limits).items() if key != "storage_path"
        }
        result["storage"] = limits.storage(docker)
        states = docker("ps", "--all", "--filter", "label=dev.palka.managed=true", "--format", "{{.State}}").splitlines()
        result["orbs"] = {"total": len(states), "running": states.count("running")}
        result["snapshot_images"] = len(set(docker(
            "image", "ls", "--all", "--quiet", "--no-trunc",
            "--filter", "label=dev.palka.snapshot=true",
        ).splitlines()))
        root = state_path()
        summaries = {}
        result["last_operations"] = summaries
        result["spawn_reservations"] = 0
        if root.exists():
            info = root.stat()
            if root.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise DispatchError("Palka state directory must be private and owned by the dispatch user")
            result["spawn_reservations"] = len(list(root.glob("orb-*.reservation")))
            for path in root.glob("orb-*.result"):
                record = json.loads(path.read_text())
                operation = record["operation"]
                seconds = record["seconds"]
                if (operation not in {"spawn", "pause", "delete"}
                        or type(record["complete"]) is not bool
                        or type(seconds) not in {int, float}
                        or not math.isfinite(seconds) or seconds < 0):
                    raise DispatchError("local operation result is invalid; inspect local state")
                summary = summaries.setdefault(operation, {"count": 0, "failed": 0, "max_seconds": 0})
                summary["count"] += 1
                summary["failed"] += not record["complete"]
                summary["max_seconds"] = max(summary["max_seconds"], seconds)
        result["ok"] = True
    except DispatchError as error:
        result.update(ok=False, error=str(error))
    except (OSError, ValueError, KeyError, TypeError):
        result.update(ok=False, error="runtime or local summary could not be read; inspect it locally")
    return result


if __name__ == "__main__":
    try:
        if len(sys.argv) != 1:
            raise DispatchError("diagnose takes no arguments")
        result = report()
    except (DispatchError, OSError, subprocess.TimeoutExpired):
        result = {"ok": False, "error": "diagnostic command could not complete; inspect local prerequisites"}
    print(json.dumps(result, sort_keys=True))
    sys.exit(0 if result["ok"] else 1)
