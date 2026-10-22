# Docker lifecycle

This is an initial implementation, not a production-ready Orb provider.
It implements the lifecycle and recovery tasks
[#3](https://github.com/zainfathoni/palka/issues/3) and
[#4](https://github.com/zainfathoni/palka/issues/4), and project snapshots
in [#5](https://github.com/zainfathoni/palka/issues/5). Local limits are in
[#6](https://github.com/zainfathoni/palka/issues/6).
Use the [Linux pilot guide](linux-pilot.md) before any approved live test.

## Requirements

- A Linux x64 host with Python 3.11 or later and a local Docker CLI and daemon.
  Linux cgroup v2 must support CPU, memory, swap, and process limits.
  The CLI must use the local Unix socket, not a proxy, remote `DOCKER_HOST`,
  or remote context. The Engine error classification depends on this boundary.
- Permission to manage local Docker containers. This grants broad host access.
- A Linux amd64/glibc image that meets the current
  [Amp image requirements](https://ampcode.com/docs/orbs/self-hosted).
- Bash, coreutils, util-linux `setsid` and `flock`, and `amp` inside the image. Bootstrap
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
- A repeat `spawn` replaces only the prior bootstrap process group.
  It preserves the running container and unrelated Orb processes. The PID
  record includes a process start time to avoid selecting a reused PID.
- `pause` stops the container with a ten-second stop grace and keeps its disk.
- `delete` removes the owned container, its writable disk, and its anonymous
  volumes. Repeat pause or delete of an absent Orb succeeds.

The writable layer is retained on stop/start. `/tmp` is a tmpfs mount and is
cleared on stop, including the bootstrap PID record. Running processes and
RAM are not retained. No host mounts, runtime socket, privileged mode, or
provider credential are passed to the Orb.

## Concurrency and recovery

Palka locks each Orb before it reads or changes Docker state. Independent
Orbs have separate locks. Every dispatch process for the same Docker daemon
must use the same local state directory and user. Set `PALKA_STATE_DIR` to
an absolute path on a local filesystem, or use the default
`$XDG_STATE_HOME/palka` (`$HOME/.local/state/palka` when unset).
Keep this directory private. Do not share it over a network filesystem.

Lock files stay in this directory. Do not remove them while jobs can run:
removal can let two jobs lock different files for the same Orb. The kernel
releases a lock when its dispatch process exits. Docker names and ownership
labels find retained containers after a provider or host restart.

Before start or stop, Palka saves a recovery record with the immutable
container ID and operation. A successful CLI response resolves the record.
For start only, an explicit `Error response from daemon:` also resolves the
request, but the job still fails. A failed stop stays uncertain: a signal
already sent can still take effect after the error. The raw error is never
logged. An unrecognized error,
lost connection, timeout, or interrupted client leaves the outcome uncertain.
A changed container ID or damaged record requires inspection.

A pending start can be resolved when the locked inspect observes its running
state. Otherwise, pause and spawn refuse an uncertain transition. In
particular, Palka does not replay an uncertain stop and then resume: another
stop request is not a completion fence for the first request. This is a
deliberate loss of automatic recovery, not a claim that the daemon cancelled
work. An approved delete can still remove the owned immutable container ID;
it does not replay a failing start. A failed removal preserves the record.
Do not discard recovery records during an upgrade or service removal.

To recover an uncertain transition without deleting a disk, stop the provider
and exclude all direct dispatch jobs. Obtain separate approval for a host
reboot or another verified procedure that drains old Docker and runtime
requests. This can affect other containers and is not authorized by a job
retry. A daemon restart, inspect result, or time delay alone is not proof
that all runtime work has ended.
After the old daemon requests cannot run, inspect the exact recorded ID and
ownership labels. Archive only that reviewed pending record outside its
active `.pending` name, then retry dispatch with the same limits and state.
Preserve the disk, lock files, and reservation. Do not clear all state.

Bootstrap launch also has an in-container lock. The child saves its PID and
start time before it runs `amp`, then closes its copy of the lock. A Docker
exec that outlives an interrupted host client cannot launch a second
unrecorded bootstrap. Pause clears this temporary in-container state.

Each job has one 240-second deadline, including lock waits. Set
`PALKA_JOB_TIMEOUT_SECONDS` to a finite value greater than zero and at most
240 to use a shorter budget. Timeout or termination kills the Docker client
process group and returns a sanitized error. It cannot guarantee that the
Docker daemon cancelled work. Retry through dispatch to recover a pending
transition, or follow the quiescence procedure if the retry refuses it.
Do not run conflicting manual Docker commands while jobs can run.

## Project snapshots

Pause with `ORB_SNAPSHOT_ID` stops the Orb, then commits its writable layer
to a separate local image. The retained Orb container stays in place. A
later spawn of that same Orb uses its retained disk, not the snapshot. A new
Orb uses an independent writable layer on the saved image. Deleting one Orb
does not remove a shared snapshot.

Snapshot IDs are opaque strings. Palka uses the SHA-256 digest of their UTF-8
bytes as the tag in `palka-snapshot:<digest>`. The image must have both
`dev.palka.snapshot=true` and `dev.palka.snapshot-id=<digest>`. A tag collision
with an unowned image is an error, not a cache miss. A successful local image
list with no such tag is a miss and uses `ORB_IMAGE`; an inspect or daemon
failure is an error. No registry is used to store or retrieve these snapshots.

Writers and readers take the same snapshot lock after the Orb lock. A writer
first creates a complete immutable image, then publishes its tag. Readers
cannot see a partial commit. Successful writers for the same ID run in lock
order; the last successful publication wins. A failure before publication
keeps the prior tag. A completed but unpublished commit can leave an untagged
owned image. Snapshot work uses the same job deadline.

Only the writable container layer is supported. Images with `VOLUME`
declarations and containers with persistent volume or bind mounts are
rejected, rather than silently lose that data from a snapshot. A newly
created incompatible container can remain stopped for inspection or an
explicit delete job. The `/tmp` tmpfs is intentionally excluded. It holds
transient PID records and other temporary files. Snapshots do not contain
RAM or running processes. Store project
files outside `/tmp` and do not add persistent mounts manually.

Palka does not write spawn credentials to the writable layer or image
configuration. It rejects nonempty `ORB_TOKEN` or `AMP_ORB_PROVIDER_TOKEN`
values in container image configuration. This is not a general secret
scanner. Project files and tools can write their own secrets to disk; such
files would enter the snapshot. Use trusted project setup and inspect its
secret handling before snapshot reuse or sharing.

Snapshots have no automatic expiry or deletion. Replaced and unpublished
images can also consume disk. Count them in storage budgets. Amp snapshot
invalidation does not itself remove a local Docker image under this dispatch
contract. Cleanup is a separate operator action, never an uninstall or Orb
delete side effect. Stop the provider and exclude direct dispatch jobs
before an approved cleanup. List candidates with:

```sh
docker image ls --all --no-trunc --filter label=dev.palka.snapshot=true
```

Verify the exact image ID, ownership labels, tags, and current users locally.
Remove only the approved reference or immutable image ID with `docker image
rm`; do not use force or a general prune command. Keep images still used by
retained Orbs or desired snapshots. This guide does not authorize deletion.

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

## Local limits and storage admission

Set all these variables for spawn and snapshot-save jobs. They have no
production defaults. Palka validates them before a lifecycle mutation.
Pause without a snapshot and delete remain available without these limits,
so an invalid growth configuration cannot prevent shutdown or recovery.

| Variable | Required value |
| --- | --- |
| `PALKA_CPUS` | Positive finite CPU count, at least one nanocpu |
| `PALKA_MEMORY_BYTES` | Positive integer bytes, at least Docker's 6-MiB minimum |
| `PALKA_PIDS_LIMIT` | Positive integer process limit |
| `PALKA_MAX_RUNNING` | Positive integer running-Orb limit |
| `PALKA_MIN_FREE_BYTES` | Positive integer filesystem reserve |
| `PALKA_MAX_USED_BYTES` | Positive integer whole-filesystem usage ceiling |
| `PALKA_STORAGE_PATH` | Existing absolute directory on the Docker data filesystem |

Created containers get CPU, memory, and process limits. Memory plus swap is
set to the memory limit, so the Orb has no additional swap allowance.
The `/tmp` tmpfs size is half the memory limit, capped at 256 MiB. Tmpfs
memory is also charged to the container memory limit. Docker's `local` log
driver keeps two files with a nominal 5-MiB rotation size each, plus driver
metadata and buffers. These log limits do not configure the provider journal.

Resume requires the same CPU, memory, swap, process, and log configuration.
A mismatch fails without starting or deleting the retained disk. Restore
the approved settings; do not delete a disk as a configuration repair.
Changing settings affects new Orbs, not existing retained disks.

A short admission lock protects a slot reservation before create or start.
Image pulls, container start, bootstrap, and snapshot work do not hold this
global lock. Running Orbs and incomplete spawn reservations each count once.
An interrupted or failed spawn can keep a slot until the same Orb is retried,
paused, or deleted. This is a safe refusal, not automatic cleanup. Do not
remove reservation files while jobs can run. All jobs must use the same
state directory, user, daemon, and admission policy. Manual Docker changes
are outside this coordination boundary.

Storage accounting uses filesystem allocated and available bytes, not the
sum of image virtual sizes. It therefore includes stopped disks, shared
snapshot layers once, replaced images, build cache, logs, and unrelated files
on that filesystem. The budget is deliberately for the whole filesystem,
not a claim about Palka-only use. Reserve space excludes filesystem blocks
not available to the caller. The accounting path must be on Docker's data
filesystem. Images, snapshots, layers, and container logs must all be on
that filesystem. Check a separate containerd image-store path locally when
the engine uses one; split data filesystems are not supported by this budget.

Spawn checks storage before admission and before start. Snapshot save checks
before commit. A reached reserve or usage ceiling refuses new work without
deleting data. A refused snapshot can leave the original Orb stopped with
its disk intact. Pause and delete can still release resources.

**This is admission control, not a hard writable-layer quota.** No per-Orb
disk quota is claimed for `overlayfs` or `overlay2`. A running Orb, image
pull, commit, or another host process can cross a threshold after its check.
The checks do not reserve the unknown size of a future image or project.
Use a dedicated monitored filesystem for the controlled pilot. Review host
journal retention separately. Do not run an unattended or untrusted workload
under this disk policy. Palka never prunes retained data to regain capacity.

The proposed one-Orb, 2-CPU, 4-GiB pilot is not a default or a measured host
capacity. Choose its process, storage, and host reserve budgets before live
activation. Synthetic tests use smaller explicit limits; they do not measure
an Amp workload's needs.

## Current limits

- Recovery tests cover interrupted dispatch processes, not a physical host
  reboot or loss of the Docker storage filesystem. A host restart must use
  the same retained Docker storage and Palka state directory.
- A successful spawn checks that the bootstrap process launched, not that it
  completed its Amp connection. No live Amp connection has been tested.
- An opt-in [Linux service example and diagnostics](service.md) are included.
  No automatic installer, provider registration, or macOS backend is included.

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
repeat and concurrent operations, unrelated resources, ownership collisions,
invalid input, failed image creation, an unreachable daemon, lock deadlines,
and interruption after create, start, exec, stop, and removal. It also
interrupts a slow stop before it completes and checks that resume refuses
the uncertain transition while the disk stays intact. Concurrent spawn/pause
can finish running or stopped, but
must keep the same sentinel disk. Concurrent spawn/delete can finish running
on a new disk or absent; the deleted disk must not return. It removes its own
containers and image tag. It does not prune Docker's shared build cache or
remove unrelated resources. A failed or interrupted test can need manual
cleanup of its uniquely named disposable resources.
