# Wiz -> Rootly Webhook Bridge

`wiz_to_rootly.py` polls Wiz GraphQL and forwards vulnerability/threat-like items to a Rootly webhook.

Default query path follows the WIN quickstart (`issues(...)`), which requires `read:issues`.

## 1) Create Rootly Alert Source (Required)

In Rootly, create a **Generic Webhook Alert Source** first. This bridge sends events to that incoming endpoint.

Navigation path in Rootly:

- `Alerts` (left panel) -> `Sources` -> `+ New Source` -> `Generic Webhook`

Use one of these forms from Rootly setup:

- URL with secret query param:
  - `https://webhooks.rootly.com/webhooks/incoming/generic_webhooks?secret=...`
- URL + Authorization header:
  - URL: `https://webhooks.rootly.com/webhooks/incoming/generic_webhooks`
  - Header: `Authorization: Bearer <secret>`

Set the corresponding values in `.env.wiz-rootly`:

- `ROOTLY_WEBHOOK_URL`
- Optional: `ROOTLY_WEBHOOK_AUTH_HEADER` + `ROOTLY_WEBHOOK_AUTH_VALUE`

## 2) Map Rootly Fields (Required)

After creating the Generic Webhook source, configure field mappings so alerts show real titles (not generic `Alert`).

In Rootly, open the source and set:

- `Fields -> Title`: `{{ alert.data.title }}`
- `Fields -> Description`: `{{ alert.data.description }}`
- `Urgency`: `{{ alert.data.urgency }}`

Then save the source.

Current bridge field behavior:

- `title` is built from Wiz control/title and now appends resource + short issue ID.
  - Example: `Secrets not stored in a secret container (Private Key) [feea4f03]`
- `description` is `Wiz reported a <TYPE> item with <SEVERITY> severity.`
- `urgency` maps from Wiz severity (for example, `HIGH` -> `High`).

Important:

- These mappings apply to newly created alerts after saving.
- Older alerts may still display the old title.

## 3) Configure

Copy the example env file and fill in values:

```bash
cp .env.wiz-rootly.example .env.wiz-rootly
```

Then export it:

```bash
set -a
source .env.wiz-rootly
set +a
```

## 4) Dry run

This prints the payloads instead of calling Rootly:

```bash
python3 wiz_to_rootly.py --once --dry-run
```

## 5) Live run

Single cycle:

```bash
python3 wiz_to_rootly.py --once
```

Continuous poller:

```bash
python3 wiz_to_rootly.py
```

## 6) Production Scheduling (Recommended)

For most customers, the most efficient setup is a scheduled `--once` run (not a long-running poller).

- Recommended cadence: once per day
- Keep `WIZ_STATE_FILE` persistent so dedupe works across runs
- Use continuous mode only if you need near-real-time forwarding

Example cron (daily at 9:00 AM):

```bash
0 9 * * * cd /path/to/Rootly-Wiz-Bridge && set -a && source .env.wiz-rootly && set +a && python3 wiz_to_rootly.py --once >> wiz_rootly.log 2>&1
```

## 7) Route Alerts in Rootly

If you use in-app routing, add a rule in `Alerts -> Routes` to page the right destination.

Example rule:

- Condition: `Urgency is High` (Rootly requires at least one condition)
- Route to: your user, team, or escalation policy

## Notes

- Dedupe state is stored in `.wiz_rootly_seen_ids.json`.
- Default `WIZ_FILTER_BY_JSON` behavior is `{"status":["OPEN"]}`.
- Default poll interval is daily (`POLL_INTERVAL_SECS=86400`) to align with WIN API pull guidance.
- Optional WIN-style GraphQL variables:
  - `WIZ_FILTER_BY_JSON='{"status":["OPEN"]}'`
  - `WIZ_ORDER_BY_JSON='{"field":"UPDATED_AT","direction":"DESC"}'`
- If your Wiz tenant uses a different query shape, set either:
  - `WIZ_GRAPHQL_QUERY`
  - `WIZ_GRAPHQL_QUERY_FILE`
- Optional severity filtering: `WIZ_ONLY_SEVERITIES=critical,high`.
- Wiz rate-limit controls:
  - `WIZ_MAX_RPS=3`
  - `WIZ_MAX_RETRIES=5`
  - `WIZ_TOKEN_REFRESH_RETRIES=5`
  - `WIZ_RETRY_BASE_SECS=1.0`
  - `WIZ_RETRY_MAX_SECS=30.0`
  - Script retries throttles (`429`) and transient `5xx` errors with backoff.
  - Script refreshes token and retries when token expiration/invalid auth is detected.
  - Script surfaces GraphQL `UNAUTHORIZED` errors with scope guidance.
  - Script handles HTTP 200 + GraphQL partial-data responses by continuing with available nodes.
