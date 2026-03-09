# Testing Suite Documentation

> Comprehensive testing reference for the Send Money Agent.
>
> Back to [README](../README.md) | See also [Architecture](ARCHITECTURE.md)

---

## Quick Start

```bash
# Unit + data tests (no API key needed)
uv run pytest tests/test_tools.py tests/test_data.py -v

# Integration tests (requires API key)
RUN_INTEGRATION_TESTS=1 uv run pytest tests/test_scenarios.py -v

# All tests
RUN_INTEGRATION_TESTS=1 uv run pytest -v
```

---

## 1. Test Organization

### Directory Structure

```
tests/
  __init__.py
  test_tools.py       # 45 unit tests — tool logic with mocked ToolContext
  test_data.py        # 7 data tests — static data consistency
  test_scenarios.py   # 11 integration tests — real LLM + Runner
```

### pytest Configuration

From `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = [
    "tests/test_tools.py",
    "tests/test_data.py",
    "tests/test_scenarios.py",
]
```

- `asyncio_mode = "auto"` — async test functions are automatically detected and run with `pytest-asyncio`. No need for `@pytest.mark.asyncio` on every test (though the existing tests include it explicitly for clarity).
- `testpaths` — enforces execution order: tools → data → scenarios. Unit tests run first for fast feedback; integration tests run last.

### Dependencies

| File | Dependencies | API Key? |
|------|-------------|----------|
| `test_tools.py` | `pytest`, `unittest.mock` | No |
| `test_data.py` | `pytest` | No |
| `test_scenarios.py` | `pytest`, `pytest-asyncio`, `pytest-rerunfailures`, `google-adk`, LLM API | Yes |

---

## 2. Running Tests

### Unit + Data Tests (no API key)

```bash
# All unit and data tests
uv run pytest tests/test_tools.py tests/test_data.py -v

# Specific test class
uv run pytest tests/test_tools.py::TestSaveCountry -v

# Specific test method
uv run pytest tests/test_tools.py::TestSaveCountry::test_valid_country -v

# Data tests only
uv run pytest tests/test_data.py -v
```

### Integration Tests (requires API key)

```bash
# All integration tests
RUN_INTEGRATION_TESTS=1 uv run pytest tests/test_scenarios.py -v

# Single scenario
RUN_INTEGRATION_TESTS=1 uv run pytest tests/test_scenarios.py::test_scenario_1_happy_path -v
```

Integration tests are skipped by default unless `RUN_INTEGRATION_TESTS=1` is set. They require a valid `OPENAI_API_KEY` (or the appropriate key for the configured model).

### Test Counts

| File | Tests | Type |
|------|-------|------|
| `test_tools.py` | 45 | Unit |
| `test_data.py` | 7 | Data validation |
| `test_scenarios.py` | 11 | Integration |
| **Total** | **63** | |

---

## 3. Mock Strategy

### `make_ctx()` Helper

```python
def make_ctx(state: dict | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.state = state if state is not None else {}
    return ctx
```

Creates a `MagicMock` standing in for `ToolContext`, but with a **real `dict`** as `state`. This is the core testing pattern:

- **Why real dict?** Tools mutate `tool_context.state` directly (e.g., `tool_context.state["transfer_country"] = "Mexico"`). With a real dict, tests assert on **actual state mutations**, not on mock `.assert_called_with()` patterns. This catches real bugs in state management logic.

### `httpx.AsyncClient` Mocking

For `convert_currency` tests:

```python
with patch("send_money_agent.tools.httpx.AsyncClient") as mock_client:
    mock_instance = AsyncMock()
    mock_instance.get.return_value = mock_response
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    mock_client.return_value = mock_instance
```

The `async with httpx.AsyncClient()` context manager is fully mocked — both `__aenter__`/`__aexit__` and the `.get()` call.

### Integration Test Strategy

Integration tests use a **real `Runner`** with `InMemorySessionService` — no mocks:

```python
@pytest_asyncio.fixture
async def runner():
    session_service = InMemorySessionService()
    return Runner(agent=root_agent, app_name=APP_NAME, session_service=session_service)
```

**Testing philosophy**: Assert on **deterministic session state** set by tools, not on LLM response text (which is non-deterministic). Example:

```python
await send(runner, "user", sid, "Send $200 to Maria Garcia in Mexico")
s = await state(runner, "user", sid)
assert s.get("transfer_country") == "Mexico"    # deterministic: tool sets this
assert s.get("transfer_amount") == 200           # deterministic: tool sets this
```

---

## 4. Unit Tests — `test_tools.py` (45 tests)

### TestSaveCountry (10 tests)

| Test | Verifies |
|------|----------|
| `test_valid_country` | Valid country saves correctly, returns success with country info and available delivery methods |
| `test_case_insensitive` | Country matching is case-insensitive (`"mExIcO"` → `"Mexico"`) |
| `test_invalid_country` | Unsupported country returns error with supported list |
| `test_sets_default_currency` | Sets `transfer_currency` to `"USD"` when not already set |
| `test_preserves_existing_currency` | Does not overwrite existing currency (e.g., `"EUR"`) |
| `test_clears_delivery_method_on_country_change` | Changing country clears `transfer_delivery_method` |
| `test_keeps_delivery_method_when_same_country` | Re-saving same country preserves delivery method |
| `test_auto_clear_after_submitted` | After submitted, saving country clears ALL old transfer fields |
| `test_sets_collecting_status` | First save tool call sets status to `"collecting"` |
| `test_returns_missing_fields` | Returns list of fields still needed |

### TestSaveAmount (7 tests)

| Test | Verifies |
|------|----------|
| `test_valid_amount` | Valid amount saves correctly |
| `test_negative_amount` | Negative amounts rejected |
| `test_over_max` | Amount over $10,000 rejected |
| `test_below_min` | Amount below $1 rejected |
| `test_min_boundary` | Exactly $1 (MIN_AMOUNT) accepted |
| `test_max_boundary` | Exactly $10,000 (MAX_AMOUNT) accepted |
| `test_auto_clear_after_confirm` | After confirmed, saving amount clears all old fields |

### TestSaveDeliveryMethod (4 tests)

| Test | Verifies |
|------|----------|
| `test_valid` | Valid method for country saves correctly, returns name/fee/ETA |
| `test_invalid_for_country` | Method unavailable for country rejected (e.g., `bank_deposit` for Kenya) |
| `test_no_country_set` | Error when no country is set yet |
| `test_returns_eta` | Returns correct ETA and fee for mobile_wallet |

### TestSaveBeneficiary (7 tests)

| Test | Verifies |
|------|----------|
| `test_full_name` | Two-word name saves correctly |
| `test_single_word_rejected` | Single-word name rejected with "full name" error |
| `test_title_cased` | Name is title-cased (`"john locke"` → `"John Locke"`) |
| `test_account_saved` | Account-only save works |
| `test_name_and_account` | Both name and account save together |
| `test_no_fields_provided` | Error when neither name nor account provided |
| `test_single_word_name_with_account_partial_error` | `"partial_error"` when name invalid but account is valid — account saved, name rejected |

### TestReviewTransfer (3 tests)

| Test | Verifies |
|------|----------|
| `test_complete` | All fields present → status `"success"`, status set to `"reviewing"`, summary with all details |
| `test_incomplete` | Missing fields → status `"incomplete"`, missing list returned |
| `test_empty_state` | Empty state → `"incomplete"`, status shows `"not_started"` |

### TestConfirmTransfer (5 tests)

| Test | Verifies |
|------|----------|
| `test_valid_confirm` | Status `"reviewing"` → `"confirmed"`, reference number generated |
| `test_wrong_status` | Status `"collecting"` → error ("review first") |
| `test_no_status` | No status → error |
| `test_sets_confirmed_at` | `transfer_confirmed_at` is a float timestamp |
| `test_confirm_after_correction_during_review` | Correction during review (via `save_beneficiary`) doesn't break confirm flow |

### TestCancelTransfer (5 tests)

| Test | Verifies |
|------|----------|
| `test_cancel_preserves_details` | Status → `"cancelled"`, all transfer fields preserved |
| `test_auto_clear_after_cancel` | Starting a new transfer after cancel auto-clears all old fields (same as confirmed/submitted) |
| `test_cancel_blocked_after_submitted` | Cancel after `"submitted"` returns error, status unchanged |
| `test_cancel_clears_confirmed_at` | Cancel clears `transfer_confirmed_at` |
| `test_fresh_start_after_confirm` | Saving after `"confirmed"` auto-clears all fields (fresh start) |

### TestConvertCurrency (4 tests)

| Test | Verifies |
|------|----------|
| `test_same_currency` | Same from/to returns immediately (rate=1.0, no API call) |
| `test_successful_conversion` | Mocked API response → correct converted amount |
| `test_api_error` | API exception → error status with "unavailable" message |
| `test_case_insensitive` | Currency codes are case-insensitive (`"usd"` → `"USD"`) |

---

## 5. Data Validation Tests — `test_data.py` (7 tests)

| Test | Verifies |
|------|----------|
| `test_all_country_delivery_methods_have_details` | Every delivery method referenced by a country exists in `DELIVERY_METHOD_DETAILS` |
| `test_all_delivery_methods_used_by_at_least_one_country` | No orphan delivery methods in `DELIVERY_METHOD_DETAILS` |
| `test_required_fields_are_valid_state_keys` | All `REQUIRED_FIELDS` start with `transfer_` prefix |
| `test_countries_have_required_keys` | Every country has `code`, `currency`, and non-empty `delivery_methods` |
| `test_delivery_method_details_have_required_keys` | Every method has `name`, `fee` (non-negative), and `eta` |
| `test_amount_limits` | `MIN_AMOUNT > 0` and `MAX_AMOUNT > MIN_AMOUNT` |
| `test_submission_delay_is_positive` | `SUBMISSION_DELAY > 0` |

---

## 6. Integration Tests — `test_scenarios.py` (11 tests)

### Environment Setup

```bash
export OPENAI_API_KEY="sk-..."
export RUN_INTEGRATION_TESTS=1
```

Tests are skipped unless `RUN_INTEGRATION_TESTS=1`. They use the real LLM configured in `agent.py` (default: `openai/gpt-5-mini`).

### Fixtures and Helpers

| Name | Type | Purpose |
|------|------|---------|
| `runner` | `@pytest_asyncio.fixture` | Creates a `Runner` with `InMemorySessionService` |
| `send(runner, uid, sid, msg)` | async function | Sends a message and returns the agent's final response text |
| `state(runner, uid, sid)` | async function | Returns session state as a dict |
| `new_session(runner, uid)` | async function | Creates a new session and returns the session ID |
| `ensure_field(runner, uid, sid, field, value, msg)` | async function | Resilient field assertion — sends a follow-up if the LLM didn't save a field in the previous turn |

### Scenarios

| # | Test | Verifies |
|---|------|----------|
| 1 | `test_scenario_1_happy_path` | Full transfer flow: greeting → country+amount → beneficiary → delivery method → confirm → status=confirmed. Sends fields in separate turns for deterministic assertions. |
| 2 | `test_scenario_2_bulk_input` | Multiple fields from one message: "Send $200 to Maria Garcia in Mexico" saves country + amount + name. Marked `flaky(reruns=1)` — LLM may split tool calls across turns. |
| 3 | `test_scenario_3_country_change` | Changing country mid-flow clears delivery method |
| 4 | `test_scenario_4_amount_correction` | "Change the amount to $350" correctly updates amount |
| 5 | `test_scenario_5_ambiguity` | Vague request ("send money to my mom") does NOT save any state |
| 6a | `test_scenario_6_invalid_country` | Invalid country ("Antarctica") not saved |
| 6b | `test_scenario_6_invalid_amount` | Negative amount not saved |
| 7 | `test_scenario_7_cancellation` | "Cancel everything" sets status to cancelled |
| 8 | `test_scenario_8_session_resumption` | State persists across messages in the same session |
| 9 | `test_scenario_9_off_topic` | Off-topic message ("What's the weather?") doesn't change transfer state |
| 10 | `test_scenario_10_delivery_method_mismatch` | bank_deposit not saved for Kenya (mobile_wallet only). LLM may auto-correct to mobile_wallet. |

### Testing Philosophy

All integration assertions are on **deterministic session state**, never on LLM text:

```python
# GOOD: deterministic state assertion
assert s.get("transfer_country") == "Mexico"

# BAD (not used): non-deterministic text assertion
assert "Mexico" in response_text  # LLM phrasing varies
```

This makes tests reliable across different models and prompt variations.

### Handling LLM Non-Determinism

LLMs don't always call the same tools in the same turn. A message like "Send $200 to Maria Garcia in Mexico" may trigger 3 parallel tool calls in one run and only 2 in the next. The test suite handles this with two strategies:

- **Separate turns (Scenario 1)**: The happy path sends each piece of information in its own message. This makes assertions fully deterministic — each turn maps to exactly one tool call.
- **Flaky reruns (Scenario 2)**: The bulk input test keeps strict assertions (all fields saved in one turn) but allows one automatic retry via `@pytest.mark.flaky(reruns=1)`. This tests the parallel tool-calling capability while tolerating occasional LLM variation.
- **Flexible assertions (Scenario 10)**: When the LLM may reasonably auto-correct (e.g., selecting the only available delivery method instead of failing), the assertion tests the invariant (`!= "bank_deposit"`) rather than a specific expected value.

---

## 7. Coverage Areas

### What's Tested

- All 8 tool functions (every code path)
- Input validation (boundaries, invalid inputs, edge cases)
- State transitions (`not_started` → `collecting` → `reviewing` → `confirmed`, `cancelled`)
- Auto-clear after all terminal states (confirmed, submitted, cancelled)
- Country change side effects (delivery method cleared)
- Parallel tool execution correctness (no state conflicts)
- Currency conversion (success, error, same-currency, case-insensitive)
- Data consistency (country-method cross-references, required keys, limits)
- End-to-end flows via real LLM (happy path, corrections, cancellation, edge cases)

### What's Not Tested

- `app/server.py` (FastAPI endpoints, SSE streaming, `_auto_submit` background task, message length guard)
- `_extract_text()` event filtering
- Web UI (`index.html`)
- Database session service (only InMemory is tested)
- Docker/deployment configuration
- Concurrent session isolation
- `transfer_history` state management (`_save_to_history`, `_update_history_status`)
- `_inject_transaction_history` before_model_callback
