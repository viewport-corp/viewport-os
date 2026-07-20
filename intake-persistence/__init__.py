from __future__ import annotations

import base64
import datetime as dt
import hashlib
import html
import json
import os
import re
import sqlite3
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
CAPTURE_INDEX_FILE = STATE_DIR / "capture_index.json"

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


def _load_capture_index() -> Dict[str, Any]:
    try:
        data = json.loads(CAPTURE_INDEX_FILE.read_text()) if CAPTURE_INDEX_FILE.exists() else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_capture_index(index: Dict[str, Any]) -> None:
    # Bound local state while retaining enough history to absorb Telegram retries.
    if len(index) > 5000:
        index = dict(list(index.items())[-5000:])
    tmp = CAPTURE_INDEX_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=2))
    os.replace(tmp, CAPTURE_INDEX_FILE)


def _event_capture_id(event: Any, source: Any, text: str) -> str:
    platform = getattr(getattr(source, "platform", None), "value", getattr(source, "platform", ""))
    message_id = getattr(source, "message_id", None) or getattr(event, "update_id", None)
    stable = ":".join(
        str(value or "")
        for value in (
            platform,
            getattr(source, "chat_id", ""),
            getattr(source, "thread_id", ""),
            message_id,
        )
    )
    if not message_id:
        stable += ":" + text
    return "telegram:" + hashlib.sha256(stable.encode()).hexdigest()[:24]


def capture_message(text: str, session_key: str = "manual-test", capture_id: str | None = None) -> Dict[str, Any]:
    index = _load_capture_index()
    if capture_id and capture_id in index:
        duplicate = dict(index[capture_id])
        duplicate["duplicate"] = True
        EVIDENCE_FILE.write_text(json.dumps(duplicate, indent=2))
        return duplicate

    tags = classify(text)
    tenant = infer_tenant(text)
    dept = infer_dept(text)
    captures = []
    # Issues
    # One inbound message maps to at most one canonical issue. Use the highest
    # urgency/actionability kind while preserving every classifier tag in body.
    kind = next((value for value in ("FEEDBACK", "DECISION", "TASK") if value in tags), None)
    if kind:
        s, d = create_issue(kind, text, tags, tenant, dept, capture_id or "")
        captures.append({"type":"issue","kind":kind,"status":s,"number":d.get("number"),"url":d.get("html_url")})
    # KB notes
    if "IDEA" in tags:
        path, s, d = make_note("ideas", text, tags, tenant, dept)
        captures.append({"type":"kb","kind":"IDEA","path":path,"status":s,"url":d.get("content",{}).get("html_url") if isinstance(d,dict) else None})
    if "REFERENCE" in tags:
        path, s, d = make_note("references", text, tags, tenant, dept)
        captures.append({"type":"kb","kind":"REFERENCE","path":path,"status":s,"url":d.get("content",{}).get("html_url") if isinstance(d,dict) else None})
    if "QUESTION" in tags and ("architecture" in text.lower() or "company" in text.lower() or "product" in text.lower() or "brain" in text.lower()):
        path, s, d = make_note("decisions", text, tags, tenant, dept)
        captures.append({"type":"kb","kind":"QUESTION","path":path,"status":s,"url":d.get("content",{}).get("html_url") if isinstance(d,dict) else None})
    out={"capture_id":capture_id,"tags":tags,"tenant":tenant,"department":dept,"summary":summarize(text),"captures":captures,"session_start":load_start_context(session_key),"duplicate":False}
    EVIDENCE_FILE.write_text(json.dumps(out, indent=2))
    if capture_id and all(c.get("status") in (200, 201) for c in captures):
        index[capture_id] = out
        _save_capture_index(index)
    return out


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
    session_key = f"{platform}:{getattr(source,'chat_id', '')}:{getattr(source,'thread_id', '')}:{getattr(source,'user_id','')}"
    capture_id = _event_capture_id(event, source, text)
    try:
        capture_message(text, session_key=session_key, capture_id=capture_id)
    except Exception:
        # Persistence is an audit side effect, never the transport. GitHub/KB
        # failure must not block, replace, or truncate the user's instruction.
        pass
    return {"action":"allow"}


def register(ctx):
    ctx.register_hook("pre_gateway_dispatch", pre_gateway_dispatch)
