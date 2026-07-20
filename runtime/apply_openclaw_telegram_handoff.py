#!/usr/bin/env python3
"""Apply the scoped Telegram bot-to-bot policy to an OpenClaw config.

The script never prints configuration contents. Without --apply it performs a
read-only drift check. With --apply it creates a timestamped backup and replaces
the JSON atomically while preserving file permissions.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import stat
from pathlib import Path
from typing import Any


LOOP_POLICY = {
    "enabled": True,
    "maxEventsPerWindow": 8,
    "windowSeconds": 60,
    "cooldownSeconds": 300,
}


def _preserve_metadata(path: Path, original_stat: os.stat_result) -> None:
    """Restore the original mode and owner after a root-run atomic replace."""
    os.chmod(path, stat.S_IMODE(original_stat.st_mode))
    os.chown(path, original_stat.st_uid, original_stat.st_gid)


def _append_unique(values: Any, value: int) -> list[Any]:
    result = list(values) if isinstance(values, list) else []
    if value not in result and str(value) not in {str(item) for item in result}:
        result.append(value)
    return result


def _apply_telegram_scope(scope: dict[str, Any], hermes_bot_id: int) -> None:
    scope["allowBots"] = True
    scope["botLoopProtection"] = dict(LOOP_POLICY)
    scope["allowFrom"] = _append_unique(scope.get("allowFrom"), hermes_bot_id)
    scope["groupAllowFrom"] = _append_unique(scope.get("groupAllowFrom"), hermes_bot_id)

    groups = scope.get("groups")
    if not isinstance(groups, dict):
        return
    for group in groups.values():
        if not isinstance(group, dict):
            continue
        group["allowFrom"] = _append_unique(group.get("allowFrom"), hermes_bot_id)
        topics = group.get("topics")
        if not isinstance(topics, dict):
            continue
        for topic in topics.values():
            if isinstance(topic, dict):
                topic["allowFrom"] = _append_unique(topic.get("allowFrom"), hermes_bot_id)


def apply_policy(config: dict[str, Any], hermes_bot_id: int) -> bool:
    """Mutate config with the least-privilege handoff policy; return drift."""
    before = json.dumps(config, sort_keys=True, separators=(",", ":"))
    channels = config.setdefault("channels", {})
    telegram = channels.setdefault("telegram", {})
    if not isinstance(telegram, dict):
        raise ValueError("channels.telegram must be a JSON object")

    _apply_telegram_scope(telegram, hermes_bot_id)
    accounts = telegram.get("accounts")
    if isinstance(accounts, dict):
        for account in accounts.values():
            if isinstance(account, dict):
                _apply_telegram_scope(account, hermes_bot_id)

    after = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return before != after


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--hermes-bot-id", required=True, type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    config = json.loads(args.config.read_text())
    changed = apply_policy(config, args.hermes_bot_id)
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
    print(f"policy_status=applied backup={backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
