# Security

## Report a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/zainfathoni/palka/security/advisories/new)
if it is enabled. If that page is not available, use the contact route on the
owner's [GitHub profile](https://github.com/zainfathoni). Ask for a private
channel without including exploit details. Do not put secrets or private
runtime data in a public issue.

There is no runtime release yet. Security fixes will target the current main
branch until a release support policy is published.

## Trust boundaries

- Amp controls the agent loop and model inference. Tool results, file reads,
  diffs, screenshots, and attachments can leave the host and be stored by Amp.
- The provider credential authorizes provider jobs. Keep it separate from
  the short-lived `ORB_TOKEN` that connects one Orb to its thread.
- The provider can control the container runtime. Docker access can provide
  host-level control. Use a host trust boundary that fits the workload.
- Keep the host runtime socket, host credentials, private mounts, and
  privileged execution out of Orbs.
- Treat retained disks and project snapshots as sensitive. They can contain
  source code, build output, and project credentials. A reusable snapshot
  must not include provider or bootstrap credentials.
- Only outbound connections are required. A public domain is not required.

## Safe evidence

Use synthetic repositories and disposable data for tests. Review logs and
diagnostic bundles before sharing them. Never publish environment dumps,
tokens, host inventories, private network addresses, or runtime state.
Ignore rules and automated checks are not a complete secret scanner.
