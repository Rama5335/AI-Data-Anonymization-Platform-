# Per-Language LLM Configuration

This document explains how to configure different LLM providers and models
for the classification engine, including licensing considerations and
configuration surfaces.

## Overview

The platform is provider-agnostic through a thin abstraction in
`app/classification/llm_client.py`. The concrete client is selected at
runtime via the `LLM_PROVIDER` environment variable and instantiated by
the factory in `app/classification/factory.py`.

```
LLM_PROVIDER=ollama          # or "openai" (future), "mock" (testing)
OLLAMA_BASE_URL=http://...
OLLAMA_MODEL=llama3.1:8b
```

## Supported Providers

### Ollama (default, fully local)

| Setting | Env var | Default | Description |
| --- | --- | --- | --- |
| Base URL | `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama server endpoint |
| Model | `OLLAMA_MODEL` | `llama3.1:8b` | Model name (must be pulled first) |
| Request timeout | `OLLAMA_TIMEOUT_SECONDS` | `120` | Per-request timeout |

**How it works:** The `OllamaClient` sends a `POST /api/generate` request
to the Ollama server, which runs inference entirely on the local machine.
No data leaves the network.

**Pulling models:**
```bash
ollama pull llama3.1:8b        # 4.7 GB, good general-purpose classifier
ollama pull qwen2.5:7b-instruct # 4.4 GB, strong instruction following
ollama pull mistral:7b          # 4.1 GB, faster inference on smaller GPUs
```

**Model selection guidance:**

| Model | Size | VRAM | Quality | Speed |
| --- | --- | --- | --- | --- |
| `llama3.1:8b` | 4.7 GB | 6 GB | High | Medium |
| `qwen2.5:7b-instruct` | 4.4 GB | 6 GB | High | Medium |
| `mistral:7b` | 4.1 GB | 5 GB | Medium | Fast |
| `llama3.1:70b` | 40 GB | 48 GB | Very high | Slow |

For PII classification tasks, 7–8B parameter models are sufficient.
Larger models provide diminishing returns for this specific task while
requiring significantly more VRAM.

### Mock (testing only)

| Setting | Env var | Default | Description |
| --- | --- | --- | --- |
| — | — | — | Always returns a canned response |

Used by `tests/test_classification_agent.py` and CI. Produces no real
inference. Selected when `LLM_PROVIDER=mock` or when no LLM is
configured.

### OpenAI-compatible (future)

Any provider that exposes an OpenAI-compatible `/v1/chat/completions`
endpoint can be used by setting:

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1   # or any compatible endpoint
OPENAI_MODEL=gpt-4o-mini
```

> **Security note:** This sends data to a remote API. Only use this
> provider if your organization's security policy permits it and the
> data has been pre-sanitized. The platform will refuse to use remote
> LLM providers unless `ALLOW_REMOTE_LLM=true` is explicitly set.

## Licensing

| Model family | License | Commercial use | Notes |
| --- | --- | --- | --- |
| Llama 3.1 | Llama 3.1 Community License | Yes (with conditions) | Meta's permissive license; 700M+ MAU requires special terms |
| Qwen 2.5 | Apache 2.0 | Yes | Fully open; no restrictions |
| Mistral 7B | Apache 2.0 | Yes | Fully open; no restrictions |

All models run locally via Ollama. No API keys or network access is
required for inference. License files are included in the Ollama model
registry and can be inspected via `ollama show <model>`.

## Configuration Surface

All LLM configuration is read from environment variables via
`app/core/config.py` → `Settings` pydantic model. The variables are:

| Variable | Type | Default | Description |
| --- | --- | ---| ---|
| `LLM_PROVIDER` | `str` | `"mock"` | Provider selection |
| `OLLAMA_BASE_URL` | `str` | `http://127.0.0.1:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `str` | `llama3.1:8b` | Model to use |
| `OLLAMA_TIMEOUT_SECONDS` | `int` | `120` | Request timeout |
| `ALLOW_REMOTE_LLM` | `bool` | `false` | Gate for non-local providers |

### Changing the model

1. Pull the new model: `ollama pull <model-name>`
2. Set the env var: `OLLAMA_MODEL=<model-name>`
3. Restart the backend

No code changes are needed. The factory will pick up the new model name
and create an `OllamaClient` with the updated configuration.

### Adding a new provider

To add support for a new LLM provider:

1. Create `app/classification/<provider>_client.py` implementing
   `BaseLLMClient.complete(prompt: str) -> str`
2. Add the provider to `app/classification/factory.py`'s provider map
3. Add the corresponding env vars to `app/core/config.py`
4. Add `LLM_PROVIDER=<provider>` support to the Docker compose files

The interface is intentionally minimal (single `complete` method) to
make new providers trivial to implement.
