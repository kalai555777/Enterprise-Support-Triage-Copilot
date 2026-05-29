# Incidents and the Status Page

## Status Page
The public status page reports the live health of each API surface and any ongoing
incidents. Support specialists check it first when a customer reports widespread
errors, because a platform incident explains errors across many company accounts at
once.

## Incident Severity
Incidents are graded from minor degradation to full outage. A full outage of an API
surface that breaches the Enterprise 99.9% uptime SLA is recorded as
service-impacting and may entitle affected customers to a service-level credit.

## During an Incident
While an incident is active, support advises customers to retry with backoff and
avoid retry storms that worsen load. Tickets that match an active incident are
linked to it rather than individually escalated, so the fix is tracked in one place.

## Postmortem
After resolution, a postmortem documents the root cause, the timeline, and the
remediation. Enterprise customers affected by an SLA breach receive a summary and,
where applicable, an automatically applied account credit on their next invoice.

## Subscribing
Customers can subscribe to status-page updates by email or webhook so their own
on-call teams are notified the moment an incident affecting their region opens.
