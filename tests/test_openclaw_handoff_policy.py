from __future__ import annotations

import importlib.util
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace


def load_module():
    path = Path(__file__).parents[1] / "runtime" / "apply_openclaw_telegram_handoff.py"
    spec = importlib.util.spec_from_file_location("apply_openclaw_telegram_handoff", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_apply_policy_changes_only_explicit_eligible_boundaries():
    module = load_module()
    owner_id = 1000000001
    peer_bot_id = 1000000002
    config = {
        "channels": {
            "telegram": {
                "allowFrom": [owner_id],
                "groupAllowFrom": [owner_id],
                "groups": {
                    "-1000000000001": {
                        "allowFrom": [owner_id],
                        "topics": {"101": {"allowFrom": [owner_id]}},
                    },
                    "-1000000000002": {
                        "allowFrom": [1000000099],
                        "topics": {"202": {"allowFrom": [1000000099]}},
                    },
                },
                "accounts": {
                    "default": {
                        "allowFrom": [owner_id],
                        "groupAllowFrom": [owner_id],
                        "groups": {
                            "-1000000000001": {
                                "allowFrom": [owner_id],
                                "topics": {"101": {"allowFrom": [owner_id]}},
                            },
                            "-1000000000002": {"allowFrom": [1000000099]},
                        },
                    },
                    "unrelated": {"allowFrom": [1000000099]},
                },
            }
        }
    }

    changed = module.apply_policy(
        config,
        hermes_bot_id=peer_bot_id,
        boundary_user_id=owner_id,
        account_name="default",
        allowed_group_ids={"-1000000000001"},
        allowed_topic_ids={"101"},
    )

    assert changed is True
    root = config["channels"]["telegram"]
    account = root["accounts"]["default"]
    for telegram in (root, account):
        assert telegram["allowBots"] is True
        assert peer_bot_id in telegram["allowFrom"]
        assert peer_bot_id not in telegram["groupAllowFrom"]
        assert telegram["botLoopProtection"] == {
            "enabled": True,
            "maxEventsPerWindow": 8,
            "windowSeconds": 60,
            "cooldownSeconds": 300,
        }
        approved = telegram["groups"]["-1000000000001"]
        assert peer_bot_id in approved["allowFrom"]
        assert peer_bot_id in approved["topics"]["101"]["allowFrom"]
        unrelated = telegram["groups"]["-1000000000002"]
        assert peer_bot_id not in unrelated["allowFrom"]

    assert peer_bot_id not in root["accounts"]["unrelated"]["allowFrom"]
    assert module.apply_policy(
        config,
        hermes_bot_id=peer_bot_id,
        boundary_user_id=owner_id,
        account_name="default",
        allowed_group_ids={"-1000000000001"},
        allowed_topic_ids={"101"},
    ) is False


def test_preserve_metadata_applies_ownership_before_mode(monkeypatch, tmp_path):
    module = load_module()
    target = tmp_path / "config.json"
    target.write_text("{}")
    calls = []
    monkeypatch.setattr(module.os, "chmod", lambda path, mode: calls.append(("chmod", path, mode)))
    monkeypatch.setattr(module.os, "chown", lambda path, uid, gid: calls.append(("chown", path, uid, gid)))
    original = SimpleNamespace(st_mode=0o100600, st_uid=1000, st_gid=1000)

    module._preserve_metadata(target, original)

    assert calls == [("chown", target, 1000, 1000), ("chmod", target, 0o600)]


def test_main_atomic_replace_preserves_final_metadata(monkeypatch, tmp_path):
    module = load_module()
    config_path = tmp_path / "openclaw.json"
    owner_id = 1000000001
    config_path.write_text(
        '{"channels":{"telegram":{"allowFrom":[1000000001],"groups":{"-1000000000001":{"allowFrom":[1000000001],"topics":{"101":{"allowFrom":[1000000001]}}}},"accounts":{"default":{"allowFrom":[1000000001],"groups":{"-1000000000001":{"allowFrom":[1000000001],"topics":{"101":{"allowFrom":[1000000001]}}}}}}}}}'
    )
    os.chmod(config_path, 0o640)
    before = config_path.stat()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "apply_openclaw_telegram_handoff.py",
            "--config", str(config_path),
            "--hermes-bot-id", "1000000002",
            "--boundary-user-id", str(owner_id),
            "--account", "default",
            "--group-id", "-1000000000001",
            "--topic-id", "101",
            "--apply",
        ],
    )

    assert module.main() == 0
    after = config_path.stat()
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)
    assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode)
