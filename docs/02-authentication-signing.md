# Authentication and request signing

Private REST calls require an API key, timestamp, and HMAC-SHA256 signature.

## Required headers

```text
api-key: <key>
timestamp: <unix-seconds>
signature: <lowercase-hex-hmac>
User-Agent: <client identifier>
Content-Type: application/json
```

Delta documents `User-Agent` as required to avoid some 4xx responses. Trading keys require IP whitelisting. Whitelist both actual IPv4/IPv6 egress addresses if applicable and use static cloud egress.

## Canonical prehash

```text
METHOD + TIMESTAMP + REQUEST_PATH + QUERY_STRING + BODY
```

Rules:

- `METHOD` is uppercase.
- `REQUEST_PATH` includes `/v2`, for example `/v2/orders`.
- `QUERY_STRING` includes the leading `?` when non-empty.
- Query order and encoding must exactly match the transmitted URL.
- `BODY` must be the exact bytes transmitted. For GET it is normally empty.
- Use the same timestamp in the header and prehash.
- The official docs state that signatures older than five seconds are rejected.

## Python implementation

```python
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

def signed_parts(method, path, secret, query=None, body=None):
    timestamp = str(int(time.time()))
    query_string = f"?{urlencode(query)}" if query else ""
    payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
    prehash = method.upper() + timestamp + path + query_string + payload
    signature = hmac.new(
        secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return query_string, payload, {
        "api-key": "<API_KEY>",
        "timestamp": timestamp,
        "signature": signature,
        "User-Agent": "my-delta-client/1.0",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
```

Serialize once, sign that exact serialization, and send those same bytes. Re-serializing a dict after signing is a common signature-mismatch cause.

## WebSocket authentication

Use the current `key-auth` method before subscribing to private channels. Its signature payload is documented separately by Delta; do not reuse the REST prehash blindly. The older `auth` method was scheduled to stop working after 2025-12-31 and should be treated as removed.

## Failure diagnosis

| Error | Verify |
|---|---|
| `SignatureExpired` | NTP clock sync, request latency, timestamp in seconds |
| signature mismatch / `invalid_signature` | method case, `/v2` path, exact query order, exact body bytes |
| invalid API key | correct environment, no whitespace, active key |
| IP not whitelisted | actual public egress IP and IPv4/IPv6 selection |
| forbidden / HTTP 403 | `User-Agent`, CDN policy, blocked hosting IP |
| unauthorized access | trading permission and endpoint scope |

Never log the secret, signature preimage containing sensitive payloads, or full auth headers.

