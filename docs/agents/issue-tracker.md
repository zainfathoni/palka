# Issue tracker: GitHub

Issues and plans live in `zainfathoni/palka` on GitHub. Use the `gh` CLI.
Before an update, read the current issue body and comments with
`gh issue view NUMBER --repo zainfathoni/palka --comments`.

Create bounded tasks with an observable result, acceptance criteria, a
parent, and any real blockers. Apply `ready-for-agent` to specified coding
tasks. Use `ready-for-human` when operator access or a human decision is
required. Work only when all native blockers are complete.

## Native relationships

Get the numeric database ID with:

```sh
gh api repos/zainfathoni/palka/issues/NUMBER --jq .id
```

Add a child to a parent with:

```sh
gh api --method POST repos/zainfathoni/palka/issues/PARENT/sub_issues \
  -F sub_issue_id=CHILD_DATABASE_ID
```

Add a blocker to the blocked issue with:

```sh
gh api --method POST repos/zainfathoni/palka/issues/BLOCKED/dependencies/blocked_by \
  -F issue_id=BLOCKER_DATABASE_ID
```

The database ID is not the issue number or node ID. Read back `sub_issues`
and `dependencies/blocked_by` with GET requests after writes. Text links are
useful context but do not replace native relationships. Keep the graph free
of cycles. A parent defines scope, not execution order.

## Pull requests as a request surface

PRs as a request surface: no.

When a skill says to publish a plan or ticket, create a GitHub issue. When it
says to fetch a ticket, read the issue and comments. Preserve existing owner
edits and do not close a scope parent as a side effect of child work.
