from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace


def load_plugin(tmp_path: Path):
    os.environ["HERMES_HOME"] = str(tmp_path)
    plugin_path = Path(__file__).parents[1] / "intake-persistence" / "__init__.py"
    spec = importlib.util.spec_from_file_location("intake_persistence_under_test", plugin_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def telegram_event(text: str, message_id: str = "501"):
    source = SimpleNamespace(
        platform=SimpleNamespace(value="telegram"),
        chat_id="-1003964024603",
        thread_id="211",
        user_id="6596211381",
        message_id=message_id,
    )
    return SimpleNamespace(source=source, text=text)


def test_pre_gateway_preserves_the_complete_original_message(tmp_path):
    plugin = load_plugin(tmp_path)
    original = "Full instruction that must reach Hermes unchanged, including everything after character 110."
    seen = {}

    def fake_capture(text, session_key="manual-test", capture_id=None):
        seen.update(text=text, session_key=session_key, capture_id=capture_id)
        return {"captures": [{"type": "issue", "number": 496}]}

    plugin.capture_message = fake_capture

    result = plugin.pre_gateway_dispatch(telegram_event(original))

    assert result == {"action": "allow"}
    assert seen["text"] == original
    assert seen["capture_id"]


def test_capture_failure_never_blocks_the_original_message(tmp_path):
    plugin = load_plugin(tmp_path)

    def fail_capture(*args, **kwargs):
        raise RuntimeError("GitHub unavailable")

    plugin.capture_message = fail_capture

    assert plugin.pre_gateway_dispatch(telegram_event("Keep this message")) == {"action": "allow"}


def test_one_message_creates_one_canonical_issue_for_multiple_tags(tmp_path):
    plugin = load_plugin(tmp_path)
    plugin.classify = lambda text: ["TASK", "DECISION", "FEEDBACK"]
    plugin.infer_tenant = lambda text: "viewport"
    plugin.infer_dept = lambda text: "ops"
    plugin.load_start_context = lambda session_key: ""
    calls = []

    def fake_create_issue(kind, text, tags, tenant, dept, capture_id=""):
        calls.append((kind, capture_id))
        return 201, {"number": 700, "html_url": "https://example.test/issues/700"}

    plugin.create_issue = fake_create_issue

    result = plugin.capture_message("Fix this decision and task", capture_id="telegram:message:501")

    assert calls == [("FEEDBACK", "telegram:message:501")]
    assert [item for item in result["captures"] if item["type"] == "issue"] == [
        {
            "type": "issue",
            "kind": "FEEDBACK",
            "status": 201,
            "number": 700,
            "url": "https://example.test/issues/700",
        }
    ]


def test_capture_id_makes_retried_telegram_update_idempotent(tmp_path):
    plugin = load_plugin(tmp_path)
    plugin.classify = lambda text: ["TASK"]
    plugin.infer_tenant = lambda text: "viewport"
    plugin.infer_dept = lambda text: "ops"
    plugin.load_start_context = lambda session_key: ""
    calls = []

    def fake_create_issue(kind, text, tags, tenant, dept, capture_id=""):
        calls.append(capture_id)
        return 201, {"number": 701, "html_url": "https://example.test/issues/701"}

    plugin.create_issue = fake_create_issue

    first = plugin.capture_message("Do this once", capture_id="telegram:message:777")
    second = plugin.capture_message("Do this once", capture_id="telegram:message:777")

    assert calls == ["telegram:message:777"]
    assert first["captures"][0]["number"] == 701
    assert second["captures"][0]["number"] == 701
    assert second["duplicate"] is True
