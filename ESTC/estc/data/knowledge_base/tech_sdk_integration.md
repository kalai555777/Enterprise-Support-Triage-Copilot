# SDK Integration and Setup

## Official SDKs
ESTC publishes official SDKs for Python, JavaScript, and Go. Each SDK wraps
authentication, retries, and pagination so integrators do not reimplement them. The
SDK reads the API token from an environment variable by default.

## Initialization
On startup the SDK validates the token format and the configured base URL. A common
first-run error is a 401 caused by an unset or truncated token environment variable.
The SDK raises a clear configuration error rather than sending an unauthenticated
request.

## Built-in Retries
The SDK retries idempotent requests on 429 and 5xx responses using exponential
backoff with jitter, honoring the Retry-After header. Non-idempotent writes are not
retried automatically to avoid duplicate side effects.

## Versioning
SDK releases follow semantic versioning. Breaking API changes are gated behind a
version header so existing integrations keep working. Integrators should pin the SDK
version and review the changelog before upgrading across major versions.

## Troubleshooting
Enable the SDK debug log to see the request ID for each call. When reporting a bug,
include the SDK version, the request ID, and the full error class so engineering can
trace the failing call quickly.
