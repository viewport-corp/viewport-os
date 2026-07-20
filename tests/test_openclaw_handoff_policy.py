from __future__ import annotations

import copy
import importlib.util
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_module():
    path = Path(__file__).parents[1] / "runtime" / "apply_openclaw_telegram_handoff.py"
    spec = importlib.util.spec_from_file_location("apply_openclaw_telegram_handoff", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_scope(owner_id: int):
    return {
        "allowFrom": [owner_id],
        "groupAllowFrom": [owner_id],
        "groups": {
            "-1000000000001": {
                "allowFrom": [owner_id],
                "topics": {
                    "101": {"allowFrom": [owner_id]},
                    "202": {"allowFrom": [owner_id]},
                },
            },
            "-1000000000002": {
                "allowFrom": [owner_id],
                "topics": {
                    "101": {"allowFrom": [owner_id]},
                    "202": {"allowFrom": [owner_id]},
                },
            },
            "-1000000000003": {"allowFrom": [1000000099]},
        },
    }


def make_config(owner_id: int):
    root = make_scope(owner_id)
    root["accounts"] = {
        "default": make_scope(owner_id),
        "unrelated": {"allowFrom": [1000000099]},
    }
    return {"channels": {"telegram": root}}


def test_apply_policy_changes_only_exact_eligible_group_topic_pairs():
    module = load_module()
    owner_id = 1000000001
    peer_bot_id = 1000000002
    config = make_config(owner_id)
    boundaries = {
        "-1000000000001": {"101"},
        "-1000000000002": {"202"},
    }

    changed = module.apply_policy(
        config,
        hermes_bot_id=peer_bot_id,
        boundary_user_id=owner_id,
        account_name="default",
        allowed_boundaries=boundaries,
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
        assert peer_bot_id in telegram["groups"]["-1000000000001"]["allowFrom"]
        assert peer_bot_id in telegram["groups"]["-1000000000001"]["topics"]["101"]["allowFrom"]
        assert peer_bot_id not in telegram["groups"]["-1000000000001"]["topics"]["202"]["allowFrom"]
        assert peer_bot_id in telegram["groups"]["-1000000000002"]["topics"]["202"]["allowFrom"]
        assert peer_bot_id not in telegram["groups"]["-1000000000002"]["topics"]["101"]["allowFrom"]
        assert peer_bot_id not in telegram["groups"]["-1000000000003"]["allowFrom"]

    assert peer_bot_id not in root["accounts"]["unrelated"]["allowFrom"]
    assert module.apply_policy(
        config,
        hermes_bot_id=peer_bot_id,
        boundary_user_id=owner_id,
        account_name="default",
        allowed_boundaries=boundaries,
    ) is False


def test_policy_validation_is_fail_closed_before_mutation():
    module = load_module()
    owner_id = 1000000001
    boundaries = {"-1000000000001": {"101"}}

    for mutate in (
        lambda cfg: cfg.pop("channels"),
        lambda cfg: cfg["channels"]["telegram"]["accounts"].pop("default"),
        lambda cfg: cfg["channels"]["telegram"].__setitem__("allowFrom", "owner-only"),
        lambda cfg: cfg["channels"]["telegram"]["groups"]["-1000000000001"]["topics"]["101"].__setitem__("allowFrom", [1000000099]),
    ):
        config = make_config(owner_id)
        mutate(config)
        before = copy.deepcopy(config)
        with pytest.raises(ValueError):
            module.apply_policy(
                config,
                hermes_bot_id=1000000002,
                boundary_user_id=owner_id,
                account_name="default",
                allowed_boundaries=boundaries,
            )
        assert config == before


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
    config = make_config(owner_id)
    import json
    config_path.write_text(json.dumps(config))
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
            "--boundary=-1000000000001:101",
            "--apply",
        ],
    )

    assert module.main() == 0
    after = config_path.stat()
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)
    assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode)
