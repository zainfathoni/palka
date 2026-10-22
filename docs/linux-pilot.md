# Controlled Linux test

Status: local synthetic Docker validation is available. Live Amp connection,
provider registration, and service activation are **not run**. This guide
does not authorize those actions. The Linux delivery parent remains open
until its remaining checks have evidence.

## Tested scope

The local test host uses Linux x86_64, kernel 7.2.8, Python 3.14.8, Docker
client and server 29.9.1, the `overlayfs` driver, and cgroup v2. These are
tested versions, not a claim about all Linux distributions or storage drivers.
Python 3.11 is the source minimum; a local run on that version is not recorded.

The fixture uses Debian bookworm-slim, pinned in
[the Dockerfile](../tests/fixtures/Dockerfile), with a synthetic `amp` command.
It is not Amp's default image. It checks the default image user, not a root
override. No real token, project repository, or workload enters these tests.
The [current Amp contract](https://ampcode.com/docs/orbs/self-hosted) was
checked on 2026-10-22.

Run the tests from the checkout:

```sh
python3 scripts/check.py
python3 -m unittest discover -s tests -v
git diff --check
```

Tests use uniquely named disposable containers and images. They remove their
own resources, not unrelated resources or the shared build cache. Run them
on the selected host before activation. A failed test can leave its named
disposable resources for inspection; do not use a general prune command.

The suite covers retained disks, repeat jobs, snapshot independence and
publication, competing jobs, interrupted dispatch clients, process restart
recovery, storage refusal, resource pressure, daemon failure, default-user
bootstrap, fresh credentials, and redacted output. Running processes and
temporary files in `/tmp` do not survive stop/start; the disk sentinel outside
`/tmp` does. There is no
RAM checkpoint. Process-restart tests do not prove physical host-reboot or
live provider-process recovery. Service verification checks the unit without
starting it. See the [Docker limits](docker.md#local-limits-and-storage-admission).

Uncertain Docker transitions can stop automatic recovery. Dispatch refuses
pause or resume rather than replay a request whose daemon outcome is unknown.
A confirmed Engine start failure does not block a later pause or approved
delete. Any failed or unknown stop can need an approved host reboot or another verified drain
before safe resume; a provider restart alone is not enough. See the
[quiescence procedure](docker.md#concurrency-and-recovery). This limits the
initial pilot to monitored operation, not unattended availability.

## Synthetic measurements

One cached-fixture run on 2026-10-22 used 0.5 CPU, 128 MiB memory, 64 processes,
and a running-Orb ceiling of eight. The run used at most two test Orbs.
The ceiling is a test configuration, not a measured capacity.

| Operation | Elapsed seconds |
| --- | ---: |
| New writable disk and bootstrap launch | 1.118 |
| Repeat spawn | 0.402 |
| Pause and snapshot | 1.681 |
| Retained-disk resume | 0.776 |
| New disk from snapshot | 1.213 |
| Delete snapshot-derived Orb | 0.549 |
| Delete original Orb | 0.566 |

These are single samples, not percentiles or a performance guarantee. The
image layers were already cached. They measure synthetic bootstrap launch,
not a successful Amp connection or project setup. Idle fixture usage was
1.918 MiB, three processes, and 0.00% CPU in one Docker stats sample. This is
not the resource use of an Amp workload.

A 2-MiB payload increased Docker's reported writable layer from zero to
2,097,152 bytes. A snapshot-derived Orb read that payload. Both container
names were absent after delete, while the shared snapshot remained until
scoped fixture cleanup. These observations do not prove immediate filesystem
space reclamation. Build cache and shared layers can remain.

Pressure tests verify CPU throttling, a memory-limit OOM kill, PID refusal,
and disk retention after restart. Storage tests cross both reserve and usage
thresholds and verify safe refusal. They do not fill the host filesystem.
There is no hard writable-layer quota. Active writes and pulls can exceed an
admission threshold; monitor the host and stop the pilot before its reserve
is lost. Provider journal retention is a separate host setting.

A separate stalled-CLI probe returned a sanitized failure in 240.128 seconds
under the default 240-second job budget. It made no daemon call. This tests
the client deadline, not cancellation of daemon work or a real cold pull.

## Default Amp image checks

On 2026-10-22, a local pull used this immutable Linux amd64 image:

```text
us-central1-docker.pkg.dev/amp-prod-0/amp-orb/amp-orb@sha256:e8d0d9a363a0039216eb315a1b420801ec4370bd434aa03e954f985d41b007f1
```

Its 31 layers total about 1.63 GiB compressed. The first pull of this local
reference did not finish within 240 seconds. The test stopped the CLI at
240.170 seconds; this does not prove that daemon work stopped. No shared
cache was removed. A separate preparation pull used the partial cache and
finished in another 432.624 seconds, outside any Palka job. The sampled free
space stayed above the test's 20-GiB reserve. These samples do not identify
the cause of the slow pull or establish a cold-start time.

An offline container from the unmodified image passed checks for Linux amd64,
the default `user` account and group, a writable home, passwordless sudo, CA
certificates, and the required command paths. It reported glibc 2.36, Git
2.55.0, Node.js 26.12.1, tmux 3.3a, and Amp
`0.0.1792680509-g8973a3`. Image configuration had no VOLUME declarations or
nonempty `ORB_TOKEN`, `AMP_URL`, or `AMP_ORB_PROVIDER_TOKEN` variables. This
was not a scan of all files for secrets. Network access was disabled for
this check. Real `amp --version` ran; real bootstrap did not run.

Two additional public-dispatch tests passed on a disposable derivative of
that image. Only the Amp executable was replaced with the synthetic fixture;
the image's default user was preserved. They checked retained-disk resume,
fresh synthetic credentials, snapshot restore into an independent disk,
redacted output, and scoped deletion. Their combined test time was 101.402
seconds, including the fixture build. This is not a lifecycle benchmark or
a real Amp connection. The derivative, test containers, and test snapshots
were removed; the public base image remains cached.

The complete small-fixture and service suite also passed: 33 tests in
174.813 seconds. It includes refusal of damaged recovery records without a
traceback, private paths, or disk changes. The two suites ran during the
same interval; do not compare their elapsed times as performance samples.

Use a pre-pulled, approved image for the first live pilot. Do not claim cold
default-image spawn support from these results. Image preparation needs its
own time and storage budget; it must not extend a dispatch job's deadline.
Before approved activation, set the provider's `ORB_IMAGE` override to the
same immutable reference. A recorded digest alone does not pin later jobs.

## Before a live test

- [ ] Approve the exact local revision for use. Publication is a separate action.
- [ ] Select one Linux x64 account, local Docker daemon, private state directory,
  and stable checkout path. Preserve that state and Docker storage on restart.
- [ ] Approve a test budget. One Orb, 2 CPUs, and 4 GiB memory are only a
  proposed starting profile, not a measured host guarantee. Select the process
  limit, whole-filesystem usage ceiling, and free-space reserve explicitly.
  Account for the host, stopped disks, snapshots, logs, and other workloads.
- [ ] Check that Docker data, its image store, layers, and logs use the accounted
  filesystem. Split containerd/image-store filesystems are not supported.
- [ ] Inspect the current upstream default image and record its immutable digest.
  Test a real cold pull and launch against the job deadline, or pre-pull the
  approved image and record that the pilot uses cached images. Do not delete
  shared cache to manufacture a cold pull. The recorded first pull exceeded
  the budget; cold default-image spawn is **not verified**.
- [ ] Verify the chosen image has the required glibc, tools, default user, writable
  home, and no unsupported VOLUME declarations or baked credentials. Review
  project setup for secrets written to snapshot content.
- [ ] Run sanitized diagnostics with the approved non-secret limits. Review
  storage and counts locally. An `ok` report means collection succeeded, not
  that capacity is sufficient or that the live provider works.
- [ ] Review outbound HTTPS/WSS rules and journal retention locally. No inbound
  endpoint, public port, or DNS change is required.

## Live Amp checklist — separate approval required

The following results are all **not run**. Use only synthetic project content.
Keep credentials outside the repository. Do not paste them into a thread,
command line, shell history, or evidence file. Follow the
[service guide](service.md) to separate installation from activation.

1. Approve registration of one named temporary provider in the intended Amp
   workspace and provision its credential through the restricted file.
   Do not change the workspace's default provider as a test side effect.
2. Approve service start. Record the exact source revision, image digest,
   approved limits, storage driver, and test time without private paths.
3. Create one thread on that provider. Confirm real bootstrap connects,
   repository cloning and project setup finish, and tool execution works.
   Inspect provider and setup output locally for credentials before sharing.
4. Write a synthetic disk sentinel and start a disposable process in the Orb.
   Let the thread go idle. Confirm pause stops the container and retains its
   disk. Send a new message; confirm the same disk, new bootstrap credentials,
   and absence of the old process. Record actual timing and resource use.
5. Let Amp save a prepared-project snapshot. Start a second test thread only
   under a separately approved two-Orb budget, or after the first is paused.
   Confirm prepared-project reuse and independent writable content. Confirm
   the original thread still resumes its own disk.
6. Under approval to interrupt the provider, stop and start it while preserving
   state. Check recovery, repeat jobs, and visible failures. A physical host
   reboot requires its own host-change approval and is **not run**.
   Stop the pilot if dispatch reports an uncertain transition. Do not clear
   records, assume the Orb is stopped, or keep retrying as a repair.
7. Obtain approval to delete only the named disposable test threads/Orbs.
   Confirm their containers disappear and unrelated resources and shared
   snapshots remain. Local snapshot cleanup is a separate explicit scope.
8. Stop the temporary provider. Record each result and every failure. Retain
   the MIT license with any release. Do not call the pilot production-ready
   from this test or increase limits without measurements.

## Stop and rollback

An emergency provider stop prevents new jobs but does not stop existing Orb
containers. Use the [service stop steps](service.md#explicit-operator-actions)
under the pilot's approved stop scope. To release active compute, explicitly
approve pause for the named Orbs without a snapshot; do not delete their disks.
Keep state, locks, recovery records, containers, snapshots, and credentials
until their separate removal scope is approved.

Restore the previous reviewed checkout path only after the provider and direct
dispatch jobs have stopped. Keep the same approved limits, state directory,
and Docker storage. Do not downgrade across an incompatible state format
without review. Do not clear records or run Docker prune as rollback.

Retain sanitized test outcomes, not raw environments, image exports, project
content, credentials, private host paths, or topology. Unregistering a provider,
revoking its credential, or removing retained data each needs explicit scope.
