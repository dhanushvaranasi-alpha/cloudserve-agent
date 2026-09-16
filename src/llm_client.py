"""
Thin wrapper around the Groq client.

Every call to a model goes through here so that (a) retry/backoff is applied
once, in one place, satisfying A11 (provider timeout / rate limit handling),
and (b) tests can substitute a fake client instead of hitting the network.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional, Protocol

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import SETTINGS

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """Raised when the model provider cannot be reached or errors out."""


class ChatClient(Protocol):
    def chat(self, *, model: str, system: str, user: str, temperature: float = 0.0,
              json_mode: bool = False) -> str: ...


class GroqChatClient:
    """Real client. Constructed lazily so importing this module never
    requires network access or an API key (tests never touch this class)."""

    def __init__(self, api_key: Optional[str] = None):
        from groq import Groq  # local import: keep it out of the hot import path

        key = api_key or SETTINGS.groq_api_key
        if not key:
            raise ProviderError(
                "GROQ_API_KEY is not set. Copy .env.example to .env and fill it in."
            )
        self._client = Groq(api_key=key)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(Exception),
    )
    def chat(self, *, model: str, system: str, user: str, temperature: float = 0.0,
              json_mode: bool = False) -> str:
        try:
            kwargs: dict[str, Any] = dict(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
            )
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            completion = self._client.chat.completions.create(**kwargs)
            return completion.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001 -- provider errors are all "unavailable" to us
            logger.warning("Groq call failed: %s", exc)
            raise ProviderError(str(exc)) from exc


class FakeChatClient:
    """Deterministic stand-in for tests and offline development.

    `responses` maps a substring of the user prompt to the JSON string (or
    plain text) that should be returned when that substring appears. Falls
    back to `default` when nothing matches, so components have a sane path
    to exercise even when the network is unavailable.
    """

    def __init__(self, default: str = "{}", responses: Optional[dict[str, str]] = None):
        self.default = default
        self.responses = responses or {}
        self.calls: list[dict] = []

    def chat(self, *, model: str, system: str, user: str, temperature: float = 0.0,
              json_mode: bool = False) -> str:
        self.calls.append({"model": model, "system": system, "user": user})
        for needle, response in self.responses.items():
            if needle in user:
                return response
        return self.default


class UnavailableChatClient:
    """Used when no provider could be constructed (e.g. missing API key).

    Every call raises ProviderError immediately, which routes every ticket
    through each component's existing fallback path -- classify() falls
    back to unclear_request/confidence 0, the router force-escalates, and
    the run completes rather than crashing. This is deliberate: A11 requires
    the system to degrade gracefully on provider unavailability, and "no key
    configured" is a special case of "provider unavailable" the harness
    should survive, not a reason to abort the whole run.
    """

    def __init__(self, reason: str):
        self.reason = reason

    def chat(self, *, model: str, system: str, user: str, temperature: float = 0.0,
              json_mode: bool = False) -> str:
        raise ProviderError(self.reason)


def get_default_client() -> ChatClient:
    try:
        return GroqChatClient()
    except ProviderError as exc:
        logger.warning("Falling back to UnavailableChatClient: %s", exc)
        return UnavailableChatClient(str(exc))
