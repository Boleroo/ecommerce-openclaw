from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class BackendResponse:
    status_code: int
    body: object
    is_json: bool = True


class BackendTransportError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class HTTPBackendClient:
    def __init__(self, base_url="http://127.0.0.1:8080", timeout=5.0, transport=None):
        parsed = urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError("Backend URL must be an HTTP(S) origin without credentials, path, query or fragment")
        if not 0 < timeout <= 60:
            raise ValueError("Backend timeout must be between 0 and 60 seconds")
        self.client = httpx.Client(
            base_url=base_url, timeout=timeout, transport=transport,
            follow_redirects=False, trust_env=False,
        )

    def request(self, method, endpoint, *, token, trace_id, params=None, json_body=None):
        if not endpoint.startswith("/v1/") or ".." in endpoint or "?" in endpoint or "#" in endpoint:
            raise ValueError("Backend endpoint must be a registered v1 path")
        try:
            response = self.client.request(
                method, endpoint, params=params, json=json_body,
                headers={"Authorization": f"Bearer {token}", "X-Request-ID": trace_id},
            )
        except httpx.TimeoutException:
            raise BackendTransportError("UPSTREAM_TIMEOUT") from None
        except httpx.TransportError:
            raise BackendTransportError("UPSTREAM_UNAVAILABLE") from None
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        try:
            body = response.json()
        except ValueError:
            return BackendResponse(response.status_code, None, False)
        return BackendResponse(response.status_code, body, content_type == "application/json" or content_type.endswith("+json"))

    def close(self):
        self.client.close()
