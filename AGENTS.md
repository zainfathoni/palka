# Palka agent instructions

Palka is a separate machine-local runtime project. Amp owns scheduling,
threads, and the agent loop. This foundation has no implemented runtime.

Before runtime changes, read [the contract](docs/runtime-contract.md) and
check its current upstream source. Keep retained Orb disks distinct from
reusable project snapshots. Neither Docker nor Apple stop/start retains RAM.

Use synthetic data for tests and public evidence. Keep credentials, private
host details, and runtime state out of the repository. Provider activation,
DNS changes, another machine, and retained-data deletion require explicit
operator approval. Code or issue work is not that approval.

Run `python3 scripts/check.py` and `git diff --check` for foundation changes.
For runtime work, run the affected public-contract tests and record limits.
Write English prose using ASD-STE100 Simplified Technical English.

## Agent skills

### Issue tracker

Use GitHub Issues and native parent-child and blocked-by relationships.
Read [the tracker rules](docs/agents/issue-tracker.md) before issue planning
or issue updates.

### Triage labels

Use the default five roles. Read [the label map](docs/agents/triage-labels.md)
before triage. A ready label does not mean all blockers are complete.

### Domain docs

This is one domain context. Read [the domain rules](docs/agents/domain.md)
before design or terminology changes.
