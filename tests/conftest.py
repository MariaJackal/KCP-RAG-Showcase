import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Settings
from services.model_client import ModelResponse, ModelUsage


class MockResponse:
    """Shared mock for legacy Vertex AI model responses (backward compat)."""

    def __init__(self, text):
        self.text = text


class FakeModelClient:
    """Drop-in ModelClient stand-in for unit tests.

    Accepts a sequence of items; each generate_text/stream_text call pops one.
    Items can be strings (normal response) or Exception instances (raised).
    """

    def __init__(self, items, *, provider="test", model_name="test-model"):
        self.items = list(items)
        self.provider = provider
        self.model_name = model_name
        self.calls = []

    def _pop(self):
        if not self.items:
            raise RuntimeError("FakeModelClient: no more items")
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def generate_text(self, prompt, **kwargs):
        self.calls.append({"method": "generate_text", "prompt": prompt, "kwargs": kwargs})
        text = self._pop()
        return ModelResponse(
            text=text,
            finish_reason="STOP",
            usage=ModelUsage(prompt=10, output=len(text.split()), total=10 + len(text.split())),
        )

    def stream_text(self, prompt, **kwargs):
        self.calls.append({"method": "stream_text", "prompt": prompt, "kwargs": kwargs})
        text = self._pop()
        # Yield text in two chunks, then a final usage chunk
        mid = max(1, len(text) // 2)
        yield ModelResponse(text=text[:mid], finish_reason=None)
        yield ModelResponse(text=text[mid:], finish_reason=None)
        yield ModelResponse(
            text="",
            finish_reason="STOP",
            usage=ModelUsage(prompt=10, output=len(text.split()), thoughts=5,
                             total=10 + len(text.split())),
        )

    # Legacy compatibility shim for tests that still call generate_content
    def generate_content(self, prompt, **kwargs):
        self.calls.append({"method": "generate_content", "prompt": prompt, "kwargs": kwargs})
        text = self._pop()
        return MockResponse(text)


def make_settings(**overrides):
    """Shared test Settings factory."""
    defaults = dict(
        project_id="test-project",
        data_store_id="test-ds",
        location="global",
        vertex_init_location="us-central1",
        app_password="pw",
    )
    defaults.update(overrides)
    return Settings(**defaults)
