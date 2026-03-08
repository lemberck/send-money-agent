"""Unit tests for Send Money Agent tools with mocked ToolContext."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from send_money_agent.tools import (
    save_country,
    save_amount,
    save_delivery_method,
    save_beneficiary,
    review_transfer,
    confirm_transfer,
    cancel_transfer,
    convert_currency,
)
from send_money_agent.tools import _auto_clear_if_needed
from send_money_agent.data import MIN_AMOUNT, MAX_AMOUNT


def make_ctx(state: dict | None = None) -> MagicMock:
    """Create a mock ToolContext with a real dict as state."""
    ctx = MagicMock()
    ctx.state = state if state is not None else {}
    return ctx


# --- save_country ---

class TestSaveCountry:
    @pytest.mark.asyncio
    async def test_valid_country(self):
        ctx = make_ctx()
        result = await save_country(ctx, country="Mexico")
        assert result["status"] == "success"
        assert ctx.state["transfer_country"] == "Mexico"
        assert ctx.state["transfer_country_code"] == "MX"
        assert result["country"] == "Mexico"
        assert "bank_deposit" in result["available_delivery_methods"]

    @pytest.mark.asyncio
    async def test_case_insensitive(self):
        ctx = make_ctx()
        result = await save_country(ctx, country="mExIcO")
        assert result["status"] == "success"
        assert ctx.state["transfer_country"] == "Mexico"

    @pytest.mark.asyncio
    async def test_invalid_country(self):
        ctx = make_ctx()
        result = await save_country(ctx, country="Antarctica")
        assert result["status"] == "error"
        assert "not supported" in result["error"]

    @pytest.mark.asyncio
    async def test_sets_default_currency(self):
        ctx = make_ctx()
        await save_country(ctx, country="India")
        assert ctx.state["transfer_currency"] == "USD"

    @pytest.mark.asyncio
    async def test_preserves_existing_currency(self):
        ctx = make_ctx({"transfer_currency": "EUR"})
        await save_country(ctx, country="India")
        assert ctx.state["transfer_currency"] == "EUR"

    @pytest.mark.asyncio
    async def test_clears_delivery_method_on_country_change(self):
        ctx = make_ctx({
            "transfer_country": "Mexico",
            "transfer_country_code": "MX",
            "transfer_delivery_method": "bank_deposit",
        })
        result = await save_country(ctx, country="India")
        assert result["status"] == "success"
        assert ctx.state["transfer_country"] == "India"
        assert ctx.state["transfer_delivery_method"] == ""

    @pytest.mark.asyncio
    async def test_keeps_delivery_method_when_same_country(self):
        ctx = make_ctx({
            "transfer_country": "Mexico",
            "transfer_country_code": "MX",
            "transfer_delivery_method": "bank_deposit",
        })
        result = await save_country(ctx, country="Mexico")
        assert result["status"] == "success"
        assert ctx.state["transfer_delivery_method"] == "bank_deposit"

    @pytest.mark.asyncio
    async def test_auto_clear_after_submitted(self):
        ctx = make_ctx({
            "transfer_country": "Mexico",
            "transfer_amount": 200,
            "transfer_beneficiary_name": "Maria",
            "transfer_status": "submitted",
            "transfer_confirmed_at": 1234567890.0,
        })
        result = await save_country(ctx, country="India")
        assert result["status"] == "success"
        assert ctx.state["transfer_country"] == "India"
        assert ctx.state["transfer_amount"] == ""
        assert ctx.state["transfer_beneficiary_name"] == ""
        assert ctx.state["transfer_confirmed_at"] == ""

    @pytest.mark.asyncio
    async def test_sets_collecting_status(self):
        ctx = make_ctx()
        await save_country(ctx, country="Mexico")
        assert ctx.state["transfer_status"] == "collecting"

    @pytest.mark.asyncio
    async def test_returns_missing_fields(self):
        ctx = make_ctx()
        result = await save_country(ctx, country="Mexico")
        assert "transfer_amount" in result["missing_fields"]
        assert "transfer_beneficiary_name" in result["missing_fields"]


# --- save_amount ---

class TestSaveAmount:
    @pytest.mark.asyncio
    async def test_valid_amount(self):
        ctx = make_ctx()
        result = await save_amount(ctx, amount=200.0)
        assert result["status"] == "success"
        assert ctx.state["transfer_amount"] == 200.0

    @pytest.mark.asyncio
    async def test_negative_amount(self):
        ctx = make_ctx()
        result = await save_amount(ctx, amount=-50)
        assert result["status"] == "error"
        assert "greater than zero" in result["error"]

    @pytest.mark.asyncio
    async def test_over_max(self):
        ctx = make_ctx()
        result = await save_amount(ctx, amount=MAX_AMOUNT + 1)
        assert result["status"] == "error"
        assert "Maximum" in result["error"]

    @pytest.mark.asyncio
    async def test_below_min(self):
        ctx = make_ctx()
        result = await save_amount(ctx, amount=MIN_AMOUNT - 0.5)
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_min_boundary(self):
        ctx = make_ctx()
        result = await save_amount(ctx, amount=MIN_AMOUNT)
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_max_boundary(self):
        ctx = make_ctx()
        result = await save_amount(ctx, amount=MAX_AMOUNT)
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_auto_clear_after_confirm(self):
        ctx = make_ctx({
            "transfer_country": "Mexico",
            "transfer_amount": 200,
            "transfer_beneficiary_name": "Maria",
            "transfer_status": "confirmed",
        })
        result = await save_amount(ctx, amount=300)
        assert result["status"] == "success"
        assert ctx.state["transfer_amount"] == 300
        assert ctx.state["transfer_country"] == ""
        assert ctx.state["transfer_beneficiary_name"] == ""


# --- save_delivery_method ---

class TestSaveDeliveryMethod:
    @pytest.mark.asyncio
    async def test_valid(self):
        ctx = make_ctx({"transfer_country": "Mexico"})
        result = await save_delivery_method(ctx, delivery_method="bank_deposit")
        assert result["status"] == "success"
        assert ctx.state["transfer_delivery_method"] == "bank_deposit"
        assert result["delivery_method_name"] == "Bank Deposit"
        assert result["fee"] == 3.99

    @pytest.mark.asyncio
    async def test_invalid_for_country(self):
        ctx = make_ctx({"transfer_country": "Kenya"})
        result = await save_delivery_method(ctx, delivery_method="bank_deposit")
        assert result["status"] == "error"
        assert "not available" in result["error"]

    @pytest.mark.asyncio
    async def test_no_country_set(self):
        ctx = make_ctx()
        result = await save_delivery_method(ctx, delivery_method="bank_deposit")
        assert result["status"] == "error"
        assert "country" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_returns_eta(self):
        ctx = make_ctx({"transfer_country": "Brazil"})
        result = await save_delivery_method(ctx, delivery_method="mobile_wallet")
        assert result["status"] == "success"
        assert result["eta"] == "Instant to 1 hour"
        assert result["fee"] == 1.99


# --- save_beneficiary ---

class TestSaveBeneficiary:
    @pytest.mark.asyncio
    async def test_full_name(self):
        ctx = make_ctx()
        result = await save_beneficiary(ctx, name="Maria Garcia")
        assert result["status"] == "success"
        assert ctx.state["transfer_beneficiary_name"] == "Maria Garcia"

    @pytest.mark.asyncio
    async def test_single_word_rejected(self):
        ctx = make_ctx()
        result = await save_beneficiary(ctx, name="Maggie")
        assert result["status"] == "error"
        assert "full name" in result["errors"][0].lower()
        assert ctx.state.get("transfer_beneficiary_name", "") == ""

    @pytest.mark.asyncio
    async def test_title_cased(self):
        ctx = make_ctx()
        result = await save_beneficiary(ctx, name="john locke")
        assert result["status"] == "success"
        assert ctx.state["transfer_beneficiary_name"] == "John Locke"

    @pytest.mark.asyncio
    async def test_account_saved(self):
        ctx = make_ctx()
        result = await save_beneficiary(ctx, account="5521999999999")
        assert result["status"] == "success"
        assert ctx.state["transfer_beneficiary_account"] == "5521999999999"

    @pytest.mark.asyncio
    async def test_name_and_account(self):
        ctx = make_ctx()
        result = await save_beneficiary(ctx, name="Maria Garcia", account="Itau / 12345")
        assert result["status"] == "success"
        assert ctx.state["transfer_beneficiary_name"] == "Maria Garcia"
        assert ctx.state["transfer_beneficiary_account"] == "Itau / 12345"

    @pytest.mark.asyncio
    async def test_no_fields_provided(self):
        ctx = make_ctx()
        result = await save_beneficiary(ctx)
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_single_word_name_with_account_partial_error(self):
        ctx = make_ctx()
        result = await save_beneficiary(ctx, name="Maggie", account="12345")
        assert result["status"] == "partial_error"
        assert ctx.state["transfer_beneficiary_account"] == "12345"
        assert ctx.state.get("transfer_beneficiary_name", "") == ""


# --- review_transfer ---

class TestReviewTransfer:
    def test_complete(self):
        ctx = make_ctx({
            "transfer_country": "Mexico",
            "transfer_amount": 200,
            "transfer_currency": "USD",
            "transfer_beneficiary_name": "Maria Garcia",
            "transfer_delivery_method": "bank_deposit",
        })
        result = review_transfer(ctx)
        assert result["status"] == "success"
        assert result["summary"]["destination_country"] == "Mexico"
        assert ctx.state["transfer_status"] == "reviewing"

    def test_incomplete(self):
        ctx = make_ctx({"transfer_country": "Mexico"})
        result = review_transfer(ctx)
        assert result["status"] == "incomplete"
        assert len(result["missing"]) > 0

    def test_empty_state(self):
        ctx = make_ctx()
        result = review_transfer(ctx)
        assert result["status"] == "incomplete"
        assert result["transfer"]["status"] == "not_started"


# --- confirm_transfer ---

class TestConfirmTransfer:
    def test_valid_confirm(self):
        ctx = make_ctx({
            "transfer_status": "reviewing",
            "transfer_country": "Mexico",
            "transfer_amount": 200,
            "transfer_beneficiary_name": "Maria",
            "transfer_delivery_method": "bank_deposit",
        })
        result = confirm_transfer(ctx)
        assert result["status"] == "success"
        assert ctx.state["transfer_status"] == "confirmed"
        assert "reference_number" in result["confirmation_details"]

    def test_wrong_status(self):
        ctx = make_ctx({"transfer_status": "collecting"})
        result = confirm_transfer(ctx)
        assert result["status"] == "error"
        assert "review" in result["message"].lower()

    def test_no_status(self):
        ctx = make_ctx()
        result = confirm_transfer(ctx)
        assert result["status"] == "error"

    def test_sets_confirmed_at(self):
        ctx = make_ctx({
            "transfer_status": "reviewing",
            "transfer_country": "Mexico",
            "transfer_amount": 200,
            "transfer_beneficiary_name": "Maria",
            "transfer_delivery_method": "bank_deposit",
        })
        result = confirm_transfer(ctx)
        assert result["status"] == "success"
        assert isinstance(ctx.state["transfer_confirmed_at"], float)

    @pytest.mark.asyncio
    async def test_confirm_after_correction_during_review(self):
        """Correcting a field during review should NOT break confirm."""
        ctx = make_ctx({
            "transfer_status": "reviewing",
            "transfer_country": "Mexico",
            "transfer_country_code": "MX",
            "transfer_amount": 200,
            "transfer_currency": "USD",
            "transfer_beneficiary_name": "Maria Garcia",
            "transfer_delivery_method": "bank_deposit",
        })
        await save_beneficiary(ctx, name="Ana Lopez")
        assert ctx.state["transfer_status"] == "reviewing"
        assert ctx.state["transfer_beneficiary_name"] == "Ana Lopez"
        result = confirm_transfer(ctx)
        assert result["status"] == "success"
        assert ctx.state["transfer_status"] == "confirmed"


# --- cancel_transfer ---

class TestCancelTransfer:
    def test_cancel_preserves_details(self):
        ctx = make_ctx({
            "transfer_country": "Mexico",
            "transfer_amount": 200,
            "transfer_beneficiary_name": "Maria",
            "transfer_delivery_method": "bank_deposit",
            "transfer_status": "reviewing",
        })
        result = cancel_transfer(ctx)
        assert result["status"] == "success"
        assert ctx.state["transfer_status"] == "cancelled"
        assert ctx.state["transfer_country"] == "Mexico"
        assert ctx.state["transfer_amount"] == 200
        assert ctx.state["transfer_beneficiary_name"] == "Maria"
        assert ctx.state["transfer_delivery_method"] == "bank_deposit"

    @pytest.mark.asyncio
    async def test_auto_clear_after_cancel(self):
        """Starting a new transfer after cancel auto-clears old fields (same as confirmed/submitted)."""
        ctx = make_ctx({
            "transfer_country": "Brazil",
            "transfer_country_code": "BR",
            "transfer_amount": 947.51,
            "transfer_currency": "USD",
            "transfer_beneficiary_name": "Ana Gonzales",
            "transfer_beneficiary_account": "55218909745556",
            "transfer_delivery_method": "mobile_wallet",
            "transfer_status": "cancelled",
        })
        result = await save_amount(ctx, amount=953.25)
        assert result["status"] == "success"
        assert ctx.state["transfer_amount"] == 953.25
        # Auto-clear should have wiped all old fields
        assert ctx.state["transfer_country"] == ""
        assert ctx.state["transfer_beneficiary_name"] == ""
        assert ctx.state["transfer_delivery_method"] == ""
        assert ctx.state["transfer_status"] == "collecting"

    def test_cancel_blocked_after_submitted(self):
        ctx = make_ctx({
            "transfer_status": "submitted",
            "transfer_country": "Mexico",
            "transfer_amount": 200,
        })
        result = cancel_transfer(ctx)
        assert result["status"] == "error"
        assert "already submitted" in result["message"].lower()
        assert ctx.state["transfer_status"] == "submitted"

    def test_cancel_clears_confirmed_at(self):
        ctx = make_ctx({
            "transfer_status": "confirmed",
            "transfer_confirmed_at": 1234567890.0,
            "transfer_country": "Mexico",
            "transfer_amount": 200,
        })
        result = cancel_transfer(ctx)
        assert result["status"] == "success"
        assert ctx.state["transfer_confirmed_at"] == ""
        assert ctx.state["transfer_status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_fresh_start_after_confirm(self):
        """Starting a new transfer after confirm auto-clears old fields."""
        ctx = make_ctx({
            "transfer_country": "Mexico",
            "transfer_country_code": "MX",
            "transfer_amount": 200,
            "transfer_beneficiary_name": "Maria",
            "transfer_delivery_method": "bank_deposit",
            "transfer_status": "confirmed",
        })
        result = await save_country(ctx, country="India")
        assert result["status"] == "success"
        assert ctx.state["transfer_country"] == "India"
        assert ctx.state["transfer_amount"] == ""
        assert ctx.state["transfer_beneficiary_name"] == ""
        assert ctx.state["transfer_delivery_method"] == ""


# --- convert_currency ---

class TestConvertCurrency:
    @pytest.mark.asyncio
    async def test_same_currency(self):
        result = await convert_currency(100, "USD", "USD")
        assert result["status"] == "success"
        assert result["converted"] == 100
        assert result["rate"] == 1.0

    @pytest.mark.asyncio
    async def test_successful_conversion(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"rates": {"USD": 108.50}}
        mock_response.raise_for_status = MagicMock()

        with patch("send_money_agent.tools.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await convert_currency(100, "EUR", "USD")
            assert result["status"] == "success"
            assert result["converted"] == 108.50

    @pytest.mark.asyncio
    async def test_api_error(self):
        import httpx as httpx_mod
        with patch("send_money_agent.tools.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.side_effect = Exception("Connection failed")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await convert_currency(100, "XYZ", "USD")
            assert result["status"] == "error"
            assert "unavailable" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_case_insensitive(self):
        result = await convert_currency(100, "usd", "USD")
        assert result["status"] == "success"
        assert result["converted"] == 100
