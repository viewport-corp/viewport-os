from __future__ import annotations

import importlib.util
from pathlib import Path


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
                "allowFrom": [6596211381],
                "groupAllowFrom": [6596211381],
                "groups": {
                    "-1003964024603": {
                        "allowFrom": [6596211381],
                        "topics": {"211": {"allowFrom": [6596211381]}},
                    }
                },
                "accounts": {
                    "default": {
                        "allowFrom": [6596211381],
                        "groupAllowFrom": [6596211381],
                        "groups": {
                            "-1003964024603": {
                                "allowFrom": [6596211381],
                                "topics": {"211": {"allowFrom": [6596211381]}},
                            }
                        },
                    }
                },
            }
        }
    }

    changed = module.apply_policy(config, hermes_bot_id=8518793332)

    assert changed is True
    root = config["channels"]["telegram"]
    account = root["accounts"]["default"]
    for telegram in (root, account):
        assert telegram["allowBots"] is True
        assert 8518793332 in telegram["allowFrom"]
        assert 8518793332 in telegram["groupAllowFrom"]
        assert telegram["botLoopProtection"] == {
            "enabled": True,
            "maxEventsPerWindow": 8,
            "windowSeconds": 60,
            "cooldownSeconds": 300,
        }
        group = telegram["groups"]["-1003964024603"]
        assert 8518793332 in group["allowFrom"]
        assert 8518793332 in group["topics"]["211"]["allowFrom"]

    assert module.apply_policy(config, hermes_bot_id=8518793332) is False
