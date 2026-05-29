# Authentication and Token Errors

## 401 Unauthorized
A 401 response means the API key or bearer token was missing, malformed, or
expired. Unlike a 500 error, a 401 is a client-side authentication failure and is
resolved by presenting a valid credential rather than by retrying the same request.

## 403 Forbidden
A 403 means the token is valid but lacks permission for the requested resource.
This commonly happens when a key scoped to read-only is used for a write endpoint,
or when a seat's role does not grant access to an administrative route.

## Token Rotation
API tokens expire on a rolling 90-day schedule. The platform emails the technical
point of contact 14 days before expiry. Rotating a token invalidates the previous
token after a 24-hour grace period to allow a smooth cutover.

## Diagnosing Auth Failures
1. Confirm the token has not expired or been revoked.
2. Confirm the token's scope matches the endpoint being called.
3. Confirm the Authorization header is formatted as "Bearer <token>".
4. Check whether the company account is Locked, which revokes all tokens.

## Security Note
Never ask a customer to share their full token over a support channel. Tokens
should be rotated immediately if they are suspected to have leaked.
