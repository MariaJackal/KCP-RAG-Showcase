"""Provider-neutral model client interface and implementations.

VertexLegacyModelClient wraps the existing vertexai.preview.generative_models API.
GoogleGenAIModelClient wraps the google-genai SDK (google.genai).

Both expose generate_text() and stream_text() with a unified ModelResponse return type.
"""

from dataclasses import dataclass, field
from typing import Iterator, Optional


@dataclass
class ModelUsage:
    prompt: Optional[int] = None
    output: Optional[int] = None
    thoughts: Optional[int] = None
    total: Optional[int] = None


@dataclass
class ModelResponse:
    text: str
    finish_reason: Optional[str] = None
    usage: ModelUsage = field(default_factory=ModelUsage)
    raw: object = None


class ModelClient:
    """Abstract provider-neutral generation interface."""

    provider: str
    model_name: str

    def generate_text(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        max_output_tokens: Optional[int] = None,
        thinking_budget: Optional[int] = None,
        thinking_level: Optional[str] = None,
    ) -> ModelResponse:
        raise NotImplementedError

    def stream_text(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        max_output_tokens: Optional[int] = None,
        thinking_budget: Optional[int] = None,
        thinking_level: Optional[str] = None,
    ) -> Iterator[ModelResponse]:
        """Yield partial ModelResponse chunks; last chunk carries finish_reason + usage."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Legacy Vertex AI adapter
# ---------------------------------------------------------------------------

def _extract_text_legacy(response) -> str:
    try:
        return (response.text or "").strip()
    except (ValueError, AttributeError):
        pass
    try:
        parts = response.candidates[0].content.parts
        return "".join(p.text for p in parts if hasattr(p, "text")).strip()
    except Exception:
        return ""


def _extract_finish_reason_legacy(response) -> Optional[str]:
    try:
        return response.candidates[0].finish_reason.name
    except Exception:
        return None


def _extract_usage_legacy(response) -> ModelUsage:
    try:
        meta = response.usage_metadata
        return ModelUsage(
            prompt=getattr(meta, "prompt_token_count", None),
            output=getattr(meta, "candidates_token_count", None),
            thoughts=None,  # not available in legacy SDK
            total=getattr(meta, "total_token_count", None),
        )
    except Exception:
        return ModelUsage()


class VertexLegacyModelClient(ModelClient):
    """Wraps vertexai.preview.generative_models.GenerativeModel."""

    provider = "vertexai_legacy"

    def __init__(self, model_name: str):
        from vertexai.preview.generative_models import GenerativeModel
        self.model_name = model_name
        self._model = GenerativeModel(model_name)

    def _gen_config(self, temperature: float, max_output_tokens: Optional[int]) -> dict:
        cfg: dict = {"temperature": temperature}
        if max_output_tokens is not None:
            cfg["max_output_tokens"] = max_output_tokens
        return cfg

    def generate_text(self, prompt, *, temperature=0.0, max_output_tokens=None,
                      thinking_budget=None, thinking_level=None) -> ModelResponse:
        response = self._model.generate_content(
            prompt,
            generation_config=self._gen_config(temperature, max_output_tokens),
        )
        return ModelResponse(
            text=_extract_text_legacy(response),
            finish_reason=_extract_finish_reason_legacy(response),
            usage=_extract_usage_legacy(response),
            raw=response,
        )

    def stream_text(self, prompt, *, temperature=0.0, max_output_tokens=None,
                    thinking_budget=None, thinking_level=None) -> Iterator[ModelResponse]:
        response = self._model.generate_content(
            prompt,
            generation_config=self._gen_config(temperature, max_output_tokens),
            stream=True,
        )
        finish_reason: Optional[str] = None
        usage = ModelUsage()
        for chunk in response:
            text = ""
            try:
                text = chunk.text or ""
            except (ValueError, AttributeError):
                try:
                    finish_reason = chunk.candidates[0].finish_reason.name
                except Exception:
                    finish_reason = "UNKNOWN"
            try:
                fr = chunk.candidates[0].finish_reason.name
                if fr and fr != "FINISH_REASON_UNSPECIFIED":
                    finish_reason = fr
            except Exception:
                pass
            yield ModelResponse(text=text, finish_reason=None, raw=chunk)

        # final chunk carries accumulated usage + finish_reason
        try:
            meta = response.usage_metadata
            usage = ModelUsage(
                prompt=getattr(meta, "prompt_token_count", None),
                output=getattr(meta, "candidates_token_count", None),
                thoughts=None,
                total=getattr(meta, "total_token_count", None),
            )
        except Exception:
            pass
        yield ModelResponse(text="", finish_reason=finish_reason, usage=usage)


# ---------------------------------------------------------------------------
# Google Gen AI adapter
# ---------------------------------------------------------------------------

def _extract_finish_reason_genai(response_or_chunk) -> Optional[str]:
    try:
        fr = response_or_chunk.candidates[0].finish_reason
        return fr.name if hasattr(fr, "name") else str(fr)
    except Exception:
        return None


def _extract_usage_genai(response_or_chunk) -> ModelUsage:
    try:
        meta = response_or_chunk.usage_metadata
        return ModelUsage(
            prompt=getattr(meta, "prompt_token_count", None),
            output=getattr(meta, "candidates_token_count", None),
            thoughts=getattr(meta, "thoughts_token_count", None),
            total=getattr(meta, "total_token_count", None),
        )
    except Exception:
        return ModelUsage()


def _extract_text_genai(response_or_chunk) -> str:
    try:
        return (response_or_chunk.text or "").strip()
    except (ValueError, AttributeError):
        pass
    try:
        parts = response_or_chunk.candidates[0].content.parts
        return "".join(p.text for p in parts if hasattr(p, "text")).strip()
    except Exception:
        return ""


class GoogleGenAIModelClient(ModelClient):
    """Wraps google-genai SDK (google.genai.Client with vertexai=True)."""

    provider = "google_genai"

    def __init__(self, project_id: str, location: str, model_name: str):
        from google import genai
        self._client = genai.Client(vertexai=True, project=project_id, location=location)
        self.model_name = model_name

    def _build_config(self, temperature, max_output_tokens, thinking_budget, thinking_level):
        from google.genai.types import GenerateContentConfig, ThinkingConfig
        kwargs: dict = {"temperature": temperature}
        if max_output_tokens is not None:
            kwargs["max_output_tokens"] = max_output_tokens
        thinking = None
        if thinking_budget and thinking_budget > 0:
            thinking = ThinkingConfig(thinking_budget=thinking_budget)
        elif thinking_level:
            thinking = ThinkingConfig(thinking_level=thinking_level)
        if thinking is not None:
            kwargs["thinking_config"] = thinking
        return GenerateContentConfig(**kwargs)

    def generate_text(self, prompt, *, temperature=0.0, max_output_tokens=None,
                      thinking_budget=None, thinking_level=None) -> ModelResponse:
        cfg = self._build_config(temperature, max_output_tokens, thinking_budget, thinking_level)
        response = self._client.models.generate_content(
            model=self.model_name, contents=prompt, config=cfg,
        )
        return ModelResponse(
            text=_extract_text_genai(response),
            finish_reason=_extract_finish_reason_genai(response),
            usage=_extract_usage_genai(response),
            raw=response,
        )

    def stream_text(self, prompt, *, temperature=0.0, max_output_tokens=None,
                    thinking_budget=None, thinking_level=None) -> Iterator[ModelResponse]:
        cfg = self._build_config(temperature, max_output_tokens, thinking_budget, thinking_level)
        for chunk in self._client.models.generate_content_stream(
            model=self.model_name, contents=prompt, config=cfg,
        ):
            yield ModelResponse(
                text=_extract_text_genai(chunk),
                finish_reason=_extract_finish_reason_genai(chunk),
                usage=_extract_usage_genai(chunk),
                raw=chunk,
            )
