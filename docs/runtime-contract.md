# Runtime contract

Status: requirements for follow-up implementation, not implemented behavior.

Source: [Amp self-hosted Orbs](https://ampcode.com/docs/orbs/self-hosted),
checked on 2026-10-22. The current upstream contract takes precedence if it
changes. Review and record such changes before implementation.

## Ownership and transport

Amp owns scheduling, threads, project setup, and the agent loop. Amp's
`amp orb provider` process receives jobs and calls Palka once for each job.
Palka owns only the local runtime operations and their local state.

The provider uses outbound HTTPS. Orbs also use outbound HTTPS and WSS for
bootstrap, tools, and tunnels. Updates, image pulls, Git hosts, and project
dependencies need their own outbound access. Use Amp's current destination
list when you configure a firewall. No inbound endpoint is required.

## Dispatch input

The dispatch program receives no arguments. Input is in the environment.

| Variable | Meaning |
| --- | --- |
| `ORB_OPERATION` | `spawn`, `pause`, or `delete` |
| `ORB_ID` | Stable Orb identity; repeated jobs address the same Orb |
| `ORB_IMAGE` | Base image supplied by Amp |
| `ORB_SNAPSHOT_ID` | Optional project snapshot to restore or save |
| `ORB_TOKEN` | Short-lived spawn credential for this Orb's connection |
| `AMP_URL` | Spawn server override when the server is not ampcode.com |

Validate operation and identifiers before a runtime call. Treat input as
data, not shell syntax. Use an ownership marker and a local namespace so an
Orb identifier cannot select an unrelated host container or snapshot.

## Operations

| Job | Required outcome |
| --- | --- |
| `spawn` | Run the existing Orb if present. Otherwise, create it from the named snapshot if available, or from `ORB_IMAGE`. Start detached `amp orb bootstrap` as the image's default user. Pass the current `ORB_TOKEN` and `AMP_URL` when set. |
| `pause` | Stop the Orb and retain its disk. If `ORB_SNAPSHOT_ID` is set, also save a reusable copy under that ID. |
| `delete` | Remove the Orb and its retained disk. Do not remove unrelated resources or shared project snapshots. |

All three operations must be safe to repeat and finish within five minutes.
Bound image pulls, runtime commands, lock waits, and snapshot work within a
single job deadline. Report a nonzero result for incomplete work; do not hide
permission, storage, or runtime errors as a missing resource.

Serialize conflicting operations for the same Orb. Specify how snapshot
publication handles writers that use the same snapshot ID. Repeated spawn
must not create a second container or conflicting bootstrap processes.
Test recovery after interruption and after provider or host restart.

Each spawn receives current credentials. Do not retain credentials from an
earlier job or bake them into a snapshot. A missing `AMP_URL` on a later spawn
must not reuse an old override.

## Persistence and snapshots

Docker stop/start retains the writable disk, not running processes or RAM.
Apple Containers stop/start has the same distinction. Bootstrap starts again
on resume. A service or process inside the Orb must not be assumed to survive.

A retained disk belongs to one Orb. A project snapshot is a separate copy
that can seed new Orbs after repository setup. Amp can send pause with a
snapshot ID, then spawn the original Orb again. Both paths must work.

Publish complete snapshots only. Restore a snapshot into an independent
writable disk. A missing snapshot falls back to the base image; a corrupt or
inaccessible snapshot requires a clear error and must not look like a cache
miss. Define retention and explicit cleanup rules. Include snapshots and
container logs in disk accounting. Do not assume container commit copies
mounted volumes; test the actual storage design.

## Images and isolation

Amp currently requires Linux x64 with glibc. Alpine is not compatible.
Use the upstream image requirements for tools, paths, and the default `user`
account with a writable home. Bootstrap must use the image's default user,
not a host root override. Apple Silicon needs Rosetta for Linux amd64.

The provider needs runtime management rights. The Orb does not need a host
Docker socket, privileged mode, host namespaces, or arbitrary host mounts.
Docker access gives broad host control; a container is not a hostile-tenant
security boundary. Use a dedicated host or stronger isolation if the trust
model requires it. See [SECURITY.md](../SECURITY.md).

## Limits and diagnostics

Apply CPU, memory, process, and concurrent-Orb limits. Define disk and log
budgets with reserve space and a clear refusal mode. Measure enforcement on
the selected Docker storage driver; do not claim a disk quota that the driver
cannot enforce. Keep stopped Orbs and snapshots in capacity accounting.

A one-Orb, 2-CPU, 4-GiB pilot is only a proposed starting limit. It is not a
measured host capacity or a production default. Measure lifecycle time,
memory, disk growth, and behavior under pressure before raising limits.

Logs may contain job identity, operation, duration, and a sanitized error.
Never log tokens, environment dumps, repository contents, or snapshot data.
Provider credentials stay with the provider. Spawn credentials are passed
only to bootstrap. A diagnostic bundle must be safe to review before sharing.

## Service boundary

Plan an opt-in Linux service with explicit install, start, stop, and uninstall
steps. Installation must not register an Amp provider or start a service as a
side effect. Store secrets outside the repository with restricted access.
Uninstall must preserve Orb disks unless the operator explicitly requests
their removal. Plan a macOS service only after backend validation.
