# LLM Configuration (local-only)

The classification engine uses a small provider abstraction in
`app/classification/llm_client.py`. The concrete client is selected at
runtime from `LLM_PROVIDER` by `app/classification/factory.py`.

Supported providers:

| `LLM_PROVIDER` | Client | Network | Purpose |
| --- | --- | --- | --- |
| `ollama` (default) | `OllamaClient` | local/private only | real classification with a local model |
| `mock` | `MockLLMClient` | none | deterministic unit/CI fixtures |

Cloud providers (OpenAI, Anthropic, hosted inference gateways, etc.) are
**intentionally not supported**. `Settings` rejects any other provider
value, and `OLLAMA_BASE_URL` is validated to be a loopback/private
address, a single-label Docker service name, or `host.docker.internal`.
This guarantees schema and sample data are never sent to a public
inference endpoint.

## Ollama

```bash
ollama serve
ollama pull qwen2.5:7b-instruct
```

```dotenv
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:7b-instruct
OLLAMA_REQUEST_TIMEOUT_SECONDS=120
```

| Setting | Env var | Default |
| --- | --- | --- |
| Base URL | `OLLAMA_BASE_URL` | `http://localhost:11434` |
| Model | `OLLAMA_MODEL` | `qwen2.5:7b-instruct` |
| Request timeout | `OLLAMA_REQUEST_TIMEOUT_SECONDS` | `120` |

`OllamaClient` calls `POST /api/chat` with `format: "json"` and parses
`message.content`. The response is validated by the agent layer; LLM
output is never executed as SQL or shell.

Accepted `OLLAMA_BASE_URL` forms:

* `http://localhost:11434`, `http://127.0.0.1:11434`, `http://[::1]:11434`
* private/loopback IP literals (for example `http://192.168.1.10:11434`)
* single-label Docker service names such as `http://ollama:11434`
* `http://host.docker.internal:11434`

Rejected: public DNS names, public IP addresses, URLs with embedded
credentials, and non-HTTP schemes.

## Mock

```dotenv
LLM_PROVIDER=mock
```

`MockLLMClient` returns deterministic fixture responses and makes no
network calls. It is used by the automated tests and by
`backend/scripts/validate_phase6.ps1` when a real model is not available.

## Model guidance

| Model | Size | Approx. VRAM | Notes |
| --- | --- | --- | --- |
| `qwen2.5:7b-instruct` | 4.4 GB | 6 GB | project default; strong instruction following |
| `llama3.1:8b` | 4.7 GB | 6 GB | supported alternative |
| `mistral:7b` | 4.1 GB | 5 GB | faster on smaller GPUs |

For PII classification, 7–8B parameter models are sufficient.

## Licensing

| Model family | License | Commercial use |
| --- | --- | --- |
| Qwen 2.5 | Apache 2.0 | Yes |
| Llama 3.1 | Llama 3.1 Community License | Yes, with conditions |
| Mistral 7B | Apache 2.0 | Yes |

Models run locally through Ollama. Inspect a model's license with
`ollama show <model>`.

## Changing the model

1. `ollama pull <model-name>`
2. Set `OLLAMA_MODEL=<model-name>` in the environment / `.env`.
3. Restart the backend.

No code changes are required.

## Adding a provider

The local-only guarantee is deliberate. A new provider must:

1. implement `LLMClient.complete_json(system, user)` in
   `app/classification/`;
2. be added to `build_llm_client()` in
   `app/classification/factory.py`;
3. add its settings (including a local-endpoint validator) to
   `app/core/config.py`;
4. update the docs and tests that assert the local-only policy.

Do not add a cloud provider without an explicit product/security
decision: the platform is designed for offline-first deployments.
