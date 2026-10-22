# Palka

Self-hosted Orb infrastructure for Amp.

Palka means a ship's cargo hold. It is a separate project, not an Amux
extension or dependency.

## Status

This repository contains an initial Docker lifecycle dispatch program and
its tests, with concurrent-job locking and interrupted-job recovery. Project
snapshots, resource limits, storage admission checks, sanitized diagnostics,
and an opt-in Linux service example are implemented. Live Amp validation is
not run. There is no automatic installer or active Orb provider.
Use the [controlled Linux test guide](docs/linux-pilot.md) for the next steps
and the required operator approvals. This is not production-validated.

The first runtime will use Docker on Linux. Apple Containers on Apple Silicon
macOS is next. E2B Embed is a possible later option, not the initial stack.

## Boundary

Amp owns scheduling, threads, and the agent loop. Palka will provide a small,
machine-local layer for lifecycle dispatch, persistent disks, project
snapshots, resource limits, and diagnostics. It will not provide a custom
scheduler, a fleet control plane, or a dashboard.

The provider and Orbs connect out to Amp. They need no public endpoint or DNS
change. Self-hosting keeps Orb disks on your infrastructure. It does not keep
tool output or model inference off Amp. See the [security guidance](SECURITY.md).

## Read and contribute

- [Runtime contract and design constraints](docs/runtime-contract.md)
- [Docker lifecycle use and limits](docs/docker.md)
- [Linux service and diagnostics](docs/service.md)
- [Controlled Linux test](docs/linux-pilot.md)
- [Delivery plan](docs/roadmap.md)
- [Domain glossary](CONTEXT.md)
- [Contribution guide](CONTRIBUTING.md)
- [Agent instructions](AGENTS.md)
- [GitHub issues](https://github.com/zainfathoni/palka/issues)

Run the foundation checks with Python 3.11 or later:

```sh
python3 scripts/check.py
```

These checks do not start containers or connect a provider.

Run the Docker integration tests on Linux x64 with a working Docker CLI and
daemon:

```sh
python3 -m unittest discover -s tests -v
```

The tests build a small synthetic image and use disposable containers. They
do not connect to Amp. See [the test requirements](docs/docker.md#verification).

## License

Palka uses the [MIT license](LICENSE).

## Upstream requirements

The plan was checked on 2026-10-22 against Amp's
[self-hosted Orb contract](https://ampcode.com/docs/orbs/self-hosted) and
[launch announcement](https://ampcode.com/news/self-hosted-orbs).
Check the current contract before runtime changes.
