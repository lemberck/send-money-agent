# Architecture Documentation

> Comprehensive architecture reference for the Send Money Agent.
>
> Back to [README](../README.md)

---

## 1. System Overview

```
                         +---------------------------------------------+
                         |              SendMoneyAgent                  |
  User ──── message ───> |  (single LLM agent, prompt-based flow)      |
                         +---------------------+-----------------------+
                                               |
                         parallel tool calls   |    sequential tool calls
                    +----------+----------+    |     +------------------+
                    |          |          |    |     |                  |
                    v          v          v    |     v                  v
              save_country save_amount  save_  |  save_delivery_   review_transfer
                                    beneficiary|  method (needs     confirm_transfer
                                               |   country first)   cancel_transfer
                                               |
                                               v
                                        convert_currency
                                        (Frankfurter API)
                                               |
                                               v
                                        Session State
                                   (flat transfer_* keys)
```

### Why single agent?

- **1 LLM call per message** — no routing, no orchestration overhead.
- **Parallel tool execution** — when a user says "Send $200 to Maria in Mexico", the agent calls `save_country`, `save_amount`, and `save_beneficiary` simultaneously.
- **All validation is deterministic** — country, amount, delivery method, and beneficiary rules are enforced in Python tool code, not LLM instructions. The LLM decides *which* tools to call; the tools decide *if the data is valid*.

### Tech stack

| Component | Technology |
|-----------|-----------|
| Agent framework | [Google ADK](https://github.com/google/adk-python) |
| Web server | FastAPI |
| Streaming | SSE (Server-Sent Events) via `sse-starlette` |
| HTTP client | httpx (async, for currency conversion) |
| Logging | loguru |
| Default LLM | `openai/gpt-5-mini` (configurable) |

---

## 2. Agent Design

### `agent.py`

The `root_agent` is a single `google.adk.agents.Agent` with 8 registered tools. All behavior is driven by the `instruction` prompt, which is organized into sections:

| Section | Purpose |
|---------|---------|
| **GUARDRAILS** | Off-topic deflection, prompt injection resistance, no internal disclosure |
| **FLOW** | Step-by-step transfer collection: country, amount, beneficiary, delivery method, review, confirm |
| **SUBMISSION WINDOW** | 60-second auto-submit after confirmation, cancellation rules |
| **KEEP IT SIMPLE** | No KYC/AML documents, trust tool validation, don't add extra checks |
| **BE PROACTIVE** | Act on clear intent, don't over-ask, keep responses short |
| **TOOL USAGE** | Parallel execution rules, when to call `convert_currency`, `save_delivery_method` sequencing |
| **CURRENCY** | Assume USD unless explicitly stated, limits always in USD |
| **AMOUNT LIMITS** | $1 min, $10,000 max, no split-transfer workarounds |
| **TRANSACTION HISTORY** | Use system-injected history for status queries, don't guess from conversation |
| **CORRECTIONS** | Re-call save tools to overwrite, country change clears delivery method |

### `before_model_callback`: Transaction History Injection

The agent registers `_inject_transaction_history` as a `before_model_callback`. Before every LLM call, this callback reads `transfer_history` from session state and, if present, appends a formatted summary of all past transactions (reference, status, amount, beneficiary, country, delivery method) to the system instruction. This ensures the LLM always has authoritative transaction status data and never guesses from conversation memory.

### Prompt-based guardrails strategy

The agent uses **prompt-based guardrails** rather than regex or callback filters:

- The LLM handles nuance naturally (e.g., "hello" is fine, "write me an essay" is off-topic).
- Regex-based approaches were too strict (blocked friendly greetings) or too loose (missed creative prompt injections).
- The server-side **message length guard** (1000 chars) provides a hard boundary against long injection payloads before they reach the LLM.

---

## 3. Tools Reference

The agent has **8 tools**: 4 save tools (async, parallel-capable), 3 lifecycle tools (sync), and 1 utility tool (async).

### Save Tools (async, parallel)

#### `save_country`

```python
async def save_country(tool_context: ToolContext, country: str) -> dict
```

- **Purpose**: Validate and save destination country.
- **Key behavior**: Case-insensitive matching against `SUPPORTED_COUNTRIES`. Changing country clears `transfer_delivery_method` (may not be available in new country). Sets `transfer_currency` to `"USD"` if not already set.
- **Returns**: `{status, country, country_code, local_currency, available_delivery_methods, missing_fields}`

#### `save_amount`

```python
async def save_amount(tool_context: ToolContext, amount: float) -> dict
```

- **Purpose**: Validate and save transfer amount in USD.
- **Key behavior**: Enforces `MIN_AMOUNT` ($1.00) and `MAX_AMOUNT` ($10,000.00). Rejects zero and negative values.
- **Returns**: `{status, amount, missing_fields}`

#### `save_delivery_method`

```python
async def save_delivery_method(tool_context: ToolContext, delivery_method: str) -> dict
```

- **Purpose**: Validate and save delivery method.
- **Key behavior**: **Requires country to be set first** (cannot run in parallel with `save_country`). Validates against the country's `available_delivery_methods`.
- **Returns**: `{status, delivery_method, delivery_method_name, fee, eta, missing_fields}`

#### `save_beneficiary`

```python
async def save_beneficiary(tool_context: ToolContext, name: str = "", account: str = "") -> dict
```

- **Purpose**: Save beneficiary name and/or account info.
- **Key behavior**: Name must be at least 2 words (full name). Converts to title case. Account is saved as-is (bank name / account number for bank deposit, phone number for mobile wallet). Supports partial saves — can set name and account independently.
- **Returns**: `{status, saved, [errors], missing_fields}` — status can be `"partial_error"` if name is invalid but account was saved.

### Lifecycle Tools (sync)

#### `review_transfer`

```python
def review_transfer(tool_context: ToolContext) -> dict
```

- **Purpose**: Generate transfer summary for user review.
- **Key behavior**: Checks all required fields. If complete, sets `transfer_status` to `"reviewing"` and returns a formatted summary with fee and total cost. If incomplete, returns missing fields list.
- **Returns**: `{status, summary|missing}` — summary includes destination, amount, beneficiary, method, fee, total cost, ETA.

#### `confirm_transfer`

```python
def confirm_transfer(tool_context: ToolContext) -> dict
```

- **Purpose**: Confirm the transfer for processing.
- **Key behavior**: Requires `transfer_status == "reviewing"`. Sets status to `"confirmed"`, records `transfer_confirmed_at` timestamp, generates `TXN-XXXXXXXX` reference number. Calls `_save_to_history` to record the transaction in `transfer_history`.
- **Returns**: `{status, message, confirmation_details}`

#### `cancel_transfer`

```python
def cancel_transfer(tool_context: ToolContext) -> dict
```

- **Purpose**: Cancel the current transfer.
- **Key behavior**: Blocked if status is `"submitted"` (60-second window expired). Sets status to `"cancelled"`, clears `transfer_confirmed_at`. Transfer details are preserved in state at this point, but the next save tool call will auto-clear all fields (same as confirmed/submitted). Updates transaction history status via `_update_history_status`.
- **Returns**: `{status, message}`

### Utility Tool (async)

#### `convert_currency`

```python
async def convert_currency(amount: float, from_currency: str, to_currency: str = "USD") -> dict
```

- **Purpose**: Convert amounts between currencies using live exchange rates.
- **Key behavior**: Uses the [Frankfurter API](https://api.frankfurter.dev/) (free, no key needed). Same-currency conversion returns immediately (rate=1.0). **Does not use `ToolContext`** — stateless, no session access.
- **Returns**: `{status, from, to, amount, converted, rate}`

### Parallel Execution Strategy

```
User: "Send 5000 reais to John Smith in Colombia"

  +-----------------+     +-----------------+     +-------------------+
  | convert_currency|     | save_country    |     | save_beneficiary  |
  | (5000, BRL,USD) |     | ("Colombia")    |     | (name="John Smith"|
  +-----------------+     +-----------------+     +-------------------+
         |                       |                        |
         v                       v                        v
    rate result            country saved             name saved
                                 |
                    (now country is set)
                                 |
                                 v
                    +------------------------+
                    | save_delivery_method   |
                    | ("bank_deposit")       |
                    +------------------------+
```

Key rules:
- `save_country`, `save_amount`, `save_beneficiary`, and `convert_currency` can all run in parallel.
- `save_delivery_method` must run **after** `save_country` (it validates against the country's available methods).

---

## 4. State Machine

### State Diagram

```
                    +-------------+
                    | not_started |
                    +------+------+
                           |
                      save tool called
                           |
                           v
                    +-------------+       corrections
                    | collecting  | <──────────────────+
                    +------+------+                    |
                           |                           |
                    review_transfer                    |
                    (all fields complete)              |
                           |                           |
                           v                           |
                    +-------------+       save tool    |
                    |  reviewing  | ───────────────────+
                    +------+------+
                           |
               +-----------+-----------+
               |                       |
        confirm_transfer          cancel_transfer
               |                       |
               v                       v
        +-------------+        +-------------+
        |  confirmed  |        |  cancelled  |
        +------+------+        +------+------+
               |                       |
        60s auto-submit           save tool called
               |                  (auto-clear ALL fields,
               v                   start fresh)
        +-------------+
        |  submitted  |
        +-------------+
               |
          save tool called
          (auto-clear ALL fields,
           start fresh)
```

### State Keys

| Key | Type | Description |
|-----|------|-------------|
| `transfer_country` | `str` | Validated country name (e.g., `"Mexico"`) |
| `transfer_country_code` | `str` | ISO country code (e.g., `"MX"`) |
| `transfer_amount` | `float` | Amount in USD |
| `transfer_currency` | `str` | Always `"USD"` (set by tools) |
| `transfer_beneficiary_name` | `str` | Title-cased full name (e.g., `"Maria Garcia"`) |
| `transfer_beneficiary_account` | `str` | Bank name/account or phone number |
| `transfer_delivery_method` | `str` | Method key: `"bank_deposit"` or `"mobile_wallet"` |
| `transfer_status` | `str` | Current state: `not_started`, `collecting`, `reviewing`, `confirmed`, `submitted`, `cancelled` |
| `transfer_missing_fields` | `list[str]` | Fields still needed (updated by every save tool) |
| `transfer_confirmed_at` | `float` | `time.time()` timestamp when confirmed (used by auto-submit) |
| `transfer_reference` | `str` | `TXN-XXXXXXXX` reference number assigned on confirm |

**Supplementary state** (not cleared by auto-clear):

| Key | Type | Description |
|-----|------|-------------|
| `transfer_history` | `list[dict]` | Append-only log of all transactions in the session (reference, country, amount, currency, beneficiary, delivery method, status, confirmed_at). Injected into system instruction by `_inject_transaction_history` callback. |

### Auto-Clear Mechanism

Four private helpers manage state transitions and transaction history:

**`_save_to_history(state, ref, status_override=None)`** — Called by `confirm_transfer`. Saves a snapshot of the current transfer (reference, country, amount, currency, beneficiary, delivery method, status) to the `transfer_history` list. Updates an existing entry if the reference already exists, otherwise appends.

**`_update_history_status(state, ref, new_status)`** — Called by `cancel_transfer`. Updates only the status field of an existing history entry (e.g., confirmed → cancelled).

**`_auto_clear_if_needed(tool_context)`** — Called at the start of every save tool. If `transfer_status` is `"confirmed"`, `"submitted"`, or `"cancelled"`, clears ALL 11 transfer keys to empty strings. This enables starting a fresh transfer after any terminal state, without needing a new session.

**`_finish(tool_context)`** — Called at the end of every save tool. If status is empty, `"not_started"`, or `"cancelled"`, sets it to `"collecting"`. Computes and stores `transfer_missing_fields`.

All three terminal states (`confirmed`, `submitted`, `cancelled`) trigger auto-clear on the next save tool call — there is no distinction between them. The `cancel_transfer` tool itself preserves fields in state (so the agent can show them), but the moment a new save tool runs, everything is wiped for a fresh start.

---

## 5. Submission Window

After `confirm_transfer`, the transfer enters a **60-second window** before final submission.

### Sequence Diagram

```
User (Browser)    Server (FastAPI)       Agent (LLM)         Session Store
 |                      |                     |                     |
 | POST /chat "confirm" |                     |                     |
 |─────────────────────>|                     |                     |
 |                      | run_async()         |                     |
 |                      |────────────────────>|                     |
 |                      |                     |                     |
 |                      |                     | confirm_transfer()  |
 |                      |                     |────────────────────>|
 |                      |                     |  status="confirmed" |
 |                      |                     |  confirmed_at=T     |
 |                      |                     |  ref=TXN-XXXXXXXX   |
 |                      |                     |                     |
 |                      | response events     |                     |
 |                      |<────────────────────|                     |
 |                      |                     |                     |
 | SSE token:           |                     |                     |
 | "confirmed, 60s"     |                     |                     |
 |<─────────────────────|                     |                     |
 |                      |                     |                     |
 |                      | fetch final state   |                     |
 |                      |────────────────────────────────────────-->|
 |                      |<──────────────────────────────────────────|
 |                      |                     |                     |
 | SSE state event      |                     |                     |
 | (confirmed, T)       |                     |                     |
 |<─────────────────────|                     |                     |
 |                      |                     |                     |
 |                      | asyncio.create_task |                     |
 |                      | (_auto_submit)      |                     |
 |                      |-------+             |                     |
 |                      |       | sleep(60)   |                     |
 |                      |       |             |                     |
 |                      |<------+             |                     |
 |                      |                     |                     |
 |                      | re-fetch session    |                     |
 |                      | status still        |                     |
 |                      | "confirmed" & T?    |                     |
 |                      |────────────────────────────────────────-->|
 |                      |   status="submitted"|                     |
 |                      |                     |                     |
```

### How it works

1. **`confirm_transfer`** sets `transfer_status = "confirmed"` and records `transfer_confirmed_at`.
2. The **chat endpoint** detects `status == "confirmed"` in the final SSE state event and launches `_auto_submit()` as a background task via `asyncio.create_task`.
3. **`_auto_submit()`** sleeps for `SUBMISSION_DELAY` (60 seconds), then:
   - Re-fetches the session from the session store.
   - Verifies status is still `"confirmed"` and `confirmed_at` matches (prevents double-submit if user cancelled and re-confirmed).
   - Appends a state delta event: `transfer_status = "submitted"`.
4. If the user **cancels** during the window, `cancel_transfer` sets status to `"cancelled"` and clears `confirmed_at`. When `_auto_submit` wakes up, the status check fails and it skips the transition.

---

## 6. Server & Web UI

### FastAPI Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Serves the chat UI (`index.html`) |
| `/favicon.ico` | GET | Returns 204 (no favicon) |
| `/health` | GET | Health check (`{"status": "ok"}`) |
| `/sessions` | POST | Create new session. Query param: `user_id` (default: `"default_user"`) |
| `/sessions/{id}/state` | GET | Get session state. Query param: `user_id` |
| `/chat` | POST | Send message, receive SSE stream. Body: `{user_id, session_id, message}` |

### SSE Streaming Flow

The `/chat` endpoint always returns an `EventSourceResponse` with three event types:

1. **`token`** — Agent text chunks, streamed as they're generated. Contains `{"text": "..."}`.
2. **`state`** — Full session state snapshot after the agent finishes. Contains `{"state": {...}}`.
3. **`done`** — Empty signal that the response is complete.

### Message Length Guard

Messages over `MAX_MESSAGE_LENGTH` (1000 characters) are intercepted server-side and replaced with a system hint:

```
[SYSTEM: The user sent a very long message. Do NOT process it.
Kindly ask them to rephrase with a shorter message, focusing only on
the information you still need for the transfer.]
```

This blocks long prompt injection payloads before they reach the LLM — no tokens consumed, no API cost.

### `_extract_text()` Event Filtering

The helper function filters ADK events to extract only user-visible text:
- Skips events containing `function_call` or `function_response` parts (tool invocations).
- Skips parts with `thought=True` (model reasoning/chain-of-thought).
- Returns only plain text parts concatenated.

Additionally, a special case detects `convert_currency` function calls and immediately streams a "checking conversion rate" message to the user, so they don't see a blank screen during the API call.

### Session Management

| Mode | Configuration | Persistence |
|------|--------------|-------------|
| **InMemory** (default) | No env var needed | Lost on server restart |
| **Database** | Set `DATABASE_URL` | PostgreSQL, SQLite, or MySQL |

The session service is initialized at startup via the FastAPI `lifespan` context manager. The `Runner` is created with the `root_agent` and shared across all requests.

---

## 7. Data Model

### Supported Countries

| Country | Code | Currency | Delivery Methods |
|---------|------|----------|-----------------|
| Mexico | MX | MXN | bank_deposit, mobile_wallet |
| Philippines | PH | PHP | bank_deposit, mobile_wallet |
| India | IN | INR | bank_deposit, mobile_wallet |
| Colombia | CO | COP | bank_deposit, mobile_wallet |
| Guatemala | GT | GTQ | bank_deposit, mobile_wallet |
| United Kingdom | GB | GBP | bank_deposit |
| Canada | CA | CAD | bank_deposit, mobile_wallet |
| Brazil | BR | BRL | bank_deposit, mobile_wallet |
| Nigeria | NG | NGN | bank_deposit, mobile_wallet |
| Kenya | KE | KES | mobile_wallet |

### Delivery Method Details

| Method | Name | Fee | ETA | Requirements |
|--------|------|-----|-----|-------------|
| `bank_deposit` | Bank Deposit | $3.99 | 1-2 business days | Beneficiary name, bank name, account number |
| `mobile_wallet` | Mobile Wallet | $1.99 | Instant to 1 hour | Beneficiary name, phone number |

### Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `MIN_AMOUNT` | 1.0 | Minimum transfer amount (USD) |
| `MAX_AMOUNT` | 10,000.0 | Maximum transfer amount (USD) |
| `DEFAULT_CURRENCY` | `"USD"` | Default currency for all transfers |
| `SUBMISSION_DELAY` | 60 | Seconds before confirmed → submitted |

### Required Fields

The 4 fields that must be set before `review_transfer` returns success:
- `transfer_country`
- `transfer_amount`
- `transfer_beneficiary_name`
- `transfer_delivery_method`

---

## 8. Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| **Single agent** over multi-agent routing | 1 LLM call per message. No routing overhead. Users don't follow linear paths — a single agent handles "Send $200 to Maria in Mexico" (all at once) naturally. |
| **8 granular tools** over a single consolidated tool | Each tool validates one concern. Enables true parallel execution — `save_country` + `save_amount` + `save_beneficiary` run simultaneously. Clearer error messages per field. |
| **Prompt-based guardrails** over regex callbacks | LLM handles nuance naturally. Regex was too strict (blocked "hello") or too loose (missed creative injections). |
| **Deterministic validation in tools** | Business rules (country list, amount limits, delivery method availability) enforced in Python, not LLM instructions. 100% reliable regardless of model. |
| **Server-side message length guard** (1000 chars) | Blocks prompt injection payloads before they reach the LLM. Fast, free, no tokens consumed. |
| **SSE streaming** | Better UX — users see text appearing immediately. Tool events filtered out to prevent duplicates. |
| **Flat state keys** (`transfer_*`) | Simple, inspectable, no nested structures. Easy to clear (`_auto_clear_if_needed` loops over a flat list). |
| **Uniform auto-clear on terminal states** | After any terminal state (confirmed, submitted, cancelled), the next save tool call clears all fields for a fresh start. Simpler, consistent behavior — no special cases for cancel vs. confirm. |
| **60-second submission window** | Gives users a chance to cancel after confirming. Server-side `asyncio` task handles the timeout independently of the LLM. |
| **`convert_currency` is stateless** | No `ToolContext` — pure function. Can run in parallel with any save tool without state conflicts. |

---

## 9. Scenario Walkthroughs

### Scenario A: Simple Transfer

**User**: "Send $200 to Maria Garcia in Mexico via bank deposit to Banco Itau / 12345"

```
User Message
     |
     v
+--------------------------------------------------+
| LLM decides to call 3 tools in  parallel:         |
|                                                   |
|   save_country("Mexico")     ──> success          |
|   save_amount(200)           ──> success          |
|   save_beneficiary(                               |
|     name="Maria Garcia",                          |
|     account="Banco Itau / 12345") ──> success     |
+--------------------------------------------------+
     |
     | (country now set, LLM calls sequentially)
     v
+--------------------------------------------------+
|   save_delivery_method("bank_deposit") ──> success|
+--------------------------------------------------+
     |
     | (all fields complete, LLM calls review)
     v
+--------------------------------------------------+
|   review_transfer() ──> success                   |
|   Summary: $200 to Maria Garcia in Mexico         |
|   via Bank Deposit, fee $3.99, total $203.99      |
+--------------------------------------------------+
     |
     | Agent shows summary, asks to confirm
     v
User: "Yes, confirm"
     |
     v
+--------------------------------------------------+
|   confirm_transfer() ──> success                  |
|   Status: confirmed                               |
|   Reference: TXN-XXXXXXXX                         |
+--------------------------------------------------+
     |
     | Server launches _auto_submit (60s timer)
     v
  [60 seconds later] → status = "submitted"
```

**State transitions**: `not_started` → `collecting` → `reviewing` → `confirmed` → `submitted`

---

### Scenario B: Currency Conversion + Correction

**User**: "Send 5000 reais to John Smith in Colombia"

```
User Message
     |
     v
+------------------------------------------------------------+
| LLM calls 3 tools in parallel:                             |
|                                                            |
|   convert_currency(5000, "BRL", "USD") ──> $947.51         |
|   save_country("Colombia")             ──> success         |
|   save_beneficiary(name="John Smith")  ──> success         |
+------------------------------------------------------------+
     |
     | LLM sees converted amount, calls save_amount
     v
+------------------------------------------------------------+
|   save_amount(947.51)  ──> success                          |
+------------------------------------------------------------+
     |
     | User provides delivery method
     v
User: "banco da colombia acc 123456-8"
     |
     v
+------------------------------------------------------------+
|   save_delivery_method("bank_deposit") ──> success          |
|   save_beneficiary(account="Banco da Colombia / 123456-8")  |
+------------------------------------------------------------+
     |
     v
+------------------------------------------------------------+
|   review_transfer() ──> success                             |
|   Summary: $947.51 to John Smith in Colombia                |
+------------------------------------------------------------+
     |
     | User confirms, then realizes wrong beneficiary
     v
User: "confirm" → confirm_transfer() → confirmed
     |
User: "that is maria gonzales account, cancel it"
     |
     v
+------------------------------------------------------------+
|   cancel_transfer() ──> success                             |
|   Status: cancelled (fields still in state for display)     |
+------------------------------------------------------------+
     |
User: "all the same, to maria"
     |
     v
+------------------------------------------------------------+
|   save_beneficiary(name="Maria Gonzales")                   |
|   _auto_clear_if_needed() fires (status was "cancelled")    |
|   → ALL fields cleared to ""                                |
|   → save_beneficiary sets name="Maria Gonzales"             |
|   Agent must re-collect: country, amount, delivery method   |
+------------------------------------------------------------+
     |
     | Agent infers from conversation context
     v
+------------------------------------------------------------+
|   save_country("Colombia"), save_amount(947.51)             |
|   save_delivery_method("bank_deposit")                      |
|   save_beneficiary(account="Banco da Colombia / 123456-8")  |
+------------------------------------------------------------+
     |
     v
+------------------------------------------------------------+
|   review_transfer() ──> success (re-review with new name)   |
|   confirm_transfer() ──> confirmed                          |
+------------------------------------------------------------+
```

**Key concepts shown**: parallel tool calls, currency conversion, cancel-and-modify flow, uniform auto-clear after cancellation (all fields wiped, agent re-collects from context).

---

### Scenario C: Multi-Transfer Session with Cancellation

Based on the real [chat history case](../send_money_agent/case_sample/chat_history_case.txt).

**Flow**: Send 5k reais to Colombia → cancel → resend to different beneficiary → auto-submit → blocked cancel → over-max rejection → fresh $1k transfer.

```
User: "send 5k reais to john smith in colombia"
     |
     v
  convert_currency(5000, BRL, USD) → $947.51
  save_country("Colombia")
  save_beneficiary(name="John Smith")
  save_amount(947.51)
     |
User: "banco da colombia acc 123456-8"
     |
     v
  save_delivery_method("bank_deposit")
  save_beneficiary(account="Banco da Colombia / 123456-8")
  review_transfer() → reviewing
     |
User: "confirm" → confirm_transfer()
     |                 status = "confirmed"
     |                 confirmed_at = T1
     |                 TXN-788943
     |
     |  [Server: _auto_submit(T1) launched — 60s timer]
     |
User: "cancel it, that's maria gonzales"
     |
     v
  cancel_transfer()
     status = "cancelled"
     confirmed_at = ""
     history: TXN-788943 → cancelled
     [_auto_submit(T1) will skip — confirmed_at mismatch]
     |
User: "all the same, to maria"
     |
     v
  save_beneficiary(name="Maria Gonzales")
     _auto_clear_if_needed() fires (status was "cancelled")
     → ALL fields cleared to ""
     → save_beneficiary sets name="Maria Gonzales"
     |
  [Agent re-collects from conversation context]
  save_country("Colombia"), save_amount(947.51)
  save_delivery_method("bank_deposit")
  save_beneficiary(account="Banco da Colombia / 123456-8")
  review_transfer() → "reviewing"
     |
User: "confirm" → confirm_transfer()
     |                 status = "confirmed"
     |                 confirmed_at = T2
     |                 TXN-725247
     |
     |  [Server: _auto_submit(T2) launched — 60s timer]
     |
     |  ... 65 seconds pass ...
     |
     |  [_auto_submit(T2) fires → status = "submitted"]
     |
User: "cancel, I want to send an extra 1k"
     |
     v
  cancel_transfer()
     → ERROR: "Transfer already submitted"
     |
User: "ok send 15k to the same person"
     |
     v
  save_amount(15000)
     _auto_clear_if_needed() fires (status was "submitted")
     → ALL fields cleared to ""
     → save_amount validates: $15,000 > $10,000 max
     → ERROR: "Maximum transfer amount is $10,000.00."
     amount NOT saved, status = "collecting"
     |
  Agent: "The maximum is $10,000. How much would you like to send?"
     |
User: "make it 1000"
     |
     v
  save_amount(1000) → success
     |
  [Agent infers: same beneficiary, same country, same method]
  save_country("Colombia"), save_beneficiary(name="Maria Gonzales",
    account="Banco da Colombia / 123456-8")
  save_delivery_method("bank_deposit")
     |
     v
  review_transfer() → confirm_transfer()
     status = "confirmed"
     TXN-174488
     |
     |  [Server: _auto_submit(T3) launched]
     |  ... 60s later → status = "submitted"
```

**Key concepts shown**:
- Complete state machine lifecycle across 3 transfers in one session
- Cancel within 60-second window (successful) vs. cancel after submission (blocked)
- Uniform auto-clear on all terminal states (confirmed, submitted, cancelled) — next save tool wipes everything
- Agent re-collects fields from conversation context after auto-clear
- Amount validation: over-max rejected with error, user must provide valid amount
- `_auto_submit` timestamp matching prevents stale timers from firing
- Transaction history tracks all references and statuses across the session
