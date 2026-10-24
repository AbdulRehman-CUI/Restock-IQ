"""HTTP-only integration. Mutations are never retried automatically."""
import os
import httpx
from urllib.parse import urlsplit


class APIError(Exception):
    def __init__(self, message, payload=None, status=None):
        super().__init__(message)
        self.payload, self.status = payload or {}, status


class APIClient:
    def __init__(self, base_url=None, transport=None):
        self.base_url = (base_url or os.getenv("RESTOCKIQ_API_URL", "http://127.0.0.1:8000")).rstrip("/")
        try:
            parsed = urlsplit(self.base_url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.port == 0:
                raise ValueError("Invalid base URL")
        except ValueError as exc:
            raise APIError("Enter a valid API address, for example http://127.0.0.1:8000") from exc
        self.transport = transport

    def request(self, method, path, **kwargs):
        try:
            with httpx.Client(base_url=self.base_url + "/api/v1/", transport=self.transport,
                              timeout=httpx.Timeout(120, connect=4)) as client:
                response = client.request(method, path.lstrip("/"), **kwargs)
        except httpx.RequestError as exc:
            raise APIError(f"Cannot reach {self.base_url}. Start the API in a separate terminal: "
                           " .\\.venv\\Scripts\\python.exe -m app.cli serve --port 8000") from exc
        if not response.is_success:
            try:
                payload = response.json()
            except ValueError:
                payload = {"message": response.text[:300]}
            raise APIError(payload.get("message", payload.get("code", "API request failed")), payload, response.status_code)
        return response

    def get(self, path, **params):
        return self.request("GET", path, **({"params": params} if params else {})).json()

    def post(self, path, body=None, **kwargs):
        return self.request("POST", path, json=body, **kwargs).json()

    def download(self, path):
        return self.request("GET", path).content
