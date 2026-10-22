# Palka

Palka is a machine-local runtime layer for self-hosted Amp Orbs.

## Language

**Provider**: The Amp process that receives lifecycle jobs from Amp and calls
a dispatch program. Its long-lived credential is separate from an Orb token.

**Dispatch program**: The program that handles one Amp lifecycle job with no
command-line arguments.

**Orb**: A tool execution environment connected to an Amp thread.
_Avoid_: Agent loop, scheduler.

**Retained Orb disk**: The writable disk kept for the same Orb across pause
and spawn operations. It does not include running processes or RAM.
_Avoid_: Project snapshot.

**Project snapshot**: A reusable copy of a prepared Orb disk, named by Amp,
that can seed a different Orb. It is not a memory checkpoint.
_Avoid_: Retained Orb disk, suspend image.

**Runtime backend**: The local container platform used by the dispatch
program, such as Docker or Apple Containers.
