import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from asyncapi_blast_radius.notifier import send_slack_notification


@dataclass
class SchemaField:
    path: str
    field_type: str
    required: bool


@dataclass
class BreakingChange:
    change_type: str
    field: str
    details: str
    severity: str


def load_document(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def load_registry(path: str | Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def resolve_message(
    channel_name: str,
    channel_body: dict[str, Any],
    contract: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    operations = contract.get("operations", {})

    if "publish" in channel_body or "subscribe" in channel_body:
        operation_name = "publish" if "publish" in channel_body else "subscribe"
        operation = channel_body.get(operation_name, {})
        return operation_name, operation.get("message", {})

    for _, operation in operations.items():
        channel_ref = operation.get("channel", {}).get("$ref", "")
        if channel_ref != f"#/channels/{channel_name}":
            continue
        operation_name = operation.get("action", "unknown")
        message_refs = operation.get("messages", [])
        if not message_refs:
            break

        message_ref = message_refs[0].get("$ref", "")
        if message_ref.startswith("#/channels/"):
            _, _, ref_channel, _, ref_message = message_ref.split("/", 4)
            message = (
                contract.get("channels", {})
                .get(ref_channel, {})
                .get("messages", {})
                .get(ref_message, {})
            )
            return operation_name, message

        if message_ref.startswith("#/components/messages/"):
            ref_message = message_ref.rsplit("/", 1)[-1]
            message = contract.get("components", {}).get("messages", {}).get(ref_message, {})
            return operation_name, message

        break

    channel_messages = channel_body.get("messages", {})
    if channel_messages:
        return "unknown", next(iter(channel_messages.values()))
    return "unknown", {}


def extract_topics(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    topics: dict[str, dict[str, Any]] = {}

    for channel_name, channel_body in contract.get("channels", {}).items():
        operation_name, message = resolve_message(channel_name, channel_body, contract)
        if "$ref" in message and message["$ref"].startswith("#/components/messages/"):
            ref_message = message["$ref"].rsplit("/", 1)[-1]
            message = contract.get("components", {}).get("messages", {}).get(ref_message, {})

        payload = message.get("payload", {})
        topics[channel_name] = {
            "topic": channel_body.get("address", channel_name),
            "operation": operation_name,
            "message": message.get("name", channel_name),
            "fields": flatten_schema(payload),
        }

    return topics


def flatten_schema(
    schema: dict[str, Any],
    prefix: str = "",
    inherited_required: bool = False,
) -> dict[str, SchemaField]:
    fields: dict[str, SchemaField] = {}
    schema_type = schema.get("type", "object")

    if prefix:
        fields[prefix] = SchemaField(
            path=prefix,
            field_type=schema_type,
            required=inherited_required,
        )

    if schema_type == "object":
        required_fields = set(schema.get("required", []))
        for property_name, property_schema in schema.get("properties", {}).items():
            path = f"{prefix}.{property_name}" if prefix else property_name
            fields.update(
                flatten_schema(
                    property_schema,
                    prefix=path,
                    inherited_required=property_name in required_fields,
                )
            )
    elif schema_type == "array":
        item_path = f"{prefix}[]" if prefix else "[]"
        fields.update(
            flatten_schema(
                schema.get("items", {}),
                prefix=item_path,
                inherited_required=inherited_required,
            )
        )

    return fields


def compare_topics(
    old_topic: dict[str, Any], new_topic: dict[str, Any]
) -> tuple[list[BreakingChange], list[dict[str, str]]]:
    old_fields = old_topic["fields"]
    new_fields = new_topic["fields"]

    removed_paths = set(old_fields) - set(new_fields)
    added_paths = set(new_fields) - set(old_fields)
    changes: list[BreakingChange] = []

    for path in sorted(removed_paths):
        changes.append(
            BreakingChange(
                change_type="field_removed",
                field=path,
                details=f"Field `{path}` was removed.",
                severity="high",
            )
        )

    for path in sorted(added_paths):
        if new_fields[path].required:
            changes.append(
                BreakingChange(
                    change_type="required_field_added",
                    field=path,
                    details=f"Field `{path}` was added as a required field.",
                    severity="medium",
                )
            )

    for path in sorted(set(old_fields) & set(new_fields)):
        old_field = old_fields[path]
        new_field = new_fields[path]
        if old_field.field_type != new_field.field_type:
            changes.append(
                BreakingChange(
                    change_type="type_changed",
                    field=path,
                    details=(
                        f"Field `{path}` changed type from "
                        f"`{old_field.field_type}` to `{new_field.field_type}`."
                    ),
                    severity="high",
                )
            )
        elif not old_field.required and new_field.required:
            changes.append(
                BreakingChange(
                    change_type="required_field_added",
                    field=path,
                    details=f"Field `{path}` became required.",
                    severity="medium",
                )
            )

    rename_hints: list[dict[str, str]] = []
    for removed_path in sorted(removed_paths):
        removed_leaf = removed_path.rsplit(".", 1)[-1]
        best_match = ""
        for added_path in sorted(added_paths):
            added_leaf = added_path.rsplit(".", 1)[-1]
            if removed_leaf.lower() in added_leaf.lower() or added_leaf.lower() in removed_leaf.lower():
                best_match = added_path
                break
        if best_match:
            rename_hints.append({"from": removed_path, "to": best_match})

    return changes, rename_hints


def find_impacts(
    topic_name: str,
    changes: list[BreakingChange],
    registry: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    changed_fields = {change.field for change in changes}
    impacts: list[dict[str, Any]] = []

    for consumer in registry:
        if topic_name not in consumer.get("topics", []):
            continue

        used_fields = set(consumer.get("fieldsUsed", []))
        impacted_fields = sorted(used_fields & changed_fields)
        if not impacted_fields:
            continue

        impacts.append(
            {
                "name": consumer["name"],
                "team": consumer.get("team", "unknown"),
                "repo": consumer.get("repo", ""),
                "slackChannel": consumer.get("slackChannel", ""),
                "runbook": consumer.get("runbook", ""),
                "impactedFields": impacted_fields,
            }
        )

    return impacts


def score_risk(changes: list[BreakingChange], impacts: list[dict[str, Any]]) -> str:
    if len(impacts) >= 3 or len(changes) >= 4:
        return "high"
    if impacts or len(changes) >= 2:
        return "medium"
    return "low"


def build_checklist(
    topic_name: str,
    changes: list[BreakingChange],
    impacts: list[dict[str, Any]],
    rename_hints: list[dict[str, str]],
) -> list[str]:
    removed_fields = sorted(
        change.field for change in changes if change.change_type == "field_removed"
    )
    type_changed_fields = sorted(
        change.field for change in changes if change.change_type == "type_changed"
    )
    required_added_fields = sorted(
        change.field
        for change in changes
        if change.change_type == "required_field_added"
    )
    rename_map = {hint["from"]: hint["to"] for hint in rename_hints}

    checklist = [
        f"Review producer rollout plan for `{topic_name}`.",
        "Confirm whether consumers need dual-field compatibility during migration.",
    ]

    for field in removed_fields:
        if field in rename_map:
            checklist.append(
                f"Keep both `{field}` and `{rename_map[field]}` available during the transition window if backward compatibility is required."
            )
        else:
            checklist.append(
                f"Check whether `{field}` can be deprecated first before being removed from `{topic_name}`."
            )

    for field in type_changed_fields:
        checklist.append(
            f"Validate all consumers of `{field}` for deserialization, validation, and storage changes."
        )

    for field in required_added_fields:
        checklist.append(
            f"Ensure producers always populate `{field}` before enforcing the new required-field contract."
        )

    for hint in rename_hints:
        checklist.append(
            f"Validate the suspected rename from `{hint['from']}` to `{hint['to']}` and document the migration path."
        )

    if impacts:
        for impact in impacts:
            owner_action = (
                f"Notify {impact['team']} ({impact['name']}) about fields: "
                f"{', '.join(impact['impactedFields'])}."
            )
            if impact.get("slackChannel"):
                owner_action += f" Coordinate in {impact['slackChannel']}."
            if impact.get("runbook"):
                owner_action += f" Review runbook: {impact['runbook']}."
            if impact.get("repo"):
                owner_action += f" Check implementation in {impact['repo']}."
            checklist.append(owner_action)

    if removed_fields or required_added_fields:
        checklist.append(
            "Plan release sequencing so consumers can deploy before the stricter contract is enforced."
        )

    return checklist


def analyze(
    old_path: str,
    new_path: str,
    registry_path: str,
    topic_filter: str | None = None,
) -> list[dict[str, Any]]:
    old_doc = load_document(old_path)
    new_doc = load_document(new_path)
    registry = load_registry(registry_path)

    old_topics = extract_topics(old_doc)
    new_topics = extract_topics(new_doc)
    reports: list[dict[str, Any]] = []

    for topic_name, old_topic in old_topics.items():
        if topic_filter and topic_name != topic_filter:
            continue

        new_topic = new_topics.get(topic_name)
        if not new_topic:
            continue

        changes, rename_hints = compare_topics(old_topic, new_topic)
        if not changes:
            continue

        impacts = find_impacts(topic_name, changes, registry)
        reports.append(
            {
                "topic": topic_name,
                "message": old_topic["message"],
                "operation": old_topic["operation"],
                "riskLevel": score_risk(changes, impacts),
                "breakingChanges": [asdict(change) for change in changes],
                "renameHints": rename_hints,
                "impactedConsumers": impacts,
                "migrationChecklist": build_checklist(topic_name, changes, impacts, rename_hints),
            }
        )

    return reports


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"Topic: {report['topic']}",
        f"Message: {report['message']}",
        f"Operation: {report['operation']}",
        f"Risk: {report['riskLevel'].upper()}",
        "",
        "Breaking changes:",
    ]

    for change in report["breakingChanges"]:
        lines.append(f"- {change['details']}")

    lines.append("")
    lines.append("Impacted consumers:")
    if report["impactedConsumers"]:
        for consumer in report["impactedConsumers"]:
            lines.append(
                f"- {consumer['name']} ({consumer['team']}) uses {', '.join(consumer['impactedFields'])}"
            )
    else:
        lines.append("- No impacted consumers found in the registry.")

    if report["renameHints"]:
        lines.append("")
        lines.append("Rename hints:")
        for hint in report["renameHints"]:
            lines.append(f"- {hint['from']} -> {hint['to']}")

    lines.append("")
    lines.append("Migration checklist:")
    for item in report["migrationChecklist"]:
        lines.append(f"- {item}")

    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"## AsyncAPI Blast Radius: `{report['topic']}`",
        f"- Risk: **{report['riskLevel'].upper()}**",
        f"- Message: `{report['message']}`",
        f"- Operation: `{report['operation']}`",
        "",
        "### Breaking changes",
    ]
    for change in report["breakingChanges"]:
        lines.append(f"- {change['details']}")

    lines.append("")
    lines.append("### Impacted consumers")
    if report["impactedConsumers"]:
        for consumer in report["impactedConsumers"]:
            lines.append(
                f"- `{consumer['name']}` owned by **{consumer['team']}** uses {', '.join(f'`{field}`' for field in consumer['impactedFields'])}"
            )
    else:
        lines.append("- No impacted consumers found in the registry.")

    lines.append("")
    lines.append("### Migration checklist")
    for item in report["migrationChecklist"]:
        lines.append(f"- {item}")

    return "\n".join(lines)


def render_reports(reports: list[dict[str, Any]], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(reports, indent=2)

    renderer = render_markdown if output_format == "markdown" else render_text
    return "\n\n---\n\n".join(renderer(report) for report in reports)


def build_slack_message(report: dict[str, Any]) -> str:
    changes = "\n".join(f"- {item['details']}" for item in report["breakingChanges"])
    impacts = "\n".join(
        f"- {consumer['name']} ({consumer['team']}): {', '.join(consumer['impactedFields'])}"
        for consumer in report["impactedConsumers"]
    ) or "- No impacted consumers found."
    checklist = "\n".join(f"- {item}" for item in report["migrationChecklist"][:5])
    return (
        f"*AsyncAPI Blast Radius*\n"
        f"*Topic:* `{report['topic']}`\n"
        f"*Risk:* {report['riskLevel'].upper()}\n"
        f"*Breaking changes*\n{changes}\n"
        f"*Impacted consumers*\n{impacts}\n"
        f"*Migration checklist*\n{checklist}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze the downstream blast radius of AsyncAPI contract changes."
    )
    parser.add_argument("--old", default="examples/contracts/orders-v1.yaml")
    parser.add_argument("--new", default="examples/contracts/orders-v2.yaml")
    parser.add_argument("--registry", default="examples/consumers.json")
    parser.add_argument("--topic", help="Analyze only one topic.")
    parser.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    parser.add_argument("--output", help="Write the rendered report to a file.")
    parser.add_argument("--notify-slack", action="store_true")
    parser.add_argument("--fail-on-breaking", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    reports = analyze(args.old, args.new, args.registry, args.topic)

    if not reports:
        print("No breaking changes detected.")
        return

    rendered = render_reports(reports, args.format)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)

    if args.notify_slack:
        for report in reports:
            send_slack_notification(build_slack_message(report))

    if args.fail_on_breaking:
        raise SystemExit(1)
