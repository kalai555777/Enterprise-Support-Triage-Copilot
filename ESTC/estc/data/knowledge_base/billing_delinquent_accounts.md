# Delinquent Accounts and Dunning

## What Makes an Account Delinquent
An account is marked Delinquent when an invoice remains unpaid 15 days past its
due date. The account_status field on the customer record transitions from Active
to Delinquent automatically during the nightly billing reconciliation job.

## Consequences
While an account is Delinquent, API rate limits are reduced and new seat
provisioning is blocked. If the balance remains unpaid for 30 additional days, the
account may transition to Locked, which suspends API access entirely.

## Dunning Communications
The billing system sends a dunning email sequence: a reminder on the due date, a
second notice at 7 days overdue, and a final notice at 14 days overdue. Each notice
includes the outstanding invoice ID and a payment link.

## Restoring an Account
To restore a Delinquent account to Active, the customer must pay the full
outstanding balance. Once payment clears, the reconciliation job restores the prior
subscription_tier entitlements within one hour. Support can expedite restoration by
manually triggering reconciliation after confirming payment.

## Escalation
Repeated delinquency on Enterprise accounts is escalated to the dedicated account
manager, who may renegotiate payment terms before any service suspension.
