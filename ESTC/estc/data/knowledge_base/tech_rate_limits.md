# Rate Limits and 429 Errors

## Symptom
A 429 Too Many Requests response means the client has exceeded the API rate limit
for its subscription tier. The response includes a Retry-After header indicating how
many seconds to wait before retrying.

## Limits by Tier
Rate limits scale with the subscription_tier. Free accounts have the lowest
sustained request rate, Growth accounts have a higher rate with short bursts
allowed, and Enterprise accounts have negotiated limits sized to their contract.

## Handling 429s
Clients should honor the Retry-After header and implement exponential backoff. A
client that ignores 429s and keeps retrying creates a retry storm that can degrade
latency for the whole account and may itself trigger more 429s.

## Delinquent Throttling
When an account is Delinquent, its effective rate limit is reduced below the normal
tier limit until the outstanding balance is cleared. Customers may report sudden
429s that are actually caused by a billing status change rather than traffic growth.

## Raising a Limit
Enterprise customers can request a higher rate limit through their account manager.
The change is applied after a capacity review and is reflected in the customer's
plan entitlements.
