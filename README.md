# Send Money Agent

International money transfer assistant built with [Google ADK](https://github.com/google/adk-python). Single agent architecture with deterministic tool-based validation.

## Architecture

### Single Agent + 8 Tools

```
                          User Message
                               |
                               v
                       +---------------+
                       | SendMoneyAgent|  (1 LLM call per message)
                       +-------+-------+
                               |
            +------------------+------------------+--- - - - - - - - -+
            |                  |                  |                    :
            v                  v                  v                    v
      save_country       save_amount       save_beneficiary    convert_currency
            |                  |                  |             (Frankfurter API)
            +------------------+------------------+
            |
            |  (country must be set first)
            v
     save_delivery_method
            |
            v
    all fields complete?
        |           |
       yes          no ── ask user ──+
        |                            |
        v                            |
   review_transfer  <────────────────+
        |
        v
   user reviews summary
        |               |
     "confirm"       "cancel"
        |               |
        v               v
 confirm_transfer  cancel_transfer
        |               |
        v               v
    confirmed       cancelled
        |           (next save tool
   60s server        auto-clears all
   auto-submit       fields for fresh start)
        |
        v
    submitted
  (cannot cancel)
```

**Why single agent?** Real users don't follow linear paths — they say "Send $200 to Maria in Mexico" (all at once) or change their mind mid-flow. A single agent with tools handles this naturally in 1 LLM call per message. All validation is deterministic (in Python tool code), so no second LLM call is needed.

**How it works:** The agent collects four pieces of information: destination country, amount, beneficiary, and delivery method. `save_country`, `save_amount`, `save_beneficiary`, and `convert_currency` run in parallel when the user provides multiple fields at once. `save_delivery_method` runs after `save_country` because it validates against the country's available methods. Once all fields are set, `review_transfer` presents a summary. The user then confirms or cancels. On confirm, the server auto-submits after 60 seconds — the user can cancel within that window. On cancel (or after confirm/submit), the next save tool call auto-clears all fields so a new transfer starts fresh in the same session.

See [Architecture Documentation](docs/ARCHITECTURE.md) for full details, diagrams, and scenario walkthroughs.

### Guardrails

- **Prompt-based**: The agent instruction includes guardrails for off-topic requests, prompt injection attempts, and instruction disclosure — handled conversationally by the Agent.
- **Message length limit**: Messages over 1000 characters are intercepted server-side and replaced with a system hint, so the LLM asks the user to rephrase with the current missing information it needs for the transfer. Blocks long prompt injection payloads without reaching the model.
- **Deterministic validation**: Country, amount, and delivery method validation happens in tool code (Python), not LLM instructions. Business rules are always enforced.

## Supported Countries

All transfers: **$1.00 minimum, $10,000.00 maximum** (USD).

| Country | Code | Currency | Bank Deposit | Mobile Wallet |
|---------|------|----------|:---:|:---:|
| Mexico | MX | MXN | x | x |
| Philippines | PH | PHP | x | x |
| India | IN | INR | x | x |
| Colombia | CO | COP | x | x |
| Guatemala | GT | GTQ | x | x |
| United Kingdom | GB | GBP | x | |
| Canada | CA | CAD | x | x |
| Brazil | BR | BRL | x | x |
| Nigeria | NG | NGN | x | x |
| Kenya | KE | KES | | x |

**Delivery Methods**:
- **Bank Deposit** ($3.99 fee, 1-2 business days) — needs beneficiary name, bank name, and account number.
- **Mobile Wallet** ($1.99 fee, instant to 1 hour) — needs beneficiary name and phone number.

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- An OpenAI API key (default model: `openai/gpt-5-mini`)

### Setup

```bash
cd send-money-agent
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY
```

### Run

```bash
uv run uvicorn app.server:app --reload
# Opens at http://127.0.0.1:8000
```

### Run with ADK Web UI

```bash
cp .env send_money_agent/.env
uv run adk web send_money_agent
```

### Run with Docker

```bash
docker build -t send-money-agent .
docker run -p 8000:8000 -e OPENAI_API_KEY=your-key send-money-agent
```

## Testing

```bash
# Unit + data tests (no API key needed) — 52 tests
uv run pytest tests/test_tools.py tests/test_data.py -v

# Integration tests (requires API key) — 11 tests
RUN_INTEGRATION_TESTS=1 uv run pytest tests/test_scenarios.py -v
```

**63 total tests**: 45 unit tests (tool logic) + 7 data validation tests + 11 integration tests (real LLM).

See [Testing Suite Documentation](docs/TESTING_SUITE.md) for full test coverage details, mock strategy, and per-test descriptions.

## Session Management

Each conversation lives in an ADK **Session** — an isolated state container identified by `(app_name, user_id, session_id)`.

- **Development**: `InMemorySessionService` (default). Sessions live in memory and are lost on server restart.
- **Production**: Set `DATABASE_URL` to enable `DatabaseSessionService` with PostgreSQL, SQLite, or MySQL. Sessions persist across restarts and scale across multiple server instances.

All transfer data is stored in session state using flat `transfer_*` keys (e.g., `transfer_country`, `transfer_amount`, `transfer_status`). Tools read and write to this shared state via `ToolContext.state`, which is the ADK mechanism for structured, deterministic state mutations.

## Chat UI (Prototype)

The web UI at `/` is a single-page prototype for testing the agent:

- **Chat panel** with SSE streaming — text appears token by token as the LLM generates it, so users don't stare at a blank screen waiting.
- **Transfer Details panel** — shows live session state (country, amount, beneficiary, delivery method, status) updated after each message.
- **Timestamps** on all messages (user and agent) — useful for measuring response times during testing.
- **Download Chat** button — exports the full conversation with timestamps as `chat_history_{session_uuid}.txt` for review and debugging.
- **New Session** button — creates a fresh session with clean state, without reloading the page.
- **Full session UUID** displayed in the header for traceability.

## Project Structure

```
send-money-agent/
|-- pyproject.toml              # dependencies (google-adk, fastapi, httpx, etc.)
|-- Dockerfile
|-- .env.example                # template for API keys and model config
|-- send_money_agent/           # ADK agent package
|   |-- __init__.py
|   |-- agent.py                # root_agent definition, instructions, history callback
|   |-- tools.py                # 8 tool functions + 4 private helpers
|   |-- data.py                 # countries, delivery methods, validation rules
|   +-- case_sample/            # real chat history cases for reference
|-- app/                        # FastAPI server + chat UI
|   |-- __init__.py
|   |-- server.py               # SSE streaming, message length guardrail
|   |-- logging_config.py       # loguru setup
|   +-- static/
|       +-- index.html          # single-file chat UI
|-- docs/                        # architecture and testing documentation
|   |-- ARCHITECTURE.md
|   +-- TESTING_SUITE.md
+-- tests/
    |-- test_tools.py           # 45 unit tests for all 8 tools
    |-- test_data.py            # 7 data consistency tests
    +-- test_scenarios.py       # 11 integration tests (require API key)
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Chat UI |
| `/health` | GET | Health check |
| `/sessions?user_id=X` | POST | Create new session |
| `/sessions/{id}/state?user_id=X` | GET | Get transfer state |
| `/chat` | POST | Send message (SSE streaming) `{user_id, session_id, message}` |

## Model Configuration

The default model is `openai/gpt-5-mini`. Change it in `.env`:

```bash
# OpenAI
AGENT_MODEL=openai/gpt-5-mini

```

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Single agent** over multi-agent routing | 1 LLM call per message. Simpler, faster, cheaper. |
| **8 granular tools** | Each validates one concern. Enables parallel execution — `save_country` + `save_amount` + `save_beneficiary` run simultaneously. |
| **Prompt-based guardrails** over regex callbacks | LLM handles nuance naturally. Regex is too strict (blocked "hello") or too loose. |
| **Validation in tools** (deterministic) | Business rules enforced in Python, not LLM instructions. 100% reliable. |
| **Server-side message length guard** | Blocks prompt injection payloads before they reach the LLM. Fast, free, no tokens wasted. |
| **SSE streaming** | Better UX — users see text appearing immediately instead of waiting for the full response to generate. |

See [Architecture Documentation](docs/ARCHITECTURE.md) for expanded rationale and scenario walkthroughs.

## Known Issues

### Thought Leakage with Thinking Models via LiteLLM

The ADK framework requires using LiteLLM for OpenAI models, but LiteLLM does not properly flag reasoning tokens as `Part.thought=True`. This causes internal reasoning content to leak into user-visible responses.

When using `openai/gpt-5-mini` with `reasoning_effort="low"` through ADK's `LiteLlm` wrapper, the model's internal reasoning tokens sometimes leak into user-visible responses. The existing filter in `server.py` checks `Part.thought == True` to strip thinking content:

```python
# server.py — _extract_text()
for part in event.content.parts:
    if getattr(part, "thought", False):
        logger.debug("Filtered thought part: {}...", (part.text or "")[:80])
        continue
    if part.text:
        texts.append(part.text)
```

This works correctly for models that set the `thought` flag (e.g., Gemini). However, ADK's LiteLLM layer **does not always set `Part.thought=True`** on OpenAI reasoning tokens — the chain-of-thought arrives as regular, unflagged text parts, indistinguishable from actual response content. The filter has nothing to catch.

**Observed impact**: Internal reasoning fragments like `"We should not add more. This is last assistant message already done."` appear appended to otherwise correct agent responses.

**Related upstream issues** ([google/adk-python](https://github.com/google/adk-python)): [#3694](https://github.com/google/adk-python/issues/3694) (reasoning content discarded instead of flagged) and [#3983](https://github.com/google/adk-python/issues/3983) (reasoning chunk duplication). Neither fully resolves this case — our leakage is unflagged text, not missing `reasoning_content` fields or duplicated `ReasoningChunk` objects.

**Proposed workaround**: An LLM-as-judge post-processing step (via ADK's `after_model_callback` or a lightweight second pass) that reviews the primary agent's output before streaming to the user, stripping any leaked reasoning. This adds latency and cost but guarantees clean output.

**Upstream fix**: This should be resolved in `google-adk` when the LiteLLM adapter properly maps OpenAI reasoning tokens to `Part.thought=True`, allowing the existing `_extract_text()` filter to work as designed.

## Scaling

- Set `DATABASE_URL` env var to switch from `InMemorySessionService` to `DatabaseSessionService` (PostgreSQL, SQLite, MySQL)
- Agent is stateless — scales horizontally behind a load balancer
- Each session is isolated — no shared mutable state between users
