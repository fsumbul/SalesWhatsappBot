# Model server deployment

The API does not assume an Arch Linux host, a reverse SSH tunnel, or a
specific model. Configure the model endpoint entirely with environment
variables, so the application server and the model server can be different
machines, containers, or operating systems.

| Variable | Meaning |
| --- | --- |
| `LLM_PROVIDER` | `ollama` or `openai_compatible` |
| `LLM_MODEL` | Exact model name served by the endpoint |
| `LLM_BASE_URL` | Ollama root (`http://host:11434`) or Chat Completions API base (`https://host/v1`) |
| `LLM_API_KEY` | Optional for Ollama; bearer token for protected OpenAI-compatible endpoints |

The application fails closed when the provider is blank, unknown, or a model
response cannot be parsed. Keep the endpoint private: bind a local container
port to loopback, use a private network/VPN, or put TLS and authentication in
front of a remote server. Never put an API key in a committed `.env` file.

## Option 1: Dockerized Ollama

This works on Linux, macOS, and Windows systems that can run Docker. It is the
simplest way to keep the model beside the application without installing a
host-specific service.

```bash
# Start the app stack and the optional model service.
docker compose --profile llm up -d --build

# Download the model selected below.
docker compose exec ollama ollama pull qwen3:8b
```

Set these values in the environment used by the `api` container:

```dotenv
LLM_PROVIDER=ollama
LLM_MODEL=qwen3:8b
LLM_BASE_URL=http://ollama:11434
```

`ollama` is the Docker service hostname. If the API runs directly on the host,
use `http://127.0.0.1:11434` instead. For GPU acceleration, add a
platform-specific Compose override; the base stack intentionally remains CPU
and platform neutral.

## Option 2: Remote or self-hosted OpenAI-compatible server

Use this mode for vLLM, LocalAI, llama.cpp server, LiteLLM, a managed OpenAI
compatible provider, or an internal inference gateway. The endpoint must
implement `POST /v1/chat/completions`, including JSON-schema response format
when the customer-agent runtime is enabled.

```dotenv
LLM_PROVIDER=openai_compatible
LLM_MODEL=your-model-name
LLM_BASE_URL=https://models.example.internal/v1
LLM_API_KEY=replace-with-a-secret
```

For a model server on another private machine, point `LLM_BASE_URL` to its
private DNS name or VPN address. Do not expose an unauthenticated inference
port to the public internet. The application server only needs outbound HTTPS
or private-network access to that endpoint.

## Option 3: Ollama on any host

Ollama itself can run on a separate supported host. Run the model there, keep
its listener private, then configure the application server with:

```dotenv
LLM_PROVIDER=ollama
LLM_MODEL=qwen3:8b
LLM_BASE_URL=http://model-host.internal:11434
```

The model name is not hard-coded: select any installed model that can follow
the runtime's structured JSON schema. Test the complete deployment before
enabling customer-facing automation.
