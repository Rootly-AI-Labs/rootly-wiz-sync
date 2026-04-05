"""Constants and default GraphQL queries for Wiz integration."""

DEFAULT_AUTH_URL = "https://auth.app.wiz.io/oauth/token"
DEFAULT_API_URL = "https://api.us17.app.wiz.io/graphql"
DEFAULT_WIZ_MAX_RPS = 2
DEFAULT_POLL_INTERVAL_SECS = 86400
DEFAULT_ACTIVE_STATUSES = ("OPEN", "IN_PROGRESS")
DEFAULT_RESOLVED_STATUSES = {"resolved", "closed", "rejected"}

DEFAULT_QUERY_ISSUES = """
query IssuesTableRich($filterBy: IssueFilters, $first: Int, $after: String) {
  issues(filterBy: $filterBy, first: $first, after: $after) {
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
""".strip()

DEFAULT_QUERY_ISSUES_COMPAT = """
query IssuesTableCompat($filterBy: IssueFilters, $first: Int, $after: String) {
  issues(filterBy: $filterBy, first: $first, after: $after) {
    nodes {
      id
      type
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

DEFAULT_QUERY_ISSUES_V2 = """
query PullIssuesV2Rich($first: Int!, $after: String, $filterBy: IssueFilters) {
  issuesV2(first: $first, after: $after, filterBy: $filterBy) {
    nodes {
      id
      type
      severity
      createdAt
      updatedAt
      resolvedAt
      statusChangedAt
      status
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
      projects {
        id
        name
        slug
      }
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
""".strip()

DEFAULT_QUERY_ISSUES_V2_COMPAT = """
query PullIssuesV2Compat($first: Int!, $after: String, $filterBy: IssueFilters) {
  issuesV2(first: $first, after: $after, filterBy: $filterBy) {
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
        type
        name
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
""".strip()
