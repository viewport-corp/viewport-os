from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def load_module():
    path = Path(__file__).parents[1] / "runtime" / "apply_openclaw_telegram_handoff.py"
    spec = importlib.util.spec_from_file_location("apply_openclaw_telegram_handoff", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_apply_policy_scopes_hermes_to_existing_telegram_boundaries():
    module = load_module()
    config = {
        "channels": {
            "telegram": {
                "allowFrom": [1000000001],
                "groupAllowFrom": [1000000001],
                "groups": {
                    "-1000000000001": {
                        "allowFrom": [1000000001],
                        "topics": {"101": {"allowFrom": [1000000001]}},
                    }
                },
                "accounts": {
                    "default": {
                        "allowFrom": [1000000001],
                        "groupAllowFrom": [1000000001],
                        "groups": {
                            "-1000000000001": {
                                "allowFrom": [1000000001],
                                "topics": {"101": {"allowFrom": [1000000001]}},
                            }
                        },
                    }
                },
            }
        }
    }

    changed = module.apply_policy(config, hermes_bot_id=1000000002)

    assert changed is True
    root = config["channels"]["telegram"]
    account = root["accounts"]["default"]
    for telegram in (root, account):
        assert telegram["allowBots"] is True
        assert 1000000002 in telegram["allowFrom"]
        assert 1000000002 in telegram["groupAllowFrom"]
        assert telegram["botLoopProtection"] == {
            "enabled": True,
            "maxEventsPerWindow": 8,
            "windowSeconds": 60,
            "cooldownSeconds": 300,
        }
        group = telegram["groups"]["-1000000000001"]
        assert 1000000002 in group["allowFrom"]
        assert 1000000002 in group["topics"]["101"]["allowFrom"]

    assert module.apply_policy(config, hermes_bot_id=1000000002) is False


def test_preserve_metadata_restores_owner_group_and_mode(monkeypatch, tmp_path):
    module = load_module()
    target = tmp_path / "config.json"
    target.write_text("{}")
    calls = []
    monkeypatch.setattr(module.os, "chmod", lambda path, mode: calls.append(("chmod", path, mode)))
    monkeypatch.setattr(module.os, "chown", lambda path, uid, gid: calls.append(("chown", path, uid, gid)))
    original = SimpleNamespace(st_mode=0o100600, st_uid=1000, st_gid=1000)

    module._preserve_metadata(target, original)

    assert ("chmod", target, 0o600) in calls
    assert ("chown", target, 1000, 1000) in calls
