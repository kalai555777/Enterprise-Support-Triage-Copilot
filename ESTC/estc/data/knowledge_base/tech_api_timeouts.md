# API Timeouts and Latency

## Symptom
A timeout occurs when a request does not complete within the client's configured
deadline. Customers see a 504 Gateway Timeout or a client-side connection-reset
error. Timeouts differ from 500 errors: the server may still be processing the
request when the client gives up.

## Typical Triggers
- Large batch requests that exceed the recommended page size.
- Slow database queries during peak traffic windows.
- A retry storm where the client retries aggressively and amplifies load.

## Recommended Client Settings
Clients should set a connection timeout of 5 seconds and a read timeout of 30
seconds, and should implement exponential backoff with jitter between retries.
Requests that page through large result sets should request no more than 100 items
per page.

## Diagnosis
Correlate the customer's timestamps with the latency dashboard. If p99 latency is
elevated platform-wide, this is likely an incident; if it is isolated to one
company_id, inspect that customer's query patterns and payload sizes.

## Escalation
Sustained elevated latency that breaches the Enterprise 99.9% uptime SLA is logged
as a service-impacting incident and may entitle the customer to a service credit.
