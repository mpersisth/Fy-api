"""fy-conformance — protocol-conformance test harness for Fy-api channels.

Asks one question only: when a client sends a malformed or boundary
parameter, does the gateway produce a *correct* error response?

Specifically:
  * status code in the right class (4xx for client errors, not 5xx)
  * error message is informative for the client (mentions the bad field)
  * error message does not leak internal Go struct paths
  * known-valid values pass through

The corpus is a JSONL file. Each row is a single conformance assertion.
"""

__version__ = "0.1.0"
