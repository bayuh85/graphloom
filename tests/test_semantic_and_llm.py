"""Semantic cache, batching, and the OpenAI/Anthropic client. No network."""

import json
import os

import pytest

from graphloom.build import build_repository
from graphloom.llm import LlmClient, client_from_env, extract_text, load_dotenv
from graphloom.semantic import parse_model_json
from paths import EXAMPLE


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.model = "fake-model"
        self.prompts = []

    def complete(self, system, user, max_tokens=700):
        self.calls += 1
        self.prompts.append(user)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_semantic_pass_caches_and_links_with_llm_provenance(tmp_path):
    payload = json.dumps(
        {
            "concepts": [
                {
                    "chunk": 0,
                    "name": "Receipt mail",
                    "summary": "A payment queues a receipt and send_receipt delivers it.",
                    "anchors": ["send_receipt", "payments"],
                }
            ]
        }
    )
    client = FakeClient(payload)
    cache = tmp_path / "cache"
    first = build_repository(EXAMPLE, semantic=True, max_calls=8, client=client, cache_dir=cache)
    assert client.calls >= 1
    assert client.calls <= 8
    concept = first.nodes["concept:receipt-mail"]
    assert concept.layer == "concept"
    about = [
        edge
        for edge in first.edges.values()
        if edge.relation == "about" and edge.source == concept.id and edge.provenance == "llm"
    ]
    targets = {first.nodes[edge.target].name for edge in about}
    assert "send_receipt" in targets
    assert "payments" in targets
    # The code graph was not the prompt body; symbol names are, source is not.
    assert "def send_receipt" not in "\n".join(client.prompts)
    again_client = FakeClient(payload)
    second = build_repository(
        EXAMPLE, semantic=True, max_calls=8, client=again_client, cache_dir=cache
    )
    assert again_client.calls == 0
    assert second.meta["semantic"]["cache_hits"] >= 1
    assert "concept:receipt-mail" in second.nodes


def test_max_calls_zero_skips_the_network(tmp_path):
    client = FakeClient('{"concepts": [{"chunk": 0, "name": "Nope", "summary": "x", "anchors": ["send_receipt"]}]}')
    graph = build_repository(
        EXAMPLE,
        semantic=True,
        max_calls=0,
        client=client,
        cache_dir=tmp_path / "cache",
    )
    assert client.calls == 0
    assert not any(node.kind == "concept" for node in graph.nodes.values())
    assert graph.meta["semantic"]["skipped"] >= 1


def test_bad_model_json_keeps_the_structural_graph(tmp_path):
    client = FakeClient("I cannot answer that.")
    graph = build_repository(
        EXAMPLE,
        semantic=True,
        max_calls=2,
        client=client,
        cache_dir=tmp_path / "cache",
    )
    assert any(node.kind == "table" and node.name == "invoices" for node in graph.nodes.values())
    assert any("semantic batch failed" in warning for warning in graph.meta["warnings"])


def test_parse_model_json_accepts_a_fence():
    data = parse_model_json('```json\n{"concepts": []}\n```')
    assert data == {"concepts": []}


def test_openai_and_anthropic_transports():
    seen = {}

    def transport(url, payload, headers, timeout):
        seen["url"] = url
        seen["payload"] = payload
        seen["headers"] = headers
        if url.endswith("/messages"):
            body = json.dumps({"content": [{"type": "text", "text": "anthropic ok"}]})
        else:
            body = json.dumps({"choices": [{"message": {"content": "openai ok"}}]})
        return 200, body

    openai = LlmClient(
        "https://atmorouter.dev/v1",
        "ar-test",
        "zai/glm-5.3-flash",
        transport=transport,
    )
    assert openai.complete("sys", "hi") == "openai ok"
    assert seen["url"] == "https://atmorouter.dev/v1/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer ar-test"
    assert seen["payload"]["model"] == "zai/glm-5.3-flash"
    anthropic = LlmClient(
        "https://atmorouter.dev/v1",
        "ar-test",
        "zai/glm-5.3-flash",
        style="anthropic",
        transport=transport,
    )
    assert anthropic.complete("sys", "hi") == "anthropic ok"
    assert seen["url"] == "https://atmorouter.dev/v1/messages"
    assert seen["headers"]["x-api-key"] == "ar-test"
    assert extract_text(
        json.dumps({"choices": [{"message": {"content": [{"type": "text", "text": "part"}]}}]}),
        "openai",
    ) == "part"


def test_dotenv_does_not_override_existing_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GRAPHLOOM_MODEL=from-file\nATMOROUTER_API_KEY=ar-from-file\n", encoding="utf-8")
    monkeypatch.setenv("GRAPHLOOM_MODEL", "already")
    monkeypatch.delenv("ATMOROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GRAPHLOOM_API_KEY", raising=False)
    load_dotenv(env)
    assert os.environ["GRAPHLOOM_MODEL"] == "already"
    assert os.environ["ATMOROUTER_API_KEY"] == "ar-from-file"
    monkeypatch.chdir(tmp_path)
    client = client_from_env(transport=lambda *args, **kwargs: (200, "{}"))
    assert client.model == "already"
    assert client.base_url == "https://atmorouter.dev/v1"
    assert client.api_key == "ar-from-file"


def test_missing_key_raises():
    client = LlmClient("https://atmorouter.dev/v1", "", "zai/glm-5.3-flash")
    with pytest.raises(Exception) as caught:
        client.complete("sys", "user")
    assert "API key" in str(caught.value)
