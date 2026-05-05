from __future__ import annotations

from dataclasses import dataclass
from json import JSONDecodeError
import time
from typing import Any, Protocol

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    BadRequestError,
    OpenAI,
    RateLimitError,
)


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ModelStep:
    thought: str
    action: str
    action_input: dict[str, Any]
    raw_response: str


class ModelAdapter(Protocol):
    def complete(self, messages: list[ModelMessage]) -> str:
        raise NotImplementedError


class EmptyModelResponseError(RuntimeError):
    """モデル API が choices や message content のない応答を返した場合の retry 用例外。"""


class OpenAIModelAdapter:
    def __init__(
        self,
        *,
        model: str,
        api_base: str,
        api_key: str,
        temperature: float,
        max_output_tokens: int = 2048,
        request_timeout_seconds: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries

    def _create_completion(
        self,
        client: OpenAI,
        request_payload: dict[str, Any],
    ) -> Any:
        try:
            return client.chat.completions.create(**request_payload)
        except BadRequestError as exc:
            if "response_format" not in str(exc):
                raise
            fallback_payload = dict(request_payload)
            fallback_payload.pop("response_format", None)
            return client.chat.completions.create(**fallback_payload)

    def complete(self, messages: list[ModelMessage]) -> str:
        if not self.api_key:
            raise RuntimeError("Missing model API key in config.agent.api_key.")

        client = OpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
            timeout=self.request_timeout_seconds,
            max_retries=0,
        )

        request_payload = {
            "model": self.model,
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "response_format": {"type": "json_object"},
        }

        last_error: Exception | None = None
        for attempt_index in range(self.max_retries + 1):
            try:
                response = self._create_completion(client, request_payload)
                choices = response.choices or []
                if not choices:
                    raise EmptyModelResponseError("Model response missing choices.")
                content = choices[0].message.content
                if not isinstance(content, str) or not content.strip():
                    raise EmptyModelResponseError("Model response missing text content.")
                break
            except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                last_error = exc
                if attempt_index >= self.max_retries:
                    raise RuntimeError(f"Model request failed: {exc}") from exc
                time.sleep(min(2.0**attempt_index, 30.0))
            except EmptyModelResponseError as exc:
                if attempt_index >= self.max_retries:
                    raise RuntimeError(str(exc)) from exc
                time.sleep(min(2.0**attempt_index, 30.0))
            except APIStatusError as exc:
                if exc.status_code < 500:
                    raise RuntimeError(f"Model request failed: {exc}") from exc
                last_error = exc
                if attempt_index >= self.max_retries:
                    raise RuntimeError(f"Model request failed: {exc}") from exc
                time.sleep(min(2.0**attempt_index, 30.0))
            except JSONDecodeError as exc:
                last_error = exc
                if attempt_index >= self.max_retries:
                    raise RuntimeError(f"Model response was not valid JSON: {exc}") from exc
                time.sleep(min(2.0**attempt_index, 30.0))
            except APIError as exc:
                raise RuntimeError(f"Model request failed: {exc}") from exc
        else:
            raise RuntimeError(f"Model request failed: {last_error}")

        return content


class ScriptedModelAdapter:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def complete(self, messages: list[ModelMessage]) -> str:
        del messages
        if not self._responses:
            raise RuntimeError("No scripted model responses remaining.")
        return self._responses.pop(0)
