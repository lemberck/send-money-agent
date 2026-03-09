"""Integration tests for all 10 verification scenarios.

These tests require a valid OPENAI_API_KEY and call the real LLM.
They are skipped by default unless RUN_INTEGRATION_TESTS=1 is set.

Testing approach: assert on deterministic session STATE set by tools,
not on LLM response text (which is non-deterministic).
"""

import os
import pytest
import pytest_asyncio

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from send_money_agent.agent import root_agent

SKIP_INTEGRATION = os.environ.get("RUN_INTEGRATION_TESTS", "0") != "1"
pytestmark = pytest.mark.skipif(SKIP_INTEGRATION, reason="Set RUN_INTEGRATION_TESTS=1 to run")

APP_NAME = "send_money_test"


@pytest_asyncio.fixture
async def runner():
    session_service = InMemorySessionService()
    return Runner(
        agent=root_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )


async def send(runner, uid, sid, msg):
    """Send a message and return the agent's response text."""
    parts = []
    async for event in runner.run_async(
        user_id=uid, session_id=sid,
        new_message=types.Content(role="user", parts=[types.Part(text=msg)]),
    ):
        if event.is_final_response() and event.content and event.content.parts:
            text = " ".join(p.text for p in event.content.parts if p.text)
            if text:
                parts.append(text)
    return " ".join(parts)


async def state(runner, uid, sid):
    """Get session state as a dict."""
    s = await runner.session_service.get_session(app_name=APP_NAME, user_id=uid, session_id=sid)
    return dict(s.state) if s else {}


async def new_session(runner, uid="user"):
    s = await runner.session_service.create_session(app_name=APP_NAME, user_id=uid)
    return s.id


# -- Scenario 1: Happy Path (full transfer flow) --
# Sends fields in separate turns for deterministic assertions.

@pytest.mark.asyncio
async def test_scenario_1_happy_path(runner):
    sid = await new_session(runner)

    resp = await send(runner, "user", sid, "Hi, I'd like to send money")
    assert len(resp) > 0

    await send(runner, "user", sid, "I want to send $200 to Mexico")
    s = await state(runner, "user", sid)
    assert s.get("transfer_country") == "Mexico"
    assert s.get("transfer_amount") == 200

    await send(runner, "user", sid, "The beneficiary is Maria Garcia")
    s = await state(runner, "user", sid)
    assert s.get("transfer_beneficiary_name") == "Maria Garcia"

    await send(runner, "user", sid, "Bank deposit to Banco do Brasil / 12345-6")
    s = await state(runner, "user", sid)
    assert s.get("transfer_delivery_method") == "bank_deposit"
    assert s.get("transfer_beneficiary_account")

    await send(runner, "user", sid, "Yes, confirm")
    s = await state(runner, "user", sid)
    assert s.get("transfer_status") == "confirmed"


# -- Scenario 2: Bulk Input (multiple fields in one message) --
# Strict assertion — LLM must save all fields in one turn. Allows one retry.

@pytest.mark.flaky(reruns=1)
@pytest.mark.asyncio
async def test_scenario_2_bulk_input(runner):
    sid = await new_session(runner)

    await send(runner, "user", sid, "Send $200 to Maria Garcia in Mexico")
    s = await state(runner, "user", sid)
    assert s.get("transfer_country") == "Mexico"
    assert s.get("transfer_amount") == 200
    assert s.get("transfer_beneficiary_name") == "Maria Garcia"


# -- Scenario 3: Country Change Mid-Flow --

@pytest.mark.asyncio
async def test_scenario_3_country_change(runner):
    sid = await new_session(runner)

    await send(runner, "user", sid, "Send $200 to Maria Garcia in Mexico")
    await send(runner, "user", sid, "Bank deposit to Itau / 99887766")
    s = await state(runner, "user", sid)
    assert s.get("transfer_country") == "Mexico"
    assert s.get("transfer_delivery_method") == "bank_deposit"

    await send(runner, "user", sid, "Actually, change the country to India")
    s = await state(runner, "user", sid)
    assert s.get("transfer_country") == "India"
    # Delivery method should be cleared since it's country-dependent
    assert s.get("transfer_delivery_method") in ("", None)


# -- Scenario 4: Amount Correction --

@pytest.mark.asyncio
async def test_scenario_4_amount_correction(runner):
    sid = await new_session(runner)

    await send(runner, "user", sid, "Send $200 to Maria Garcia in Mexico")
    await send(runner, "user", sid, "Bank deposit")
    await send(runner, "user", sid, "Change the amount to $350")
    s = await state(runner, "user", sid)
    assert s.get("transfer_amount") == 350


# -- Scenario 5: Ambiguity (vague request should NOT save state) --

@pytest.mark.asyncio
async def test_scenario_5_ambiguity(runner):
    sid = await new_session(runner)

    await send(runner, "user", sid, "Send money to my mom")
    s = await state(runner, "user", sid)
    # Agent should NOT have saved any transfer fields from this vague request
    assert not s.get("transfer_country")
    assert not s.get("transfer_amount")


# -- Scenario 6: Invalid Data --

@pytest.mark.asyncio
async def test_scenario_6_invalid_country(runner):
    sid = await new_session(runner)

    await send(runner, "user", sid, "Send money to Antarctica")
    s = await state(runner, "user", sid)
    # Country should NOT be saved (Antarctica is invalid)
    assert not s.get("transfer_country")


@pytest.mark.asyncio
async def test_scenario_6_invalid_amount(runner):
    sid = await new_session(runner)

    await send(runner, "user", sid, "Send money to Mexico")
    await send(runner, "user", sid, "Send -$50")
    s = await state(runner, "user", sid)
    assert s.get("transfer_country") == "Mexico"
    # Negative amount should NOT be saved
    assert not s.get("transfer_amount")


# -- Scenario 7: Cancellation --

@pytest.mark.asyncio
async def test_scenario_7_cancellation(runner):
    sid = await new_session(runner)

    await send(runner, "user", sid, "Send $200 to Maria Garcia in Mexico")
    await send(runner, "user", sid, "Cancel everything")
    s = await state(runner, "user", sid)
    assert s.get("transfer_status") == "cancelled"


# -- Scenario 8: Session Resumption --

@pytest.mark.asyncio
async def test_scenario_8_session_resumption(runner):
    sid = await new_session(runner)

    await send(runner, "user", sid, "Send $200 to Maria Garcia in Mexico")
    s = await state(runner, "user", sid)
    assert s.get("transfer_country") == "Mexico"
    assert s.get("transfer_amount") == 200

    # "Resume" — same session, new message. State should persist.
    await send(runner, "user", sid, "Hi, I'm back")
    s = await state(runner, "user", sid)
    assert s.get("transfer_country") == "Mexico"
    assert s.get("transfer_amount") == 200


# -- Scenario 9: Off-Topic (state should not change) --

@pytest.mark.asyncio
async def test_scenario_9_off_topic(runner):
    sid = await new_session(runner)

    await send(runner, "user", sid, "I want to send $500 to Maria Garcia in Mexico")
    s_before = await state(runner, "user", sid)

    await send(runner, "user", sid, "What's the weather like today?")
    s_after = await state(runner, "user", sid)
    # Off-topic message should not change transfer state
    assert s_after.get("transfer_country") == s_before.get("transfer_country")
    assert s_after.get("transfer_amount") == s_before.get("transfer_amount")


# -- Scenario 10: Delivery Method Mismatch --

@pytest.mark.asyncio
async def test_scenario_10_delivery_method_mismatch(runner):
    sid = await new_session(runner)

    await send(runner, "user", sid, "Send $200 to Maria Garcia in Kenya")
    s = await state(runner, "user", sid)
    assert s.get("transfer_country") == "Kenya"

    # Kenya only supports mobile_wallet — bank_deposit should be rejected
    await send(runner, "user", sid, "I want bank deposit")
    s = await state(runner, "user", sid)
    # bank_deposit must NOT be saved (unavailable for Kenya)
    assert s.get("transfer_delivery_method") != "bank_deposit"
