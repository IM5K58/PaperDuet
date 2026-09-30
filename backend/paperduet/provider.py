"""API transports. Credentials are obtained exclusively from Windows keyring."""
import base64
import json
import sys
from pathlib import Path
from typing import Protocol

import httpx


class ProviderError(Exception):
    """A deliberately public, fixed error code. Never wrap a transport exception."""
    def __init__(self,code,usage=None):
        super().__init__(code)
        self.usage=usage


class Vault(Protocol):
    def get(self) -> str | None: ...
    def set(self, value: str): ...
    def delete(self): ...


class WindowsVault:
    def __init__(self,service="PaperDuet",provider="anthropic"):
        self.service=service
        self.provider=provider
    def _backend(self):
        if sys.platform != "win32":
            raise ProviderError("KEYRING_UNAVAILABLE")
        # Deliberately bypass backend discovery: never fall back to a plaintext
        # file backend or an installed third-party keyring implementation.
        from keyring.backends.Windows import WinVaultKeyring
        return WinVaultKeyring()

    def get(self):
        try:
            return self._backend().get_password(self.service, self.provider)
        except Exception:
            raise ProviderError("KEYRING_UNAVAILABLE") from None

    def set(self, value: str):
        try:
            self._backend().set_password(self.service, self.provider, value)
        except Exception:
            raise ProviderError("KEYRING_UNAVAILABLE") from None

    def delete(self):
        try:
            if self.get():
                self._backend().delete_password(self.service, self.provider)
        except Exception:
            raise ProviderError("KEYRING_UNAVAILABLE") from None


class AnthropicProvider:
    id = "anthropic"
    mode = "api_key"
    supports_images = True

    def __init__(self, vault: Vault, transport=None):
        self.vault = vault
        self.transport = transport

    def connected(self):
        return bool(self.vault.get())

    async def _request(self, method: str, path: str, body=None):
        key = self.vault.get()
        if not key:
            raise ProviderError("AI_CONNECTION_REQUIRED")
        try:
            async with httpx.AsyncClient(base_url="https://api.anthropic.com", timeout=httpx.Timeout(180, connect=15),
                                         follow_redirects=False, trust_env=False, transport=self.transport) as client:
                response = await client.request(method, path, json=body,
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
            if response.status_code in (401, 403):
                raise ProviderError("AI_AUTH_FAILED")
            if response.status_code == 429:
                raise ProviderError("AI_RATE_LIMIT")
            if response.status_code >= 400:
                raise ProviderError("AI_REQUEST_FAILED")
            # Response bodies and transport exception text are never logged or
            # returned as error details. Redact even a misbehaving mock/upstream.
            return json.loads(response.text.replace(key, "[REDACTED]"))
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("AI_NETWORK_ERROR") from None

    async def list_models(self):
        result = []
        after = ""
        for _ in range(10):
            data = await self._request("GET", "/v1/models?limit=100" + ("&after_id=" + after if after else ""))
            result.extend({"id": m["id"], "name": m.get("display_name", m["id"])} for m in data.get("data", []))
            if not data.get("has_more"):
                break
            after = data.get("last_id", "")
            if not after:
                break
        return result

    async def health_check(self):
        return {"status": "ok", "models": await self.list_models()}

    async def json(self, stage: str, model: str, payload, image: Path | None = None):
        prompt = (Path(__file__).with_name("prompts") / f"{stage}.md").read_text(encoding="utf-8")
        content = []
        if image:
            raw = image.read_bytes()
            if len(raw) > 5_000_000:
                raise ProviderError("AI_IMAGE_TOO_LARGE")
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(raw).decode("ascii")}})
        content.append({"type": "text", "text": json.dumps(payload, ensure_ascii=False)})
        total = {"tokens_in": 0, "tokens_out": 0, "cache_read": 0, "cache_write": 0, "requests": 0}
        for attempt in range(2):
            total["requests"] += 1
            data = await self._request("POST", "/v1/messages", {"model": model, "max_tokens": 16000,
                "system": prompt, "messages": [{"role": "user", "content": content}]})
            usage = data.get("usage", {})
            read, write = usage.get("cache_read_input_tokens", 0), usage.get("cache_creation_input_tokens", 0)
            total["tokens_in"] += usage.get("input_tokens", 0) + read + write
            total["cache_read"] += read
            total["cache_write"] += write
            total["tokens_out"] += data.get("usage", {}).get("output_tokens", 0)
            text = "".join(c.get("text", "") for c in data.get("content", []) if c.get("type") == "text").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                if data.get("stop_reason") == "max_tokens":
                    raise ValueError()
                return json.loads(text), total
            except (ValueError, TypeError):
                if attempt:
                    raise ProviderError("AI_INVALID_JSON",usage=total) from None
        raise ProviderError("AI_INVALID_JSON")
