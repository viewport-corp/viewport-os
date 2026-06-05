# Viewport OS HANDOFF

Last updated: 2026-06-05T00:10:54.233966+00:00

## System state

- `/migration/audit` completed sections 0–12.
- Intake-to-persistence pipeline v1 installed as Hermes plugin `intake_persistence`.
- Session DB has credential-pattern hits; raw chat/session content must not be exported.
- GitHub and KB captures are public-safe paraphrases only.
- Disabled toolsets: homeassistant, spotify, yuanbao, moa.

## Active tenants

- Viewport
- MLG / MLH
- BCCL
- Unknown/unclassified intake is allowed but tagged `tenant-unknown`.

## Active tasks

- Monitor live intake hook after gateway restart.
- Promote repeated KB topics into canonical decisions/tasks.
- Replace PAT auth with GitHub App auth.
- Complete native Telegram export only with safe redaction/rotation plan.

## Last session summary

Sam ordered anti-amnesia intake-to-persistence. Step 0 security issue created (#192). `viewport-kb` and `viewport-os` repos were created. The hook creates issues for TASK/DECISION/FEEDBACK and KB notes for IDEA/REFERENCE/architecture QUESTION. Acceptance tests created issue #194 and KB notes including `ideas/2026-06-05-agent-tokens.md`. Weekly digest cron job `2c9a013fb729` is scheduled.

## End-of-session rule

Update this HANDOFF.md at the end of every significant session with current state, active tenants, active tasks, and blockers.
