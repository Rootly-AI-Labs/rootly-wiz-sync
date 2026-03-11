# WIN Query Inventory

This document captures the Wiz API calls, variables, and default frequency used by the Rootly pull integration.

## Integration Type

- Pull integration
- The integration stores authentication details for a Wiz service account and pulls issue data from Wiz into Rootly

## Default Runtime Frequency

- Default schedule: once per day
- Config key: `POLL_INTERVAL_SECS`
- Default value: `86400`
- Recommended production mode: `python3 wiz_to_rootly.py sync` on a scheduler
- WIN alignment: monitor no more frequently than daily

## Wiz Authentication Call

- Endpoint: `POST https://auth.app.wiz.io/oauth/token`
- Default env key: `WIZ_AUTH_URL`
- Request body:

```text
grant_type=client_credentials
audience=wiz-api
client_id=<WIZ_CLIENT_ID>
client_secret=<WIZ_CLIENT_SECRET>
```

- Frequency:
  - One token request per run before issue polling begins
  - Additional token requests only when the Wiz API reports token expiration or invalid auth during retries

## Wiz GraphQL Calls

- Endpoint: `POST https://api.us17.app.wiz.io/graphql`
- Default env key: `WIZ_API_URL`
- Required scope for the default queries: `read:issues`
- Default order:

```json
{"field":"UPDATED_AT","direction":"DESC"}
```

### Query Candidate 1 (Preferred)

```graphql
query PullIssuesV2Rich($first: Int!, $after: String, $filterBy: IssueFilters, $orderBy: IssueOrder) {
  issuesV2(first: $first, after: $after, filterBy: $filterBy, orderBy: $orderBy) {
    nodes {
      id
      sourceRules {
        __typename
        ... on Control {
          id
          name
        }
        ... on CloudEventRule {
          id
          name
          sourceType
          type
        }
        ... on CloudConfigurationRule {
          id
          name
          serviceType
          control {
            id
            name
          }
        }
      }
      createdAt
      updatedAt
      dueAt
      type
      resolvedAt
      statusChangedAt
      projects {
        id
        name
        slug
        businessUnit
        riskProfile {
          businessImpact
        }
      }
      status
      severity
      entitySnapshot {
        id
        type
        nativeType
        name
        status
        cloudPlatform
        cloudProviderURL
        providerId
        region
        subscriptionExternalId
        subscriptionName
        externalId
      }
      serviceTickets {
        externalId
        name
        url
      }
      notes {
        createdAt
        updatedAt
        text
        user {
          name
          email
        }
        serviceAccount {
          name
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
```

### Query Candidate 2

```graphql
query PullIssuesV2Compat($first: Int!, $after: String, $filterBy: IssueFilters, $orderBy: IssueOrder) {
  issuesV2(first: $first, after: $after, filterBy: $filterBy, orderBy: $orderBy) {
    nodes {
      id
      type
      severity
      createdAt
      updatedAt
      resolvedAt
      statusChangedAt
      status
      projects {
        id
        name
      }
      entitySnapshot {
        id
        name
        type
        status
        cloudPlatform
        region
      }
      sourceRules {
        __typename
        ... on Control {
          id
          name
        }
        ... on CloudEventRule {
          id
          name
        }
        ... on CloudConfigurationRule {
          id
          name
          control {
            id
            name
          }
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
```

### Query Candidate 3

```graphql
query IssuesTableRich($filterBy: IssueFilters, $first: Int, $after: String, $orderBy: IssueOrder) {
  issues(filterBy: $filterBy, first: $first, after: $after, orderBy: $orderBy) {
    nodes {
      id
      type
      title
      name
      severity
      createdAt
      updatedAt
      status
      sourceRule {
        id
        name
      }
      control {
        id
        name
      }
      project {
        id
        name
        slug
        businessUnit
        riskProfile {
          businessImpact
        }
      }
      projects {
        id
        name
        slug
      }
      entitySnapshot {
        id
        type
        name
        status
        cloudPlatform
        region
      }
      note
      serviceTickets {
        externalId
        name
        url
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
```

### Query Candidate 4

```graphql
query IssuesTableCompat($filterBy: IssueFilters, $first: Int, $after: String, $orderBy: IssueOrder) {
  issues(filterBy: $filterBy, first: $first, after: $after, orderBy: $orderBy) {
    nodes {
      id
      type
      status
      severity
      createdAt
      updatedAt
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
```

## GraphQL Variables

The integration uses the following variables:

- `first`
  - Value source: `WIZ_PAGE_SIZE`
  - Default value: `50`
- `after`
  - Value source: pagination cursor returned by Wiz
  - Default first-page value: `null`
- `filterBy`
  - Value source: `WIZ_FILTER_BY_JSON`
  - Default behavior on first run: `{"status":["OPEN","IN_PROGRESS"]}`
  - Default behavior after the first successful run: `{"statusChangedAt":{"after":"<last_successful_run_at>"}}`
  - If `WIZ_ONLY_SEVERITIES` is set, those severities are added to the GraphQL filter when `severity` is not already specified
  - If a custom `WIZ_FILTER_BY_JSON` is set, the bridge still adds `statusChangedAt.after` after the first successful run unless the custom filter already includes `statusChangedAt`
- `orderBy`
  - Value source: `WIZ_ORDER_BY_JSON`
  - Default value:

```json
{"field":"UPDATED_AT","direction":"DESC"}
```

## Delta Cursor State

- Local state file: `.wiz_rootly_seen_ids.json`
- Per-issue state tracks dedupe and lifecycle details
- Metadata tracks `last_successful_run_at`, which is used as the next `statusChangedAt.after` cursor
- The cursor advances only after a successful run, so partial delivery failures do not skip updates

## Worst-Case Call Pattern Per Scheduled Run

- 1 token request before polling starts
- Up to `WIZ_MAX_PAGES` GraphQL requests per query candidate
- Default `WIZ_MAX_PAGES`: `5`
- Default query candidates: `4`
- Worst-case default GraphQL request ceiling if every candidate fails late: `20`
