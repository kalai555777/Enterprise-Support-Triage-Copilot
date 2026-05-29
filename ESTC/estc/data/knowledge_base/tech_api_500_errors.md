# API 500 Internal Server Errors

## What It Means
A 500 Internal Server Error indicates an unhandled exception on the API server
while processing a request. It is a server-side fault, not a client mistake, and is
distinct from 4xx errors which indicate a malformed or unauthorized request.

## First Response
When a customer reports a 500 error on an endpoint such as /api/orders, the support
specialist should collect the request ID from the response header, the timestamp,
and the company_id. The request ID lets engineering trace the exact failed call in
the application logs.

## Common Causes
- A downstream dependency such as the database or a third-party service timed out.
- A recent deployment introduced a regression on a specific route.
- A malformed payload triggered an unhandled code path that escaped validation.

## Remediation Steps
1. Check the status page for an active incident affecting the API.
2. Search the issue tracker for open bugs referencing the same endpoint.
3. Confirm whether the error correlates with a recent deployment window.
4. If reproducible, capture the request ID and escalate to engineering with logs.

## Customer Guidance
Advise the customer to retry the request after a short delay, since transient 500
errors often clear once the downstream dependency recovers. If the error persists
across retries, the ticket should be escalated rather than auto-resolved.
