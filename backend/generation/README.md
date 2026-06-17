# S1.8 — Answer Generation Foundation

Provider-agnostic, grounded, citation-aware answer generation. Consumes the
`PromptPackage` from S1.7 and returns a structured `GeneratedAnswer`.

## Quickstart

```python
import generation as g
from generation.providers.mock import MockProvider  # or create_provider(...)

package = g.PromptPackage(
    request_id=g.new_request_id(),
    system_prompt="You are a helpful, grounded assistant.",
    user_query="What is the capital of France?",
    context_chunks=(
        g.ContextChunk("c1", "The capital of France is Paris.", "doc-1", score=0.92),
    ),
    citations=(g.CitationRecord("cite-1", "c1", "doc-1", title="Geo", locator="p.1"),),
    params=g.GenerationParams(confidence_threshold=0.3),
)

# Production: provider = g.create_provider("anthropic", api_key=...)
pipeline = g.GenerationPipeline(MockProvider(), metrics=g.InMemoryMetricsSink())
answer = pipeline.generate(package)

print(answer.status.value, answer.confidence, answer.supporting_chunk_ids)
```

## Providers

`create_provider(name, **kwargs)` — names: `openai`, `azure_openai`,
`anthropic`, `local`, `mock`. Vendor SDKs are imported lazily; install
`openai` / `anthropic` only for the providers you use.

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

26 tests cover providers, citation binding, grounding, safety gates, retries,
and end-to-end pipeline behaviour. The mock provider makes the whole suite
deterministic and network-free.

## Docs

- `S1.8-ARCHITECTURE.md`
- `S1.8-FAILURE-HANDLING.md`
- `S1.8-SEQUENCE-DIAGRAMS.md`
