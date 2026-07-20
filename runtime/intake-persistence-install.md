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

Telegram Bot API 10.0 requires **Bot-to-Bot Communication Mode** to be enabled in BotFather for both `@Hermes_Viewport_Bot` and `@TheViewportBot` before private bot-to-bot sends by username will work.

Runtime gates after the BotFather toggle:

- Hermes: `telegram.allow_bots: mentions`; retain mention requirements and existing chat boundaries.
- Viewport OpenClaw: `channels.telegram.allowBots: true`; add Hermes bot ID `8518793332` to the top-level, group, and topic allowlists that already contain Sam.
- Keep pair-loop protection enabled with a bounded event window and cooldown.
- Use a plain-text handoff envelope containing request ID, destination, status, timeout, and maximum interaction depth. Do not depend on rich-message parsing for control messages.

The intake hook must return `{"action": "allow"}` after persistence. GitHub/KB capture is an audit side effect and must never replace, truncate, or block the original Telegram payload. A stable capture ID makes Telegram retries idempotent, and one inbound message creates at most one canonical issue.

## Rollout and rollback

1. Back up the live Hermes plugin, Hermes config, and OpenClaw config.
2. Apply only the committed plugin artifact and scoped Telegram policy keys.
3. Reload/restart only the Hermes and Viewport Bot gateways.
4. Verify human-origin intake, both private bot-to-bot directions, one-issue idempotency, and loop suppression.
5. If any check fails, restore the backups and restart only the affected gateway.

## Security

Session DB credential-pattern scan is counts-only. Raw session content is not exported. Bot tokens remain runtime-only and must never enter Git, logs, evidence, or chat.

## Weekly digest cron

Job id: `2c9a013fb729`
Schedule: `0 9 * * 1` / next run confirmed by Hermes scheduler as Monday 09:00 Asia/Bangkok.
