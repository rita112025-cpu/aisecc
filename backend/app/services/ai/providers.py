"""Pluggable AI provider adapters. Add a provider by implementing `AIProvider` and registering it in
`build_provider`. Without a configured provider every AI endpoint reports "not configured" and the
rest of the system keeps working."""

from __future__ import annotations

import os
from typing import Any, Protocol

import httpx

from app.services.settings_store import AppSettings, is_local_url


class AIError(RuntimeError):
    pass


class AINotConfigured(AIError):
    pass


class AIProvider(Protocol):
    name: str
    model: str
    # True when prompts leave this machine; such providers require explicit opt-in.
    external: bool

    def generate(self, prompt: str) -> str: ...

    def status(self) -> dict[str, Any]: ...


class OllamaProvider:
    name = "ollama"
    external = False

    def __init__(self, base_url: str, model: str, timeout: float, transport: httpx.BaseTransport | None = None):
        if not is_local_url(base_url):
            raise AINotConfigured("Ollama 位址必須是本機")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=self.timeout, transport=self._transport,
                            trust_env=False)

    def generate(self, prompt: str) -> str:
        body = {"model": self.model, "prompt": prompt, "stream": False, "format": "json",
                "options": {"temperature": 0.1}}
        try:
            with self._client() as c:
                r = c.post("/api/generate", json=body)
        except httpx.TimeoutException as exc:
            raise AIError(f"Ollama 回應逾時（{self.timeout:.0f}s）") from exc
        except httpx.HTTPError as exc:
            raise AIError(f"無法連線到 Ollama：{exc}") from exc
        if r.status_code != 200:
            raise AIError(f"Ollama 錯誤 {r.status_code}：{r.text[:300]}")
        try:
            return str(r.json()["response"])
        except (ValueError, KeyError) as exc:
            raise AIError("Ollama 回應格式不正確") from exc

    def status(self) -> dict[str, Any]:
        try:
            with self._client() as c:
                r = c.get("/api/tags", timeout=5)
            models = [m.get("name") for m in r.json().get("models", [])] if r.status_code == 200 else []
        except (httpx.HTTPError, ValueError) as exc:
            return {"provider": self.name, "reachable": False, "error": str(exc), "model": self.model}
        return {"provider": self.name, "reachable": True, "model": self.model, "models": models,
                "model_available": self.model in models or f"{self.model}:latest" in models}


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, base_url: str, model: str, api_key: str | None, timeout: float,
                 transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.external = not is_local_url(base_url)
        self._transport = transport

    def generate(self, prompt: str) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body = {"model": self.model, "temperature": 0.1, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": "You return only JSON."},
                             {"role": "user", "content": prompt}]}
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport, trust_env=False) as c:
                r = c.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise AIError("AI 服務回應逾時") from exc
        except httpx.HTTPError as exc:
            raise AIError(f"無法連線到 AI 服務：{exc}") from exc
        if r.status_code != 200:
            raise AIError(f"AI 服務錯誤 {r.status_code}")
        try:
            return str(r.json()["choices"][0]["message"]["content"])
        except (ValueError, KeyError, IndexError) as exc:
            raise AIError("AI 服務回應格式不正確") from exc

    def status(self) -> dict[str, Any]:
        return {"provider": self.name, "model": self.model, "external": self.external,
                "api_key_present": bool(self.api_key)}


def build_provider(settings: AppSettings, transport: httpx.BaseTransport | None = None) -> AIProvider:
    if settings.ai_provider == "ollama":
        if not settings.ollama_model:
            raise AINotConfigured("尚未設定 Ollama 模型名稱")
        return OllamaProvider(settings.ollama_url, settings.ollama_model, settings.ai_timeout_sec, transport)
    if settings.ai_provider == "openai_compatible":
        if not settings.external_ai_base_url or not settings.external_ai_model:
            raise AINotConfigured("尚未設定外部 AI 位址或模型")
        provider = OpenAICompatibleProvider(settings.external_ai_base_url, settings.external_ai_model,
                                            os.environ.get(settings.external_ai_api_key_env),
                                            settings.ai_timeout_sec, transport)
        if provider.external and not settings.allow_external_ai:
            raise AINotConfigured("外部 AI 會收到專案程式碼，需在系統設定明確開啟「允許外部 AI」")
        return provider
    raise AINotConfigured("未設定 AI 模型（其他功能不受影響）")
