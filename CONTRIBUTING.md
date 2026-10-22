# Contributing

Start with an issue from the [delivery plan](docs/roadmap.md). Read its parent
and native blocked-by links. Work only on a task whose blockers are complete.
Agree on changes to scope before adding another backend or service.

Read [the domain glossary](CONTEXT.md) and
[the runtime contract](docs/runtime-contract.md) before runtime work.

## Local checks

Use Python 3.11 or later. No third-party package is needed for foundation
checks. Run:

```sh
python3 scripts/check.py
git diff --check
```

The check validates local Markdown file links and text hygiene. It does not
check external URLs, full Markdown syntax, secrets, or runtime behavior.
CI runs this check and the Docker integration tests on pushes and pull
requests. For runtime work, also run:

```sh
python3 -m unittest discover -s tests -v
```

These tests need Linux x64 and Docker access. They build a synthetic image,
create disposable containers, and remove their own test resources. They do
not register a provider or connect to Amp. Docker can pull the pinned public
base image. Do not use private runtime data for these tests.

## Changes and tests

Keep each pull request bounded. Include the observable behavior, the linked
issue, tests run, and any known limits. Runtime changes need evidence for
normal operation, repeat jobs, and relevant failure cases. Use synthetic
data. Keep tests that cover the public contract rather than private calls.

Do not register a provider, start a host service, change DNS, or change
another machine as part of a code change. Request explicit operator approval
for these actions. Installing development tools is separate from installing
or activating a provider service.

Write short, clear English using ASD-STE100 Simplified Technical English.
Keep code and examples free of private host data and credentials.

## License

Contributions use the repository's [MIT license](LICENSE).
