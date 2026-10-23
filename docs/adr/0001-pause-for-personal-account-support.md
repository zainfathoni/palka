# Pause Palka until personal-account Orb providers are supported

Accepted on 2026-10-23.

Amp's current documented self-hosted Orb registration requires a workspace;
no personal-account-only registration path was found. We will pause Palka
development and live validation because the intended use requires a personal
account without workspace membership. We will not create or join a workspace,
move credits or threads, or change account settings to complete this pilot.

## Reasons

Workspace membership changes credit handling, thread ownership, and default
visibility. These account changes are not acceptable for the intended use.
This decision does not assume that personal free credits become unusable:
the current Team workspace rules say that personal free credits are used
before the workspace pool. That rule does not remove the account and thread
ownership concerns.

## Consequences

- Preserve the MIT-licensed Linux implementation and its test evidence. Local
  synthetic tests passed; live Amp bootstrap and provider use remain untested.
- Leave the locally installed service inactive, without enabling it. Preserve
  private local configuration and state; do not commit or delete them.
- Do not register a provider, provision its token, or start live tests. Earlier
  pilot approval is no longer an instruction to proceed.
- Keep unfinished Linux and macOS work open but paused. Local runners remain
  an alternative workflow, not proof of Palka's live Orb integration.
- Reconsider only if Amp supports personal-account providers or the owner
  explicitly changes this decision. Check current upstream requirements and
  obtain new operator approval before any live test.

## Sources

Checked on 2026-10-23:

- [Amp self-hosted Orbs](https://ampcode.com/docs/orbs/self-hosted)
- [Amp workspace ownership and billing rules](https://ampcode.com/docs/collaborate/workspaces)
