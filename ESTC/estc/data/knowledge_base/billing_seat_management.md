# Seat Management and Licensing

## Seat Model
Paid tiers license a fixed number of seats. A seat is consumed by each active user
who can sign in. Growth includes up to twenty-five seats; Enterprise seat counts are
negotiated in the contract. Seat usage is enforced at login time.

## Adding Seats
Adding seats takes effect immediately and is prorated for the remainder of the
billing cycle. The additional cost appears as a line item on the next invoice. There
is no limit on seat additions for Enterprise customers within their contract terms.

## Removing Seats
Removing seats takes effect at the start of the next billing cycle so the customer
keeps entitlements they have already paid for. A prorated credit is not issued for
mid-cycle seat removals; the reduction simply lowers the next invoice.

## Seat Limit Reached
When all licensed seats are occupied, new sign-ins are blocked with a clear message
asking an administrator to free a seat or purchase more. This is a licensing limit,
not an authentication error, and is resolved through the billing settings page.

## Auditing Seats
Administrators can review active seats and last-login timestamps to reclaim seats
from inactive users. Support specialists verify seat counts against the
subscription_tier entitlement when a customer reports unexpected login blocks.
