# Webhook Delivery Failures

## How Webhooks Work
ESTC delivers event notifications to a customer-configured HTTPS endpoint. A
delivery is considered successful when the endpoint returns a 2xx response within
10 seconds. Any other response, or a timeout, is treated as a failed delivery.

## Retry Behavior
Failed webhook deliveries are retried with exponential backoff for up to 24 hours.
After the final retry fails, the event is marked undeliverable and surfaced in the
webhook dashboard so the customer can replay it manually.

## Common Causes of Failure
- The customer endpoint returned a 500 error or timed out.
- The endpoint's TLS certificate expired or failed validation.
- A firewall rule blocked the platform's outbound delivery IP range.
- The signing secret was rotated on one side but not the other, failing verification.

## Signature Verification
Every webhook includes an HMAC signature header. The customer endpoint must verify
the signature using the shared signing secret before trusting the payload. A
signature mismatch should return 401 so the platform records it as a failure.

## Diagnosis Checklist
Confirm the endpoint is reachable, the certificate is valid, the signing secret
matches, and the endpoint responds within the 10-second window. The webhook
dashboard shows the response code recorded for each delivery attempt.
