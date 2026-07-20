from __future__ import annotations

import base64
import datetime as dt
import hashlib
import html
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

ORG = "viewport-corp"
ISSUE_REPO = "viewport-ops"
KB_REPO = "viewport-kb"
OS_REPO = "viewport-os"
HOME = Path(os.environ.get("HERMES_HOME", "/opt/data"))
STATE_DIR = HOME / "intake-persistence"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "seen_sessions.json"
EVIDENCE_FILE = STATE_DIR / "last_capture.json"
QUEUE_DB = STATE_DIR / "capture_queue.sqlite3"
_WORKER_LOCK = threading.Lock()
_WORKER_WAKE = threading.Event()
_WORKER: threading.Thread | None = None

LABELS = {
    "chat-capture": "5319E7",
    "decision-log": "0E8A16",
    "correction": "D93F0B",
    "idea-log": "1D76DB",
    "reference-log": "0969DA",
    "needs-triage": "FBCA04",
    "needs-sam-review": "D876E3",
    "anti-amnesia": "6F42C1",
    "high-priority": "B60205",
    "security": "B60205",
    "tenant-viewport": "0052CC",
    "tenant-mlg": "2EA44F",
    "tenant-mlh": "2EA44F",
    "tenant-bccl": "BFDADC",
    "tenant-unknown": "C5DEF5",
    "dept-ops": "5319E7",
    "dept-engineering": "1D76DB",
    "dept-security": "B60205",
    "dept-kb": "0E8A16",
    "dept-finance": "FBCA04",
    "dept-legal": "D93F0B",
    "dept-product": "A2EEEF",
    "dept-unknown": "C5DEF5",
}

SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9_\-]{10,}",
    r"ghp_[A-Za-z0-9_\-]{10,}",
    r"xoxb-[A-Za-z0-9_\-]+",
    r"(?i)(telegram_bot_token|cf_api_key|api[_-]?key|secret|password|token)\s*[:=]\s*\S+",
    r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}",
]


def _load_env_token() -> str:
    for key in ["GITHUB_TOKEN_VIEWPORT_CORP", "GITHUB_TOKEN"]:
        if os.environ.get(key):
            return os.environ[key]
    env_path = HOME / ".env"
    if env_path.exists():
        for line in env_path.read_text(errors="ignore").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() in ["GITHUB_TOKEN_VIEWPORT_CORP", "GITHUB_TOKEN"]:
                    return v.strip().strip('"').strip("'")
    return ""


def _headers(extra=None):
    token = _load_env_token()
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = "Bearer " + token
    if extra:
        h.update(extra)
    return h


def gh(path: str, method="GET", data=None):
    body = None
    h = _headers()
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request("https://api.github.com" + path, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            txt = r.read().decode() or "{}"
            return r.status, json.loads(txt)
    except urllib.error.HTTPError as e:
        txt = e.read().decode(errors="replace")
        try:
            d = json.loads(txt)
        except Exception:
            d = {"raw": txt[:300]}
        return e.code, d
    except Exception as e:
        return "ERR", {"error": str(e)}


def safe_text(text: str) -> str:
    text = text or ""
    for pat in SECRET_PATTERNS:
        text = re.sub(pat, "[REDACTED]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:700]


def public_safe_text(text: str) -> str:
    """Create the only message representation allowed in the durable queue."""
    redacted = safe_text(text)

    def strip_url_credentials(match: re.Match[str]) -> str:
        try:
            parts = urllib.parse.urlsplit(match.group(0))
            host = parts.hostname or ""
            if parts.port:
                host += f":{parts.port}"
            return urllib.parse.urlunsplit((parts.scheme, host, "/", "", ""))
        except Exception:
            return "[REDACTED_URL]"

    return re.sub(r"https?://[^\s]+", strip_url_credentials, redacted)[:700]


def slugify(s: str) -> str:
    s = s.lower()
    if "agent token" in s or "agent tokens" in s:
        return "agent-tokens"
    if "neo4j" in s and "brain" in s:
        return "neo4j-company-brain"
    words = re.findall(r"[a-z0-9]+", s)[:8]
    return "-".join(words) or "capture"


def classify(text: str) -> List[str]:
    t = text.lower()
    tags = []
    if re.search(r"\b(need|needs|build|set up|setup|create|fix|audit|run|deploy|wire|disable|enable|test|schedule|commit)\b", t):
        tags.append("TASK")
    if re.search(r"\b(decide|decision|approved|approval|never|always|rule|correct answer|from now on|must|do not|don’t)\b", t):
        tags.append("DECISION")
    if re.search(r"\b(what if|idea|could sell|sell|business model|tokens per tenant|agent tokens|product idea)\b", t):
        tags.append("IDEA")
    if re.search(r"https?://|www\.", text) or re.search(r"\b(image|screenshot|video|link|url|article)\b", t):
        tags.append("REFERENCE")
    if "?" in text or re.search(r"^\s*(what|why|how|where|when|who|is|are|do|does|did|can|should)\b", t):
        tags.append("QUESTION")
    if re.search(r"\b(wrong|bad|don’t do|do not do|correction|feedback|you missed|not acceptable|fix your process)\b", t):
        tags.append("FEEDBACK")
    if re.search(r"\b(blocked|blocker|stuck|cannot|can't|failed|broken|access missing|missing access)\b", t):
        tags.append("BLOCKER")
    return sorted(set(tags)) or ["QUESTION"]


def infer_tenant(text: str) -> str:
    t = text.lower()
    if "bccl" in t or "phonem" in t or "laowise" in t: return "bccl"
    if "mlh" in t or "modern lao homes" in t: return "mlh"
    if "mlg" in t or "modern lao" in t: return "mlg"
    if "viewport" in t or "companyos" in t or "company os" in t: return "viewport"
    return "unknown"


def infer_dept(text: str) -> str:
    t = text.lower()
    if any(x in t for x in ["sell", "tenant", "product", "pricing", "agent tokens", "tokens per tenant"]): return "product"
    if any(x in t for x in ["secret", "credential", "token", "security"]): return "security"
    if any(x in t for x in ["github", "code", "repo", "build", "deploy", "neo4j", "qdrant", "docker"]): return "engineering"
    if any(x in t for x in ["finance", "invoice", "payment", "expense"]): return "finance"
    if any(x in t for x in ["legal", "signature", "approval"]): return "legal"
    if any(x in t for x in ["kb", "knowledge", "brain", "obsidian"]): return "kb"
    if any(x in t for x in ["sell", "tenant", "product", "pricing", "tokens"]): return "product"
    return "ops"


def summarize(text: str) -> str:
    clean = safe_text(text)
    low = clean.lower()
    if "neo4j" in low and "brain" in low:
        return "Set up Neo4j as a company-brain component for Viewport OS"
    if "agent tokens" in low:
        return "Explore selling agent tokens per tenant"
    urls = re.findall(r"https?://\S+", clean)
    if urls:
        return "Capture and analyze shared reference link"
    return clean[:110] or "Chat capture"


def issue_body(text: str, tags: List[str], tenant: str, dept: str, capture_id: str = "") -> str:
    text = public_safe_text(text)
    urls = re.findall(r"https?://\S+", text or "")
    return f"""## Source
Telegram DM with Sam, captured by intake_persistence pre-gateway hook.
Capture ID: `{capture_id or 'unavailable'}`

## What Sam said — paraphrased
{summarize(text)}

## Tags
{', '.join(tags)}

## Task / Decision / Correction
{summarize(text)}

## Context
Anti-amnesia intake pipeline. Public-safe paraphrase only; raw Telegram/session text is not stored.

## Tenant
{tenant}

## Department
{dept}

## Links
{chr(10).join(urls) if urls else 'None detected.'}
"""


def create_issue(kind: str, text: str, tags: List[str], tenant: str, dept: str, capture_id: str = ""):
    if kind == "DECISION":
        title = "[CHAT→DECISION] " + summarize(text)
        labels = ["decision-log", "chat-capture", "anti-amnesia", "needs-triage", f"tenant-{tenant}", f"dept-{dept}"]
    elif kind == "FEEDBACK":
        title = "[CHAT→CORRECTION] " + summarize(text)
        labels = ["correction", "high-priority", "chat-capture", "anti-amnesia", "needs-triage", f"tenant-{tenant}", f"dept-{dept}"]
    else:
        title = "[CHAT→TASK] " + summarize(text)
        labels = ["chat-capture", "anti-amnesia", "needs-triage", f"tenant-{tenant}", f"dept-{dept}"]
    # assignee hermes only if it exists; GitHub rejects unknown assignee, so omit on failure-prone hook.
    return gh(f"/repos/{ORG}/{ISSUE_REPO}/issues", "POST", {"title": title[:240], "body": issue_body(text,tags,tenant,dept,capture_id), "labels": labels})


def get_file(repo: str, path: str):
    return gh(f"/repos/{ORG}/{repo}/contents/{urllib.parse.quote(path)}")


def put_file(repo: str, path: str, content: str, msg: str):
    s, d = get_file(repo, path)
    data = {"message": msg, "content": base64.b64encode(content.encode()).decode(), "branch": "main"}
    if s == 200:
        data["sha"] = d.get("sha")
    return gh(f"/repos/{ORG}/{repo}/contents/{urllib.parse.quote(path)}", "PUT", data)


def fetch_url_summary(url: str) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Viewport intake bot"})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read(120000).decode(errors="ignore")
        title = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
        title_s = html.unescape(re.sub(r"\s+", " ", title.group(1)).strip()) if title else url
        text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<[^>]+>", " ", raw)
        text = html.unescape(re.sub(r"\s+", " ", text)).strip()
        return f"Title: {safe_text(title_s)}\n\nSummary: {safe_text(text[:900])}"
    except Exception as e:
        return f"Summary fetch failed safely: {type(e).__name__}. URL stored for later review."


def make_note(folder: str, text: str, tags: List[str], tenant: str, dept: str):
    now = dt.datetime.now(dt.timezone.utc)
    title = summarize(text)
    slug = slugify(title)
    path = f"{folder}/{now.strftime('%Y-%m-%d')}-{slug}.md"
    urls = re.findall(r"https?://\S+", text or "")
    if folder == "references":
        summary = fetch_url_summary(urls[0]) if urls else "Reference mentioned, but no URL was available in text payload."
        body = f"""---
title: {title}
date: {now.isoformat()}
source: telegram
type: {'link' if urls else 'reference'}
url: {urls[0] if urls else ''}
status: captured-reference
tenant: {tenant}
department: {dept}
tags: {json.dumps(tags)}
---

# {title}

## Summary

{summary}

## Why Sam shared this

Likely relevant to current Viewport/CompanyOS work; captured for anti-amnesia review.

## Relevant to

Tenant: {tenant}; Department: {dept}

## Action potential

Review and convert into task/decision if it changes architecture, product, or operations.
"""
    elif folder == "ideas":
        body = f"""---
title: {title}
date: {now.isoformat()}
source: telegram
status: raw-idea
tenant: {tenant}
department: {dept}
tags: {json.dumps(tags)}
---

# {title}

## What was said — paraphrased

{title}

## Why it matters

Potential product/business direction for Viewport or tenant systems.

## Possible next step

Validate market, pricing, implementation cost, and tenant-fit before promoting.

## Related links

{chr(10).join(urls) if urls else 'None.'}
"""
    else:
        body = f"""---
title: {title}
date: {now.isoformat()}
source: telegram
status: captured-question-or-decision
tenant: {tenant}
department: {dept}
tags: {json.dumps(tags)}
---

# {title}

## Question / decision — paraphrased

{title}

## Answer / current state

Captured for follow-up. Link to GitHub issue if it changes work.
"""
    s, d = put_file(KB_REPO, path, body, "feat: intake-to-persistence pipeline v1")
    rebuild_index()
    return path, s, d


def rebuild_index():
    s, d = gh(f"/repos/{ORG}/{KB_REPO}/git/trees/main?recursive=1")
    files = []
    if s == 200:
        for item in d.get("tree", []):
            p = item.get("path", "")
            if p.endswith(".md") and p != "INDEX.md":
                files.append(p)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    lines = ["# Viewport KB Index", "", f"Generated: {now}", "", f"Total notes: {len(files)}", "", "## By date", ""]
    for p in sorted(files):
        lines.append(f"- [{p}]({p})")
    lines += ["", "## By tag", "", "_Tag index pending deeper parser._", "", "## By tenant", "", "_Tenant index pending deeper parser._", "", "## By status", "", "_Status index pending deeper parser._", ""]
    put_file(KB_REPO, "INDEX.md", "\n".join(lines), "chore: rebuild KB index")


def load_start_context(session_key: str) -> str:
    try:
        seen = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except Exception:
        seen = {}
    if seen.get(session_key):
        return ""
    seen[session_key] = dt.datetime.now(dt.timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(seen, indent=2))
    # Recent issues
    s, issues = gh(f"/repos/{ORG}/{ISSUE_REPO}/issues?state=open&labels=chat-capture&per_page=5")
    n_issues = len(issues) if s == 200 and isinstance(issues, list) else 0
    # KB notes count
    s2, tree = gh(f"/repos/{ORG}/{KB_REPO}/git/trees/main?recursive=1")
    notes = [x.get("path") for x in tree.get("tree", []) if x.get("path", "").endswith(".md") and x.get("path") != "INDEX.md"] if s2 == 200 else []
    # HANDOFF date
    s3, hand = get_file(OS_REPO, "HANDOFF.md")
    last = "unknown"
    if s3 == 200:
        try:
            content = base64.b64decode(hand.get("content", "")).decode(errors="ignore")
            m = re.search(r"Last updated:\s*(.+)", content)
            if m: last = m.group(1).strip()
        except Exception:
            pass
    return f"Loaded: {n_issues} recent issues, KB has {len(notes)} notes, last session: {last}."


def _connect_queue() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    db = sqlite3.connect(QUEUE_DB, timeout=0.25)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        """CREATE TABLE IF NOT EXISTS captures (
        capture_id TEXT PRIMARY KEY,
        safe_text TEXT,
        session_key_hash TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        issue_kind TEXT,
        issue_number INTEGER,
        issue_url TEXT,
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
        )"""
    )
    db.commit()
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(QUEUE_DB) + suffix)
        if path.exists():
            os.chmod(path, 0o600)
    return db


def _recover_stale_processing() -> int:
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)).isoformat()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with _connect_queue() as db:
        cursor = db.execute(
            """UPDATE captures SET status='retry', last_error='worker_restarted', updated_at=?
            WHERE status='processing' AND updated_at < ?""",
            (now, cutoff),
        )
        return cursor.rowcount


def _event_capture_id(event: Any, source: Any, text: str) -> str | None:
    del text
    platform = getattr(getattr(source, "platform", None), "value", getattr(source, "platform", ""))
    message_id = (
        getattr(source, "message_id", None)
        or getattr(event, "message_id", None)
        or getattr(event, "platform_update_id", None)
    )
    if not message_id:
        # There is no safe way to distinguish a retry from a distinct identical
        # message without a transport identifier. Skip only the audit side effect;
        # the immutable message still proceeds to the agent.
        return None
    stable = ":".join(
        str(value or "")
        for value in (platform, getattr(source, "chat_id", ""), getattr(source, "thread_id", ""), message_id)
    )
    return "telegram:" + hashlib.sha256(stable.encode()).hexdigest()[:24]


def enqueue_capture(capture_id: str, text: str, session_key: str) -> bool:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with _connect_queue() as db:
        cursor = db.execute(
            """INSERT OR IGNORE INTO captures
            (capture_id, safe_text, session_key_hash, status, created_at, updated_at)
            VALUES (?, ?, ?, 'pending', ?, ?)""",
            (
                capture_id,
                public_safe_text(text),
                hashlib.sha256(session_key.encode()).hexdigest(),
                now,
                now,
            ),
        )
        inserted = cursor.rowcount == 1
    _ensure_worker()
    _WORKER_WAKE.set()
    return inserted


def find_issue_by_capture_id(capture_id: str) -> Dict[str, Any] | None:
    marker = f"Capture ID: `{capture_id}`"
    for page in range(1, 21):
        status, data = gh(
            f"/repos/{ORG}/{ISSUE_REPO}/issues?state=all&sort=created&direction=desc&per_page=100&page={page}"
        )
        if status != 200 or not isinstance(data, list):
            raise RuntimeError(f"issue_reconciliation_lookup_{status}")
        for item in data:
            if isinstance(item, dict) and marker in str(item.get("body") or ""):
                return item
        if len(data) < 100:
            return None
    raise RuntimeError("issue_reconciliation_incomplete")


def _write_evidence(result: Dict[str, Any]) -> None:
    minimal = {
        key: result.get(key)
        for key in ("capture_id", "status", "issue_kind", "issue_number", "issue_url", "issue_reconciled")
    }
    EVIDENCE_FILE.write_text(json.dumps(minimal, indent=2))
    os.chmod(EVIDENCE_FILE, 0o600)


def capture_message(
    text: str,
    session_key: str = "manual-test",
    capture_id: str | None = None,
    persist_issue=None,
    existing_issue_number: int | None = None,
    existing_issue_url: str | None = None,
) -> Dict[str, Any]:
    del session_key
    text = public_safe_text(text)
    tags = classify(text)
    tenant = infer_tenant(text)
    dept = infer_dept(text)
    kind = next((value for value in ("FEEDBACK", "DECISION", "TASK") if value in tags), None)
    result: Dict[str, Any] = {
        "capture_id": capture_id,
        "status": "completed",
        "issue_kind": kind,
        "issue_number": None,
        "issue_url": None,
        "issue_reconciled": False,
    }
    if kind and capture_id:
        if existing_issue_number:
            result.update(
                issue_number=existing_issue_number,
                issue_url=existing_issue_url,
                issue_reconciled=True,
            )
        else:
            existing = find_issue_by_capture_id(capture_id)
            if existing:
                result.update(
                    issue_number=existing.get("number"),
                    issue_url=existing.get("html_url"),
                    issue_reconciled=True,
                )
            else:
                status, data = create_issue(kind, text, tags, tenant, dept, capture_id)
                if status != 201 or not isinstance(data, dict) or not data.get("number"):
                    raise RuntimeError(f"issue_create_{status}")
                result.update(issue_number=data.get("number"), issue_url=data.get("html_url"))
        if persist_issue:
            persist_issue(kind, result["issue_number"], result["issue_url"])

    # KB effects use sanitized text and deterministic paths; they are secondary
    # to the canonical issue and never run on the gateway dispatch path.
    if "IDEA" in tags:
        _, status, _ = make_note("ideas", text, tags, tenant, dept)
        if status not in (200, 201):
            raise RuntimeError(f"idea_capture_{status}")
    if "REFERENCE" in tags:
        _, status, _ = make_note("references", text, tags, tenant, dept)
        if status not in (200, 201):
            raise RuntimeError(f"reference_capture_{status}")
    _write_evidence(result)
    return result


def _persist_issue_effect(capture_id: str, kind: str, number: int, url: str | None) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with _connect_queue() as db:
        db.execute(
            """UPDATE captures SET issue_kind=?, issue_number=?, issue_url=?, updated_at=?
            WHERE capture_id=?""",
            (kind, number, url, now, capture_id),
        )


def _claim_next_capture() -> sqlite3.Row | None:
    db = _connect_queue()
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM captures WHERE status IN ('pending','retry') ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row:
            db.execute(
                "UPDATE captures SET status='processing', attempts=attempts+1, updated_at=? WHERE capture_id=?",
                (dt.datetime.now(dt.timezone.utc).isoformat(), row["capture_id"]),
            )
        db.commit()
        return row
    finally:
        db.close()


def _finish_capture(capture_id: str, status: str, error: str | None = None) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with _connect_queue() as db:
        attempts = db.execute("SELECT attempts FROM captures WHERE capture_id=?", (capture_id,)).fetchone()
        final_status = "failed" if status == "retry" and attempts and attempts[0] >= 10 else status
        clear_text = final_status in ("completed", "failed")
        db.execute(
            """UPDATE captures SET status=?, safe_text=CASE WHEN ? THEN NULL ELSE safe_text END,
            last_error=?, updated_at=? WHERE capture_id=?""",
            (final_status, clear_text, error, now, capture_id),
        )


def _retry_delay(previous_attempts: int) -> int:
    return min(2 ** (max(0, previous_attempts) + 1), 60)


def _worker_loop() -> None:
    while True:
        try:
            _recover_stale_processing()
            row = _claim_next_capture()
        except Exception:
            _WORKER_WAKE.wait(2.0)
            _WORKER_WAKE.clear()
            continue
        if row is None:
            _WORKER_WAKE.wait(2.0)
            _WORKER_WAKE.clear()
            continue
        capture_id = row["capture_id"]
        try:
            capture_message(
                row["safe_text"] or "",
                capture_id=capture_id,
                persist_issue=lambda kind, number, url: _persist_issue_effect(capture_id, kind, number, url),
                existing_issue_number=row["issue_number"],
                existing_issue_url=row["issue_url"],
            )
        except Exception as exc:
            try:
                _finish_capture(capture_id, "retry", type(exc).__name__)
            except Exception:
                pass
            time.sleep(_retry_delay(int(row["attempts"] or 0)))
        else:
            try:
                _finish_capture(capture_id, "completed")
            except Exception:
                pass


def _ensure_worker() -> None:
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is None or not _WORKER.is_alive():
            _recover_stale_processing()
            _WORKER = threading.Thread(target=_worker_loop, name="intake-persistence", daemon=True)
            _WORKER.start()


def pre_gateway_dispatch(event=None, gateway=None, session_store=None, **kwargs):
    if event is None:
        return {"action":"allow"}
    source = getattr(event, "source", None)
    platform = getattr(getattr(source, "platform", None), "value", getattr(source, "platform", ""))
    if platform != "telegram":
        return {"action":"allow"}
    text = getattr(event, "text", None) or ""
    if not text.strip() or text.strip().startswith("/"):
        return {"action":"allow"}
    original_text = text
    session_key = f"{platform}:{getattr(source,'chat_id', '')}:{getattr(source,'thread_id', '')}:{getattr(source,'user_id','')}"
    try:
        capture_id = _event_capture_id(event, source, original_text)
        if capture_id:
            enqueue_capture(capture_id, original_text, session_key)
    except Exception:
        # Persistence is an audit side effect, never the transport. GitHub/KB
        # failure must not block, replace, or truncate the user's instruction.
        pass
    return {"action":"allow"}


def register(ctx):
    _connect_queue().close()
    _recover_stale_processing()
    _ensure_worker()
    ctx.register_hook("pre_gateway_dispatch", pre_gateway_dispatch)
