"""Constants and default GraphQL queries for Wiz integration."""

DEFAULT_AUTH_URL = "https://auth.app.wiz.io/oauth/token"
DEFAULT_API_URL = "https://api.us17.app.wiz.io/graphql"
DEFAULT_WIZ_MAX_RPS = 3
DEFAULT_POLL_INTERVAL_SECS = 86400

DEFAULT_QUERY_ISSUES_V2 = """
query PullIssuesV2($first: Int!, $after: String, $filterBy: IssueFilters, $orderBy: IssueOrder) {
  issuesV2(first: $first, after: $after, filterBy: $filterBy, orderBy: $orderBy) {
    nodes {
      id
      type
      severity
      createdAt
      updatedAt
      status
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
""".strip()

DEFAULT_QUERY_ISSUES = """
query IssuesTable($filterBy: IssueFilters, $first: Int, $after: String, $orderBy: IssueOrder) {
  issues(filterBy: $filterBy, first: $first, after: $after, orderBy: $orderBy) {
    nodes {
      id
      control {
        id
        name
      }
      createdAt
      updatedAt
      dueAt
      project {
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
""".strip()

