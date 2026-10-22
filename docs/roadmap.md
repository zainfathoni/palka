# Delivery plan

The [GitHub issue graph](https://github.com/zainfathoni/palka/issues) is the
source of truth for task state. Parent-child links define scope. Native
blocked-by links define execution order. A parent does not replace a blocker.

Start at the [delivery parent, #1](https://github.com/zainfathoni/palka/issues/1).
The phase parents are [Linux, #2](https://github.com/zainfathoni/palka/issues/2)
and [macOS, #9](https://github.com/zainfathoni/palka/issues/9).
The first coding task is [Docker lifecycle, #3](https://github.com/zainfathoni/palka/issues/3).
The separate [macOS feasibility task, #10](https://github.com/zainfathoni/palka/issues/10)
requires operator approval and test hardware.

## Linux first

1. Deliver one Docker Orb from spawn through pause, resume, and delete, with
   disk evidence and default-user bootstrap checks.
2. Make that path safe under duplicate jobs, concurrent jobs, failures, and
   restart. Enforce the five-minute operation deadline.
3. Add reusable project snapshots and prove that they are independent of
   the original Orb disk.
4. Add resource limits and storage accounting with pressure tests.
5. Add opt-in service packaging and redacted diagnostics.
6. Validate the integrated runtime. Prepare a controlled pilot procedure;
   live provider registration and activation need separate owner approval.

Steps 3, 4, and 5 can proceed in parallel after step 2. Step 6 needs all three.
Each task must deliver a complete, testable behavior, not only an internal API.

## Apple Silicon next

First, test Apple Containers and Rosetta on a separately authorized Apple
Silicon host. This feasibility task can proceed independently of Linux work.
Then implement the same lifecycle and snapshot contract after the Linux
validation gate. Finally, run platform parity checks and add an opt-in macOS
service procedure. Do not claim that macOS host support means native arm64
Orb support: the current guest requirement remains Linux amd64 with glibc.

## Outside the first delivery

E2B Embed can be evaluated later. A custom scheduler, fleet-wide control
plane, public endpoint, and dashboard are outside the current scope.

## Approval gates

- The owner approves publishing local commits if not already authorized.
- The operator approves live provider registration and service activation.
- Access to another machine requires separate approval.
- Removal of retained disks or snapshots requires explicit operator scope.

Use synthetic data for local integration tests. Public evidence must contain
no credentials, private topology, runtime data, or private repository content.
