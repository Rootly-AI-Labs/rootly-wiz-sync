# Wiz -> Rootly Webhook Bridge

`wiz_to_rootly.py` polls Wiz GraphQL and forwards vulnerability/threat-like items to a Rootly webhook.

Default query path follows the WIN quickstart (`issues(...)`), which requires `read:issues`.

## 1) Configure

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

## 2) Dry run

This prints the payloads instead of calling Rootly:

```bash
python3 wiz_to_rootly.py --once --dry-run
```

## 3) Live run

Single cycle:

```bash
python3 wiz_to_rootly.py --once
```

Continuous poller:

```bash
python3 wiz_to_rootly.py
```

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
