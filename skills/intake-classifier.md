---
name: intake-classifier
description: Classify Sam's incoming Telegram messages and persist public-safe paraphrases to GitHub/Viewport KB.
version: 1.0.0
author: Hermes
license: MIT
metadata:
  hermes:
    tags: [intake, anti-amnesia, github, kb, telegram, viewport]
---

# Intake Classifier

Use for every incoming Telegram message from Sam before responding.

## Non-negotiable security rule

- Never commit or paste raw Telegram/session content into GitHub or KB.
- Session logs contain credential-like patterns. Treat `/opt/data/state.db` as sensitive.
- GitHub and KB are public-safe stores only.
- Store paraphrases, summaries, links, decisions, and tasks. Do not store secrets.

## Tags

Classify each message with one or more:

- `TASK`: Sam wants something built, fixed, audited, checked, scheduled, changed, deployed, or followed up.
- `IDEA`: Sam proposes a concept, business model, product direction, or possible future strategy.
- `DECISION`: Sam makes a durable choice, rule, architecture decision, boundary, approval, or rejection.
- `REFERENCE`: Sam shares a link, image, video, document, screenshot, or external resource.
- `QUESTION`: Sam asks something that may become architecture/product/ops knowledge.
- `FEEDBACK`: Sam corrects Hermes, gives process feedback, rejects an approach, or says what to do differently.
- `BLOCKER`: Sam states something is stuck, access is missing, a system is broken, or progress is blocked.

Default to capture if unsure. Never ask Sam to classify.

## Persistence rules

### TASK

Create GitHub issue immediately.

Title:

```text
[CHAT→TASK] {one-line paraphrased summary}
```

Labels:

```text
chat-capture, needs-triage, anti-amnesia, tenant-{tenant}, dept-{department}
```

Body fields:

```markdown
## Source
Telegram DM with Sam, timestamp, message id if available.

## What Sam said — paraphrased
Public-safe paraphrase only. No raw content.

## Task
Specific task/action requested.

## Context
Known project/tenant/runtime context.

## Tenant
Viewport / MLG / MLH / BCCL / unknown.

## Department
ops / engineering / product / security / finance / legal / kb / unknown.

## Links
URLs only if user shared a public URL or GitHub URL.
```

### DECISION

Create GitHub issue immediately. Never close automatically.

Title:

```text
[CHAT→DECISION] {one-line paraphrased decision}
```

Labels:

```text
decision-log, chat-capture, anti-amnesia, needs-triage
```

### FEEDBACK

Create GitHub issue immediately.

Title:

```text
[CHAT→CORRECTION] {one-line paraphrased correction}
```

Labels:

```text
correction, high-priority, chat-capture, anti-amnesia, needs-triage
```

Link original issue when available.

### IDEA

Create Obsidian-compatible markdown note in `viewport-corp/viewport-kb`:

```text
/ideas/{date}-{slug}.md
```

Fields:

```markdown
---
title: ...
date: ...
source: telegram
status: raw-idea
tags: [...]
tenant: ...
---

# {title}

## What was said — paraphrased

## Why it matters

## Possible next step

## Related links
```

### REFERENCE

Create note:

```text
/references/{date}-{slug}.md
```

Fetch/analyze the URL/image/video. Do not only store URL.

Fields:

```markdown
---
title: ...
date: ...
source: telegram
type: link/image/video/document
url: ...
status: captured-reference
---

# {title}

## Summary

## Why Sam shared this

## Relevant to

## Action potential
```

### QUESTION

If answer is durable architecture/product/ops knowledge, create:

```text
/decisions/{date}-{slug}.md
```

Log paraphrased question + answer. Link issue if it changes anything.

## Reply language rule

Never say: `I'll remember that`.

Say one of:

```text
Captured to GitHub issue #N
Captured to KB: {path}
Found in KB: {link}
Issue #N exists
```

## Session-start anti-amnesia rule

At the start of a new session, load:

1. Last 5 GitHub issues labeled `chat-capture`.
2. `viewport-corp/viewport-kb/INDEX.md`.
3. `viewport-corp/viewport-os/HANDOFF.md`.

Tell Sam:

```text
Loaded: N recent issues, KB has N notes, last session: {date}
```

## Repeated topic rule

Before creating a new issue/note, search existing GitHub issues and KB paths. If similar topic exists, surface it:

```text
Sam, we discussed this — issue #N / KB: {link}. Status: {status}. Update or new?
```

Only ask this when it changes whether to update or duplicate. Otherwise create/update immediately.
