# Initial Docker lifecycle

This is the first implementation slice, not a production-ready Orb provider.
It implements task [#3](https://github.com/zainfathoni/palka/issues/3).
Do not activate a live provider with this version.

## Requirements

- A Linux x64 host with Python 3.11 or later and the Docker CLI and daemon.
- Permission to manage local Docker containers. This grants broad host access.
- A Linux amd64/glibc image that meets the current
  [Amp image requirements](https://ampcode.com/docs/orbs/self-hosted).
- Bash, coreutils, util-linux `setsid`, and `amp` inside the image. Bootstrap
  runs as the image's default user. No root-user override is added.

The dispatch uses Python's standard library and the Docker CLI. It has no
Python package dependencies or local database.

## Dispatch interface

`dispatch.py` is executable and takes no arguments. The caller supplies the
[Amp job environment](runtime-contract.md#dispatch-input). A direct local
call uses:

```sh
./dispatch.py
```

This command operates on the Orb identified by its environment. A delete
job removes that Orb's writable disk and anonymous volumes. Only use it with
an explicit data-removal scope. The integration tests supply synthetic jobs
and remove only their own disposable resources.

`ORB_ID` must contain 1 to 128 ASCII letters, digits, dots, underscores, or
hyphens, with a letter or digit first. The Docker name is `palka-` followed
by this ID. The container must also have the matching `dev.palka.managed`
and `dev.palka.orb-id` labels. A name collision with an unowned resource is
an error for every operation. Labels identify resources; they are not a
security boundary against another user with Docker access.

- `spawn` creates or starts the same owned container. It uses `sleep infinity`
  as the entrypoint and Docker's init process. It starts detached bootstrap
  with the current token and server override. An unset `AMP_URL` clears any
  image default or prior override.
- A serial repeat `spawn` replaces only the prior bootstrap process group.
  It preserves the running container and unrelated Orb processes. The PID
  record includes a process start time to avoid selecting a reused PID.
- `pause` stops the container with a ten-second stop grace and keeps its disk.
- `delete` removes the owned container, its writable disk, and its anonymous
  volumes. Repeat pause or delete of an absent Orb succeeds.

The writable layer is retained on stop/start. `/tmp` is a tmpfs mount and is
cleared on stop, including the bootstrap PID record. Running processes and
RAM are not retained. No host mounts, runtime socket, privileged mode, or
provider credential are passed to the Orb.

## Credentials and output

Spawn credentials enter only the bootstrap exec environment, not container
creation arguments or persistent container configuration. A process with
host or Docker authority can still inspect exec environments. This version
does not claim to protect credentials from a host administrator or from code
running as the bootstrap user inside its own Orb.

Bootstrap stdout and stderr are discarded. Docker command output is captured,
and failures report only the operation stage and exit code. This limits
diagnostics but prevents image output or Docker errors from copying input
credentials into provider and thread logs. Investigate runtime state privately
when a sanitized error is not enough. No environment dump is produced.

## Current limits

- Each job shares a 240-second command budget. A timeout fails the dispatch;
  killing the Docker client does not guarantee that the daemon cancelled work.
  Inspect the owned resource before retrying a timed-out job.
- Jobs must be serialized by the caller. Concurrent operations, interruption
  recovery, and bootstrap failure after launch need
  [task #4](https://github.com/zainfathoni/palka/issues/4).
- A successful spawn checks that the bootstrap process launched, not that it
  completed its Amp connection. No live Amp connection has been tested.
- `ORB_SNAPSHOT_ID` is accepted but not used. Spawn uses the retained disk or
  base image, and pause does not save a project snapshot. A warning states
  this limit. Snapshot support is [task #5](https://github.com/zainfathoni/palka/issues/5).
- CPU, memory, process, concurrent-Orb, disk, and log budgets are not configured
  by this version. See [task #6](https://github.com/zainfathoni/palka/issues/6).
- No service, installer, provider registration, or macOS backend is included.

## Verification

Run:

```sh
python3 scripts/check.py
python3 -m unittest discover -s tests -v
git diff --check
```

Tests use the public dispatch interface and real Docker. The pinned Debian
fixture has a synthetic `amp` command, not the Amp agent. It deliberately
prints synthetic credentials to both output streams so the tests can detect
disclosure. It also has a stale image server default that a later spawn must
clear. Its credential observation file lives only in tmpfs.

The suite checks disk retention, default-user bootstrap, fresh credentials,
serial repeat operations, unrelated resources, ownership collisions, invalid
input, failed image creation, and an unreachable daemon. It removes its own
containers and image tag. It does not prune Docker's shared build cache or
remove unrelated resources. A failed or interrupted test can need manual
cleanup of its uniquely named disposable resources.
