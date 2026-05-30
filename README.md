# AsyncAPI Blast Radius

`asyncapi-blast-radius` is a Python CLI for analyzing how AsyncAPI contract changes affect downstream consumers.

Unlike generic diff tools, it focuses on the operational question teams usually ask next: "Who is likely to break, and what should we coordinate before rollout?"

## Why this is useful

Existing AsyncAPI compare tools help detect spec differences. This tool goes one step further by:

- flattening nested payload fields such as `customer.email` or `shipping.address.postalCode`
- mapping breaking changes to known consumers from a registry
- assigning a simple blast-radius risk level
- generating team-facing migration checklists
- emitting text, JSON, or Markdown for CI pipelines, pull requests, and release notes

That makes it useful for platform teams, event governance, release engineering, and schema review workflows.

## How the migration checklist is built

The migration checklist is deterministic and rule-based, not LLM-generated.

It always starts with baseline rollout steps, then adds targeted actions based on the detected contract changes:

- removed fields: recommends deprecation first, or temporary dual-field compatibility when there is a likely rename
- type changes: asks teams to validate deserialization, validation, and storage assumptions
- newly required fields: asks producers to guarantee population before enforcing the stricter contract
- rename hints: adds explicit rename validation and migration-path documentation steps
- impacted consumers: adds owner-specific follow-up steps with Slack, runbook, and repo references when available

This keeps the output predictable in CI while still being more actionable than a plain schema diff.

## Features

- AsyncAPI contract comparison for AsyncAPI 3-style documents
- nested field path analysis
- breaking change detection for removed fields, type changes, and new required fields
- consumer ownership mapping
- rename hints for likely field replacements
- rule-based migration checklists tailored to removals, type changes, and stricter required fields
- Slack notification support with a shortened migration checklist for fast triage
- CI-friendly `--fail-on-breaking` mode
- Markdown output for PR comments

## Installation

```bash
pip install -e .
```

Or:

```bash
pip install -r requirements.txt
```

## Quick start

Run against the bundled sample project:

```bash
asyncapi-blast-radius
```

Compatibility entrypoint:

```bash
python blast_radius.py
```

Generate JSON:

```bash
asyncapi-blast-radius --format json
```

Generate Markdown:

```bash
asyncapi-blast-radius --format markdown
```

Fail CI on breaking changes:

```bash
asyncapi-blast-radius --fail-on-breaking
```

Analyze a specific topic:

```bash
asyncapi-blast-radius --topic orders.created.v1
```

Write to a file:

```bash
asyncapi-blast-radius --format markdown --output blast-radius.md
```

## Example output

```text
Topic: orders.created.v1
Message: OrderCreated
Operation: send
Risk: HIGH

Breaking changes:
- Field `customer.email` was removed.
- Field `customer.loyaltyTier` changed type from `string` to `integer`.
- Field `shipping.address.postalCode` was removed.
- Field `total` changed type from `number` to `string`.
- Field `orderStatus` was added as a required field.

Impacted consumers:
- billing-service (Revenue Platform) uses customer.email, total
- fulfillment-service (Operations Platform) uses shipping.address.postalCode
- crm-sync (Growth Systems) uses customer.email, customer.loyaltyTier

Rename hints:
- customer.email -> customer.emailAddress
- shipping.address.postalCode -> shipping.address.zipCode

Migration checklist:
- Review producer rollout plan for `orders.created.v1`.
- Confirm whether consumers need dual-field compatibility during migration.
- Keep both `customer.email` and `customer.emailAddress` available during the transition window if backward compatibility is required.
- Check whether `shipping.address.postalCode` can be deprecated first before being removed from `orders.created.v1`.
- Validate all consumers of `customer.loyaltyTier` for deserialization, validation, and storage changes.
- Validate all consumers of `total` for deserialization, validation, and storage changes.
- Ensure producers always populate `orderStatus` before enforcing the new required-field contract.
- Validate the suspected rename from `customer.email` to `customer.emailAddress` and document the migration path.
- Validate the suspected rename from `shipping.address.postalCode` to `shipping.address.zipCode` and document the migration path.
- Notify Revenue Platform (billing-service) about fields: customer.email, total. Coordinate in #revenue-alerts. Review runbook: https://runbooks.example.com/billing-orders. Check implementation in github.com/example/billing-service.
- Notify Operations Platform (fulfillment-service) about fields: shipping.address.postalCode. Coordinate in #ops-alerts. Review runbook: https://runbooks.example.com/fulfillment-orders. Check implementation in github.com/example/fulfillment-service.
- Notify Growth Systems (crm-sync) about fields: customer.email, customer.loyaltyTier. Coordinate in #growth-integrations. Review runbook: https://runbooks.example.com/crm-sync-orders. Check implementation in github.com/example/crm-sync.
- Plan release sequencing so consumers can deploy before the stricter contract is enforced.
```

## Slack notifications

Copy `.env.example` to `.env` and set:

```bash
set SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

Then run:

```bash
asyncapi-blast-radius --notify-slack
```

Slack messages include:

- topic and risk summary
- breaking changes
- impacted consumers
- the first few migration checklist actions for fast triage

## Project structure

- `asyncapi_blast_radius/`: package source
- `examples/contracts/`: sample AsyncAPI documents
- `examples/consumers.json`: sample ownership registry
- `tests/`: basic regression tests
- `.asyncapi-tool`: AsyncAPI tools catalog metadata

## Registry format

Each consumer entry in the registry is a JSON object:

```json
{
  "name": "billing-service",
  "team": "Revenue Platform",
  "repo": "github.com/example/billing-service",
  "topics": ["orders.created.v1"],
  "fieldsUsed": ["total", "customer.email"],
  "slackChannel": "#revenue-alerts",
  "runbook": "https://runbooks.example.com/billing-orders"
}
```

`fieldsUsed` should reference flattened field paths that match the payload shape in the AsyncAPI document.

## Development

```bash
python -m pytest
```
