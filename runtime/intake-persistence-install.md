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

## Security

Session DB credential-pattern scan is counts-only. Raw session content is not exported.

## Weekly digest cron

Job id: `2c9a013fb729`
Schedule: `0 9 * * 1` / next run confirmed by Hermes scheduler as Monday 09:00 Asia/Bangkok.
