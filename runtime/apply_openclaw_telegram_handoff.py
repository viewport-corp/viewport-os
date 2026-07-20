#!/usr/bin/env python3
"""Apply the scoped Telegram bot-to-bot policy to an OpenClaw config.

The script never prints configuration contents. Without --apply it performs a
read-only drift check. With --apply it creates a timestamped backup and replaces
the JSON atomically while preserving owner, group, and mode.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import stat
from pathlib import Path
from typing import Any


def _preserve_metadata(path: Path, original_stat: os.stat_result) -> None:
    """Restore the original mode and owner after a root-run atomic replace."""
    os.chown(path, original_stat.st_uid, original_stat.st_gid)
    os.chmod(path, stat.S_IMODE(original_stat.st_mode))


def _append_unique(values: Any, value: int) -> list[Any]:
    if not isinstance(values, list):
        raise ValueError("allowlist must be a JSON array")
    result = list(values)
    if value not in result and str(value) not in {str(item) for item in result}:
        result.append(value)
    return result


def _contains_id(values: Any, expected: int) -> bool:
    return isinstance(values, list) and str(expected) in {str(item) for item in values}


def _validate_scope(
    scope: dict[str, Any],
    boundary_user_id: int,
    allowed_boundaries: dict[str, set[str]],
    label: str,
) -> None:
    if not _contains_id(scope.get("allowFrom"), boundary_user_id):
        raise ValueError(f"{label}.allowFrom must be an owner-qualified JSON array")
    groups = scope.get("groups")
    if not isinstance(groups, dict):
        raise ValueError(f"{label}.groups must be a JSON object")
    for group_id, topic_ids in allowed_boundaries.items():
        group = groups.get(group_id)
        if not isinstance(group, dict):
            raise ValueError(f"{label}.groups[{group_id}] is missing")
        if not _contains_id(group.get("allowFrom"), boundary_user_id):
            raise ValueError(f"{label}.groups[{group_id}] is not owner-qualified")
        topics = group.get("topics")
        if not isinstance(topics, dict):
            raise ValueError(f"{label}.groups[{group_id}].topics must be a JSON object")
        for topic_id in topic_ids:
            topic = topics.get(topic_id)
            if not isinstance(topic, dict):
                raise ValueError(f"{label}.groups[{group_id}].topics[{topic_id}] is missing")
            if not _contains_id(topic.get("allowFrom"), boundary_user_id):
                raise ValueError(
                    f"{label}.groups[{group_id}].topics[{topic_id}] is not owner-qualified"
                )


def _apply_validated_scope(
    scope: dict[str, Any],
    hermes_bot_id: int,
    allowed_boundaries: dict[str, set[str]],
) -> None:
    scope["allowFrom"] = _append_unique(scope.get("allowFrom"), hermes_bot_id)
    groups = scope["groups"]
    for group_id, topic_ids in allowed_boundaries.items():
        group = groups[group_id]
        group["allowFrom"] = _append_unique(group.get("allowFrom"), hermes_bot_id)
        topics = group["topics"]
        for topic_id in topic_ids:
            topic = topics[topic_id]
            topic["allowFrom"] = _append_unique(topic.get("allowFrom"), hermes_bot_id)


def apply_policy(
    config: dict[str, Any],
    hermes_bot_id: int,
    boundary_user_id: int,
    account_name: str,
    allowed_boundaries: dict[str, set[str]],
) -> bool:
    """Mutate config with the least-privilege handoff policy; return drift."""
    before = json.dumps(config, sort_keys=True, separators=(",", ":"))
    channels = config.get("channels")
    if not isinstance(channels, dict):
        raise ValueError("channels must be a JSON object")
    telegram = channels.get("telegram")
    if not isinstance(telegram, dict):
        raise ValueError("channels.telegram must be a JSON object")
    if not allowed_boundaries:
        raise ValueError("at least one explicit group:topic boundary is required")
    accounts = telegram.get("accounts")
    if not isinstance(accounts, dict):
        raise ValueError("channels.telegram.accounts must be a JSON object")
    account = accounts.get(account_name)
    if not isinstance(account, dict):
        raise ValueError(f"selected Telegram account is missing: {account_name}")

    # Validate every requested target before mutating any part of the config.
    _validate_scope(telegram, boundary_user_id, allowed_boundaries, "channels.telegram")
    _validate_scope(account, boundary_user_id, allowed_boundaries, f"accounts.{account_name}")

    _apply_validated_scope(telegram, hermes_bot_id, allowed_boundaries)
    _apply_validated_scope(account, hermes_bot_id, allowed_boundaries)

    after = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return before != after


def _parse_boundaries(values: list[str]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for value in values:
        try:
            group_id, topic_id = value.rsplit(":", 1)
        except ValueError as exc:
            raise ValueError(f"invalid boundary {value!r}; expected GROUP_ID:TOPIC_ID") from exc
        if not group_id or not topic_id:
            raise ValueError(f"invalid boundary {value!r}; expected GROUP_ID:TOPIC_ID")
        result.setdefault(group_id, set()).add(topic_id)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--hermes-bot-id", required=True, type=int)
    parser.add_argument("--boundary-user-id", required=True, type=int)
    parser.add_argument("--account", default="default")
    parser.add_argument("--boundary", action="append", required=True, help="Exact GROUP_ID:TOPIC_ID pair")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    config = json.loads(args.config.read_text())
    changed = apply_policy(
        config,
        args.hermes_bot_id,
        args.boundary_user_id,
        args.account,
        _parse_boundaries(args.boundary),
    )
    if not changed:
        print("policy_status=current")
        return 0
    if not args.apply:
        print("policy_status=drift")
        return 2

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    original_stat = args.config.stat()
    backup = args.config.with_name(f"{args.config.name}.before-bot-handoff.{stamp}.bak")
    backup.write_bytes(args.config.read_bytes())
    _preserve_metadata(backup, original_stat)

    temp = args.config.with_name(f".{args.config.name}.bot-handoff.tmp")
    temp.write_text(json.dumps(config, indent=2) + "\n")
    _preserve_metadata(temp, original_stat)
    os.replace(temp, args.config)
    _preserve_metadata(args.config, original_stat)
    print(f"policy_status=applied backup={backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
