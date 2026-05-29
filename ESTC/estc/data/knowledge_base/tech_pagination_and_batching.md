# Pagination and Batch Requests

## Why Pagination Exists
List endpoints return results in pages to keep responses fast and bounded. A single
unpaginated request over a large dataset is the most common cause of timeouts and
elevated latency, so the API caps the maximum page size.

## Page Size Limits
The maximum page size is 100 items. Requesting more than 100 is rejected with a 400
error explaining the limit. Clients should request 50 to 100 items per page and
follow the next-page cursor returned in the response body.

## Cursor-Based Paging
The API uses opaque cursors rather than numeric offsets. Each response includes a
next_cursor value; the client passes it on the following request. Cursors are stable
even if rows are inserted between requests, avoiding skipped or duplicated items.

## Batch Writes
Batch write endpoints accept up to 500 records per call. Larger batches should be
split client-side. Each record in a batch is validated independently, and the
response reports per-record success or failure so partial batches are recoverable.

## Performance Tips
Combine pagination with a reasonable read timeout of 30 seconds and exponential
backoff. Avoid issuing many pages in parallel, which can trigger 429 rate-limit
errors and degrade throughput for the whole account.
