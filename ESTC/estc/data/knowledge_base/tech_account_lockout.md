# Account Lockout and Login Recovery

## When Login Is Blocked
A user may be unable to log in for several distinct reasons: too many failed
password attempts, an account placed in the Locked status, an expired or revoked
session token, or single sign-on misconfiguration on Enterprise accounts.

## Failed Password Attempts
After five consecutive failed password attempts, the user account is temporarily
locked for 15 minutes as a brute-force protection. The user can wait out the lock or
trigger a password reset email to regain access sooner.

## Locked Account Status
A company whose account_status is Locked has all API access and user sign-ins
suspended. This usually follows prolonged delinquency. Restoring access requires
clearing the billing balance, after which sign-in is re-enabled within one hour.

## Password Reset Flow
The password reset email contains a single-use link valid for 30 minutes. If the
link expires, the user must request a new one. Resetting a password invalidates all
existing sessions for that user and forces re-authentication on every device.

## Verification Before Reset
Because account lockout is a security-sensitive event, support specialists must
verify the requester's identity against the technical point of contact on the
customer record before manually unlocking an account. Lockout tickets are always
escalated for human review rather than auto-resolved.
