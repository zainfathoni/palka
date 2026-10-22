# Linux user service

This is an opt-in service example for a controlled Linux test. No live Amp
test has been run. It is not a production capacity claim. The proposed
1-Orb, 2-CPU, 4-GiB pilot is unmeasured. Complete and test the runtime limits,
snapshots, and diagnostics on the selected host before a live test. See the
[runtime contract](runtime-contract.md) and [Docker guide](docker.md).

The current [upstream contract](https://ampcode.com/docs/orbs/self-hosted)
was checked on 2026-10-22. It uses `AMP_ORB_PROVIDER_TOKEN` and
`amp orb provider /absolute/dispatch/path`. The service uses `%h` (the user
manager's absolute home path) for both the Amp executable and dispatch path.
It does not depend on a shell or a login PATH to find Amp.

## Prepare and install (no activation)

Use a Linux x64 host with Python 3.11 or later, a user systemd manager,
and a working Docker CLI and daemon. Use one dedicated account for all
dispatch jobs against that daemon. Docker access grants broad host control;
this service is not a hostile-tenant security boundary. Do not run it as root.

Run these commands from the approved, stable Palka checkout. Do not move or
remove that checkout while the service uses it. These commands install only
a unit and a path reference. They do not register, enable, or start a provider.
Do not overwrite an existing path reference without inspection.

```sh
test -x "$HOME/.local/bin/amp"
test -x "$PWD/dispatch.py"
install -d -m 700 "$HOME/.local/lib" "$HOME/.config/palka"
install -d "$HOME/.config/systemd/user"
ln -s "$PWD" "$HOME/.local/lib/palka"
install -m 600 packaging/palka-provider.service \
  "$HOME/.config/systemd/user/palka-provider.service"
systemd-analyze --user verify "$HOME/.config/systemd/user/palka-provider.service"
systemctl --user daemon-reload
```

If Amp is elsewhere, edit `ExecStart` in the installed unit to use its absolute
executable path. If the dispatch path changes, also change `WorkingDirectory`.
Keep both executable and dispatch paths absolute after specifier expansion.
The fixed PATH supports the Python shebang and Docker CLI; adjust it for a
non-standard tool installation. Verify the edited unit before use.

Credential provisioning is a separate operator action. Keep credentials
outside the repository. With `umask 077`, create
`$HOME/.config/palka/provider.env` as a regular file owned by this account,
with mode `0600`. Keep its parent directory at mode `0700`. Do not use a
shared file or put a token in command arguments, shell history, or a unit.
The file is required; a missing file causes a visible service failure.
Use systemd EnvironmentFile syntax (`NAME=value`), not shell `export`, command
substitution, or variable expansion. Use absolute paths, not `$HOME` or `~`.

Set each of these variables in that file before start:

- `AMP_ORB_PROVIDER_TOKEN`: an operator-provisioned provider credential.
- `PALKA_CPUS`: the operator-selected per-Orb CPU limit.
- `PALKA_MEMORY_BYTES`: the per-Orb memory limit in bytes.
- `PALKA_PIDS_LIMIT`: the per-Orb process limit.
- `PALKA_MAX_RUNNING`: the concurrent running-Orb limit.
- `PALKA_MIN_FREE_BYTES`: the storage reserve in bytes.
- `PALKA_MAX_USED_BYTES`: the storage usage budget in bytes.
- `PALKA_STORAGE_PATH`: the absolute path used for storage accounting.

There are no production defaults for these required limits. Select them
for the approved host and verify enforcement before activation. An optional
`PALKA_STATE_DIR` must be an absolute local path, private to this account.
If unset, the runtime uses `$XDG_STATE_HOME/palka`, or
`$HOME/.local/state/palka` when `XDG_STATE_HOME` is unset. Use the same state
directory across restarts and all dispatch entry points. Optionally set
`PALKA_JOB_TIMEOUT_SECONDS`; its default is 240 seconds and its maximum is
240 seconds. Do not remove lock or recovery files.

The provider credential is available to the provider and its host dispatch
children. It must never enter an Orb, image, or snapshot. Dispatch must pass
only the job's short-lived spawn credential to bootstrap. File permissions
do not protect secrets from this account, a host administrator, or a user
with Docker access. Do not dump the process environment.

## Explicit operator actions

The commands below are instructions, not evidence that activation was tested.
Start requires separate operator approval, provisioned credentials, completed
runtime work, and a reviewed host budget. Registration in Amp is a separate
operator action; this package does not register anything. Do not enable the
unit or configure login lingering as part of installation. Without lingering,
the user manager can stop when the account logs out.

```sh
# Start only after approval.
systemctl --user start palka-provider.service
# Inspect local state and recent failures.
systemctl --user status palka-provider.service
journalctl --user -u palka-provider.service -n 100 --no-pager
# Stop receiving jobs; this does not pause or delete existing Orbs.
systemctl --user stop palka-provider.service
# Stop and start the provider; retained data stays in place.
systemctl --user restart palka-provider.service
```

On failure, systemd waits 30 seconds before restart. It permits three starts
in 300 seconds, then leaves the service failed. A normal exit is not restarted.
Use status and the journal to find repeated failures. Correct the cause before
an explicit `systemctl --user reset-failed palka-provider.service` and start.
There is no failure handler that clears failures or deletes runtime data.

Stop sends SIGTERM to the service control group, including active dispatch
clients. After 270 seconds, systemd sends SIGKILL to remaining processes.
This bounds service stop, not Docker daemon work. A job can be interrupted.
Retain recovery records and retry through dispatch after restart. An uncertain
transition can require the [operator quiescence procedure](docker.md#concurrency-and-recovery)
instead of automatic recovery. A stopped inspect result is not a completion
fence for an old request. Do not
assume stopping the provider stops Docker containers. A host restart also
requires the same Docker storage and Palka state. Orb stop/start retains
disk only, not RAM or running processes.

## Diagnostics and network

Run `python3 scripts/diagnose.py` from the checkout with the same
operator-selected runtime configuration. It does not require a provider
token. It reads state and Docker metadata; it does not start containers,
create state, or activate a provider. It returns selected JSON on stdout
and a nonzero exit status for incomplete diagnostics. Do not source an
EnvironmentFile as a shell script. Set the non-secret limit variables through
your approved environment tooling.

The report includes the source commit, Python and Docker versions, runtime
reachability, configured numeric limits, whole-filesystem storage use, and
aggregate resource counts. The commit identifies the base revision, not any
uncommitted edits. It omits paths, IDs, image names, endpoints, and credentials.
Counts are a best-effort view while other jobs run, not an atomic inventory.

Dispatch saves one atomic last-result record per Orb in the private state
directory. It contains only operation, success, and elapsed seconds, including
lock wait when the job acquired its lock. Diagnostics group these last results
by operation, with count, failure count, and maximum duration. This is not an
event history. Validation failures before lock acquisition, SIGKILL, and host
power loss can leave no new result. These records are not recovery records;
they do not control the lifecycle. Do not remove state while jobs can run.

Review all diagnostic output and journals locally before sharing. Never share
tokens, environment dumps, repository content, snapshot data, or private
topology. Verify stdout and stderr redaction with synthetic token markers,
including failure paths. Unit verification does not prove provider log
redaction, dispatch redaction, or a successful Amp connection.

The provider needs outbound HTTPS. Orbs need outbound HTTPS and WSS for
bootstrap, tools, and tunnels. Allow image pulls, updates, Git hosts, and
project dependencies as required. Use the current upstream destination list
for firewall rules. No public endpoint, inbound port, or DNS change is needed.

## Uninstall (preserve all runtime data)

After approval to stop the provider, run:

```sh
systemctl --user stop palka-provider.service
systemctl --user disable palka-provider.service
rm -- "$HOME/.config/systemd/user/palka-provider.service"
systemctl --user daemon-reload
```

Disable removes a prior operator enablement if one exists; this package has
no Install section and does not enable itself. Uninstall leaves the checkout,
path reference, credential file, Palka state directory, Docker containers,
retained Orb disks, volumes, and project snapshots intact. Remove credentials
only as a separate scoped operator action. Do not use Docker prune or remove
state files as an uninstall step. Any retained-data deletion needs separate
explicit approval and a named resource scope.
