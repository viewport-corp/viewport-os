# Intake-to-Persistence Runtime Install

Updated: 2026-06-05T00:10:54.233966+00:00

## Runtime plugin

Installed at `/opt/data/plugins/intake_persistence/`.

Enabled in `/opt/data/config.yaml` under `plugins.enabled`.

Hook: `pre_gateway_dispatch` — runs before auth/agent dispatch for Telegram messages.

## Tool disablement

Disabled toolsets:

- homeassistant
- spotify
- yuanbao
- moa

Spotify plugin also removed from `plugins.enabled`.

## Telegram bot-to-bot handoff contract

Telegram Bot API 10.0 supports opt-in bot-to-bot communication. Confirm the owner-level mode from current evidence, but treat each send direction independently: a successful peer send proves the pair is not globally disabled, while a reverse `BOT_ACCESS_RESTRICTED` result is an asymmetric transport restriction rather than proof that both owner settings are off.

Runtime gates use values resolved from the protected runtime registry or environment; never commit production bot handles, user IDs, chat IDs, or tokens:

- Hermes: add only `${VIEWPORT_BOT_ID}` to the existing Telegram sender allowlist. Do not enable a global any-bot bypass.
- Viewport OpenClaw: set `channels.telegram.allowBots: true`; add `${HERMES_BOT_ID}` only to the selected account and explicitly approved `${GROUP_ID}` / `${TOPIC_ID}` boundaries that already contain `${BOUNDARY_USER_ID}`. Do not modify unrelated accounts, groups, or topics.
- Keep pair-loop protection enabled with a bounded event window and cooldown.
- Use a plain-text handoff envelope containing request ID, destination, status, timeout, and maximum interaction depth. Do not depend on rich-message parsing for control messages.

The intake hook must return `{"action": "allow"}` immediately after a local SQLite enqueue. GitHub/KB capture runs in a bounded background worker and is an audit side effect; it must never replace, truncate, block, or synchronously delay the original Telegram payload. A stable capture ID, atomic queue claim, and GitHub reconciliation make Telegram retries idempotent, and one inbound message creates at most one canonical issue. Durable queue files are mode `0600`; completed rows retain only the capture ID and effect metadata, not message text.

## Rollout and rollback

1. Back up the live Hermes plugin, Hermes config, and OpenClaw config.
2. Apply only the committed plugin artifact and scoped Telegram policy keys.
3. Reload/restart only the Hermes and Viewport Bot gateways.
4. Verify human-origin intake, each private bot-to-bot direction independently, the approved group mention/reply fallback, one-issue idempotency, and loop suppression. Record any asymmetric private-path restriction without attributing it to a globally disabled owner setting.
5. If any check fails, restore the backups and restart only the affected gateway.

## Security

Session DB credential-pattern scan is counts-only. Raw session content is not exported. Bot tokens remain runtime-only and must never enter Git, logs, evidence, or chat.

## Weekly digest cron

Job id: `2c9a013fb729`
Schedule: `0 9 * * 1` / next run confirmed by Hermes scheduler as Monday 09:00 Asia/Bangkok.
