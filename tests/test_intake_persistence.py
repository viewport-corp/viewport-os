from __future__ import annotations

import importlib.util
import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace


def load_plugin(tmp_path: Path):
    os.environ["HERMES_HOME"] = str(tmp_path)
    plugin_path = Path(__file__).parents[1] / "intake-persistence" / "__init__.py"
    spec = importlib.util.spec_from_file_location(f"intake_persistence_{tmp_path.name}", plugin_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def telegram_event(text: str, message_id: str = "501"):
    source = SimpleNamespace(
        platform=SimpleNamespace(value="telegram"),
        chat_id="-1000000000001",
        thread_id="101",
        user_id="1000000001",
        message_id=None,
    )
    return SimpleNamespace(
        source=source,
        text=text,
        message_id=message_id,
        platform_update_id=f"update-{message_id}",
    )


def test_pre_gateway_enqueues_and_preserves_complete_original_message(tmp_path):
    plugin = load_plugin(tmp_path)
    original = "Full instruction that must reach Hermes unchanged, including everything after character 110."
    seen = {}
    plugin.enqueue_capture = lambda capture_id, text, session_key: seen.update(
        capture_id=capture_id, text=text, session_key=session_key
    )

    result = plugin.pre_gateway_dispatch(telegram_event(original))

    assert result == {"action": "allow"}
    assert seen["text"] == original
    assert seen["capture_id"].startswith("telegram:")


def test_enqueue_failure_never_blocks_original_message(tmp_path):
    plugin = load_plugin(tmp_path)
    plugin.enqueue_capture = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("queue unavailable"))

    assert plugin.pre_gateway_dispatch(telegram_event("Keep this message")) == {"action": "allow"}


def test_gateway_path_never_calls_github_or_kb(tmp_path):
    plugin = load_plugin(tmp_path)
    plugin._ensure_worker = lambda: None
    plugin.gh = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network call on dispatch path"))

    assert plugin.pre_gateway_dispatch(telegram_event("Create one task")) == {"action": "allow"}


def test_one_message_creates_one_canonical_issue_for_multiple_tags(tmp_path):
    plugin = load_plugin(tmp_path)
    plugin.classify = lambda text: ["TASK", "DECISION", "FEEDBACK"]
    plugin.infer_tenant = lambda text: "viewport"
    plugin.infer_dept = lambda text: "ops"
    plugin.find_issue_by_capture_id = lambda capture_id: None
    calls = []

    def fake_create_issue(kind, text, tags, tenant, dept, capture_id=""):
        calls.append((kind, capture_id))
        return 201, {"number": 700, "html_url": "https://example.test/issues/700"}

    plugin.create_issue = fake_create_issue
    result = plugin.capture_message("Fix this decision and task", capture_id="telegram:message:501")

    assert calls == [("FEEDBACK", "telegram:message:501")]
    assert result["issue_number"] == 700
    assert "summary" not in result
    assert "text" not in result


def test_retry_reconciles_github_after_crash_window(tmp_path):
    plugin = load_plugin(tmp_path)
    plugin.classify = lambda text: ["TASK"]
    plugin.infer_tenant = lambda text: "viewport"
    plugin.infer_dept = lambda text: "ops"
    created = []

    def find_existing(capture_id):
        return created[0] if created else None

    def create_once(kind, text, tags, tenant, dept, capture_id=""):
        issue = {"number": 701, "html_url": "https://example.test/issues/701"}
        created.append(issue)
        return 201, issue

    plugin.find_issue_by_capture_id = find_existing
    plugin.create_issue = create_once

    def crash_after_post(*args):
        raise RuntimeError("simulated crash after GitHub POST")

    try:
        plugin.capture_message("Do this once", capture_id="telegram:message:777", persist_issue=crash_after_post)
    except RuntimeError:
        pass

    recovered = plugin.capture_message("Do this once", capture_id="telegram:message:777")

    assert len(created) == 1
    assert recovered["issue_number"] == 701
    assert recovered["issue_reconciled"] is True


def test_reconciliation_failure_is_fail_closed_and_never_posts(tmp_path):
    plugin = load_plugin(tmp_path)
    plugin.classify = lambda text: ["TASK"]
    plugin.infer_tenant = lambda text: "viewport"
    plugin.infer_dept = lambda text: "ops"
    plugin.find_issue_by_capture_id = lambda capture_id: (_ for _ in ()).throw(
        RuntimeError("github lookup unavailable")
    )
    posts = []
    plugin.create_issue = lambda *args, **kwargs: posts.append(1)

    try:
        plugin.capture_message("Do not duplicate", capture_id="telegram:message:779")
    except RuntimeError:
        pass

    assert posts == []


def test_persisted_issue_effect_skips_reconciliation_and_post(tmp_path):
    plugin = load_plugin(tmp_path)
    plugin.classify = lambda text: ["TASK"]
    plugin.infer_tenant = lambda text: "viewport"
    plugin.infer_dept = lambda text: "ops"
    plugin.find_issue_by_capture_id = lambda capture_id: (_ for _ in ()).throw(
        AssertionError("lookup should not run")
    )
    plugin.create_issue = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("post should not run"))

    result = plugin.capture_message(
        "Resume secondary work",
        capture_id="telegram:message:780",
        existing_issue_number=703,
        existing_issue_url="https://example.test/issues/703",
    )

    assert result["issue_number"] == 703
    assert result["issue_reconciled"] is True


def test_issue_success_followed_by_kb_failure_reconciles_on_retry(tmp_path):
    plugin = load_plugin(tmp_path)
    plugin.classify = lambda text: ["TASK", "IDEA"]
    plugin.infer_tenant = lambda text: "viewport"
    plugin.infer_dept = lambda text: "ops"
    created = []
    notes = []
    plugin.find_issue_by_capture_id = lambda capture_id: created[0] if created else None

    def create_once(kind, text, tags, tenant, dept, capture_id=""):
        issue = {"number": 702, "html_url": "https://example.test/issues/702"}
        created.append(issue)
        return 201, issue

    def flaky_note(*args):
        notes.append(1)
        return "ideas/example.md", (500 if len(notes) == 1 else 201), {}

    plugin.create_issue = create_once
    plugin.make_note = flaky_note

    try:
        plugin.capture_message("Build this product idea", capture_id="telegram:message:778")
    except RuntimeError:
        pass
    recovered = plugin.capture_message("Build this product idea", capture_id="telegram:message:778")

    assert len(created) == 1
    assert len(notes) == 2
    assert recovered["issue_number"] == 702
    assert recovered["issue_reconciled"] is True


def test_concurrent_duplicate_delivery_creates_one_queue_row(tmp_path):
    plugin = load_plugin(tmp_path)
    plugin._ensure_worker = lambda: None

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda _: plugin.enqueue_capture("telegram:concurrent:1", "Do this once", "session-a"),
                range(20),
            )
        )

    with sqlite3.connect(plugin.QUEUE_DB) as db:
        count = db.execute("SELECT COUNT(*) FROM captures WHERE capture_id = ?", ("telegram:concurrent:1",)).fetchone()[0]
    assert count == 1
    assert oct(plugin.QUEUE_DB.stat().st_mode & 0o777) == "0o600"


def test_stale_processing_rows_are_recovered_after_worker_crash(tmp_path):
    plugin = load_plugin(tmp_path)
    plugin._ensure_worker = lambda: None
    plugin.enqueue_capture("telegram:stale:1", "Recover me", "session-a")
    with sqlite3.connect(plugin.QUEUE_DB) as db:
        db.execute(
            "UPDATE captures SET status='processing', updated_at='2000-01-01T00:00:00+00:00' WHERE capture_id=?",
            ("telegram:stale:1",),
        )

    recovered = plugin._recover_stale_processing()

    with sqlite3.connect(plugin.QUEUE_DB) as db:
        status = db.execute(
            "SELECT status FROM captures WHERE capture_id=?", ("telegram:stale:1",)
        ).fetchone()[0]
    assert recovered == 1
    assert status == "retry"


def test_retry_delay_is_bounded_exponential(tmp_path):
    plugin = load_plugin(tmp_path)

    assert [plugin._retry_delay(attempt) for attempt in range(6)] == [2, 4, 8, 16, 32, 60]
    assert plugin._retry_delay(50) == 60


def test_worker_restart_runs_stale_recovery(tmp_path):
    plugin = load_plugin(tmp_path)
    calls = []
    plugin._recover_stale_processing = lambda: calls.append("recover") or 0

    class FakeThread:
        def __init__(self, *args, **kwargs):
            self.started = False

        def is_alive(self):
            return self.started

        def start(self):
            self.started = True

    plugin.threading.Thread = FakeThread
    plugin._WORKER = None
    plugin._ensure_worker()

    assert calls == ["recover"]


def test_queue_stores_only_redacted_public_safe_text(tmp_path):
    plugin = load_plugin(tmp_path)
    plugin._ensure_worker = lambda: None
    raw = (
        "Use " + "token" + "=" + "demo-value"
        + " and inspect https://example.test/reset/lower-path?signature=private#fragment"
        + " then HTTPS://upper.example.test/reset/upper-path?signature=private#fragment"
        + " then www.scheme-less.example.test/reset/scheme-less-path?signature=private#fragment"
    )

    plugin.enqueue_capture("telegram:safe:1", raw, "private-session-key")

    with sqlite3.connect(plugin.QUEUE_DB) as db:
        safe_text, session_hash = db.execute(
            "SELECT safe_text, session_key_hash FROM captures WHERE capture_id = ?", ("telegram:safe:1",)
        ).fetchone()
    assert "demo-value" not in safe_text
    assert "signature=" not in safe_text
    assert "#fragment" not in safe_text
    assert "lower-path" not in safe_text
    assert "upper-path" not in safe_text
    assert "scheme-less-path" not in safe_text
    assert "https://example.test/" in safe_text
    assert "https://upper.example.test/" in safe_text
    assert "https://www.scheme-less.example.test/" in safe_text
    assert "private-session-key" not in session_hash

    for suffix in ("", "-wal", "-shm"):
        path = Path(str(plugin.QUEUE_DB) + suffix)
        if path.exists():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_event_capture_id_uses_canonical_message_fields(tmp_path):
    plugin = load_plugin(tmp_path)
    first = telegram_event("same text", "501")
    retry = telegram_event("same text", "501")
    distinct = telegram_event("same text", "502")

    assert plugin._event_capture_id(first, first.source, first.text) == plugin._event_capture_id(retry, retry.source, retry.text)
    assert plugin._event_capture_id(first, first.source, first.text) != plugin._event_capture_id(
        distinct, distinct.source, distinct.text
    )


def test_event_without_transport_id_skips_audit_capture(tmp_path):
    plugin = load_plugin(tmp_path)
    event = telegram_event("same text", "501")
    event.message_id = None
    event.platform_update_id = None

    assert plugin._event_capture_id(event, event.source, event.text) is None
