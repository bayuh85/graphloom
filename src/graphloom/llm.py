"""OpenAI-compatible and Anthropic-compatible chat calls.

The default endpoint is AtmoRouter. Override the base URL and key to point
Graphloom at any compatible gateway. Only document chunks and ask-questions
are sent; the code graph itself is built offline.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "https://atmorouter.dev/v1"
DEFAULT_MODEL = "zai/glm-5.3-flash"
DEFAULT_STYLE = "openai"


class LlmError(RuntimeError):
    """The model gateway refused the call or returned something unusable."""


class LlmClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        style: str = DEFAULT_STYLE,
        transport=None,
        timeout: float = 60,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.style = style if style in {"openai", "anthropic"} else DEFAULT_STYLE
        self.transport = transport or urllib_transport
        self.timeout = timeout
        self.calls = 0

    def complete(self, system: str, user: str, max_tokens: int = 700) -> str:
        if not self.api_key:
            raise LlmError(
                "No API key. Set GRAPHLOOM_API_KEY or ATMOROUTER_API_KEY "
                "(see .env.example). The code graph does not need a key; "
                "only --semantic and ask do."
            )
        url, payload, headers = self._request(system, user, max_tokens)
        status, body = self._send(url, payload, headers)
        if status in {429, 502, 503}:
            status, body = self._send(url, payload, headers)
        if status >= 400:
            raise LlmError(f"LLM HTTP {status}: {body[:300]}")
        try:
            return extract_text(body, self.style)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LlmError(f"LLM response was not readable: {exc}") from exc

    def _send(self, url: str, payload: dict, headers: dict) -> tuple[int, str]:
        self.calls += 1
        return self.transport(url, payload, headers, self.timeout)

    def _request(self, system: str, user: str, max_tokens: int) -> tuple[str, dict, dict]:
        if self.style == "anthropic":
            url = self.base_url + "/messages"
            payload = {
                "model": self.model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
            headers = {
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "authorization": f"Bearer {self.api_key}",
            }
            return url, payload, headers
        url = self.base_url + "/chat/completions"
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.api_key}",
        }
        return url, payload, headers


def urllib_transport(url: str, payload: dict, headers: dict, timeout: float) -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return int(response.status), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return int(exc.code), raw
    except urllib.error.URLError as exc:
        raise LlmError(f"LLM connection failed: {exc.reason}") from exc


def extract_text(body: str, style: str) -> str:
    data = json.loads(body)
    if style == "anthropic":
        parts = data.get("content") or []
        texts: list[str] = []
        for part in parts:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict) and part.get("text"):
                texts.append(str(part["text"]))
        if not texts:
            raise KeyError("content")
        return "\n".join(texts).strip()
    message = data["choices"][0]["message"]
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict):
                texts.append(str(part.get("text") or part.get("content") or ""))
        return "\n".join(piece for piece in texts if piece).strip()
    if content is None and message.get("reasoning"):
        raise KeyError("content")
    return str(content or "").strip()


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines. Existing environment variables win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def client_from_env(transport=None) -> LlmClient:
    load_dotenv(Path.cwd() / ".env")
    key = os.environ.get("GRAPHLOOM_API_KEY") or os.environ.get("ATMOROUTER_API_KEY") or ""
    base = os.environ.get("GRAPHLOOM_BASE_URL") or DEFAULT_BASE_URL
    model = os.environ.get("GRAPHLOOM_MODEL") or DEFAULT_MODEL
    style = (os.environ.get("GRAPHLOOM_API_STYLE") or DEFAULT_STYLE).strip().lower()
    return LlmClient(base, key, model, style=style, transport=transport)
