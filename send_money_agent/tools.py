"""Tools for the Send Money Agent."""

import time
import uuid

import httpx
from loguru import logger
from google.adk.tools.tool_context import ToolContext
from .data import (
    SUPPORTED_COUNTRIES,
    DELIVERY_METHOD_DETAILS,
    MIN_AMOUNT,
    MAX_AMOUNT,
    DEFAULT_CURRENCY,
    REQUIRED_FIELDS,
)


# --- Private helpers (not registered as tools) ---

_TRANSFER_KEYS = [
    "transfer_country", "transfer_country_code", "transfer_amount",
    "transfer_currency", "transfer_beneficiary_name", "transfer_beneficiary_account",
    "transfer_delivery_method", "transfer_status", "transfer_missing_fields",
    "transfer_confirmed_at", "transfer_reference",
]


def _save_to_history(state, ref, status_override=None):
    """Save or update a transaction record in transfer_history."""
    if not ref:
        return
    history = list(state.get("transfer_history") or [])
    entry = {
        "reference": ref,
        "country": state.get("transfer_country", ""),
        "amount": state.get("transfer_amount", 0),
        "currency": state.get("transfer_currency", "USD"),
        "beneficiary_name": state.get("transfer_beneficiary_name", ""),
        "beneficiary_account": state.get("transfer_beneficiary_account", ""),
        "delivery_method": state.get("transfer_delivery_method", ""),
        "status": status_override or state.get("transfer_status", ""),
        "confirmed_at": state.get("transfer_confirmed_at", ""),
    }
    # Update existing or append new
    for i, existing in enumerate(history):
        if existing.get("reference") == ref:
            history[i] = entry
            state["transfer_history"] = history
            return
    history.append(entry)
    state["transfer_history"] = history


def _update_history_status(state, ref, new_status):
    """Update only the status of an existing history entry."""
    if not ref:
        return
    history = list(state.get("transfer_history") or [])
    for entry in history:
        if entry.get("reference") == ref:
            entry["status"] = new_status
            state["transfer_history"] = history
            return


def _auto_clear_if_needed(tool_context: ToolContext):
    """Clear all transfer fields when starting fresh after a completed/cancelled transfer."""
    if tool_context.state.get("transfer_status") in ("confirmed", "submitted", "cancelled"):
        for key in _TRANSFER_KEYS:
            tool_context.state[key] = ""


def _finish(tool_context: ToolContext) -> list[str]:
    """Set status to collecting and return missing fields."""
    status = tool_context.state.get("transfer_status")
    if not status or status in ("not_started", "cancelled"):
        tool_context.state["transfer_status"] = "collecting"
    missing = [f for f in REQUIRED_FIELDS if not tool_context.state.get(f)]
    tool_context.state["transfer_missing_fields"] = missing
    return missing


# --- Save tools (async for parallel execution) ---

async def save_country(tool_context: ToolContext, country: str) -> dict:
    """Validate and save the destination country. Optimized for parallel execution.
    Changing country clears any previously set delivery method.

    Args:
        country: Destination country name (e.g., 'Mexico', 'India', 'Brazil').
    """
    try:
        _auto_clear_if_needed(tool_context)

        matched = None
        for name, info in SUPPORTED_COUNTRIES.items():
            if name.lower() == country.lower():
                matched = (name, info)
                break

        if not matched:
            supported_list = ", ".join(sorted(SUPPORTED_COUNTRIES.keys()))
            missing = _finish(tool_context)
            return {"status": "error", "error": f"'{country}' is not supported. Supported: {supported_list}.", "missing_fields": missing}

        name, info = matched

        # Clear delivery method if country is changing
        old_country = tool_context.state.get("transfer_country")
        if old_country and old_country != name:
            tool_context.state["transfer_delivery_method"] = ""

        tool_context.state["transfer_country"] = name
        tool_context.state["transfer_country_code"] = info["code"]
        if not tool_context.state.get("transfer_currency"):
            tool_context.state["transfer_currency"] = DEFAULT_CURRENCY

        missing = _finish(tool_context)
        logger.info("save_country: country={} validated", name)
        return {
            "status": "success",
            "country": name,
            "country_code": info["code"],
            "local_currency": info["currency"],
            "available_delivery_methods": info["delivery_methods"],
            "missing_fields": missing,
        }
    except Exception as e:
        logger.error("save_country unexpected error: {}", e)
        return {"status": "error", "error": f"Failed to save country: {e}"}


async def save_amount(tool_context: ToolContext, amount: float) -> dict:
    """Validate and save the transfer amount in USD. Optimized for parallel execution.
    Limits: $1.00 min, $10,000.00 max.

    Args:
        amount: Amount to send in USD.
    """
    try:
        _auto_clear_if_needed(tool_context)

        if amount <= 0:
            missing = _finish(tool_context)
            return {"status": "error", "error": "Amount must be greater than zero.", "missing_fields": missing}
        if amount < MIN_AMOUNT:
            missing = _finish(tool_context)
            return {"status": "error", "error": f"Minimum transfer amount is ${MIN_AMOUNT:.2f}.", "missing_fields": missing}
        if amount > MAX_AMOUNT:
            missing = _finish(tool_context)
            return {"status": "error", "error": f"Maximum transfer amount is ${MAX_AMOUNT:,.2f}.", "missing_fields": missing}

        tool_context.state["transfer_amount"] = amount
        if not tool_context.state.get("transfer_currency"):
            tool_context.state["transfer_currency"] = DEFAULT_CURRENCY

        missing = _finish(tool_context)
        logger.info("save_amount: amount=${:,.2f} validated", amount)
        return {"status": "success", "amount": amount, "missing_fields": missing}
    except Exception as e:
        logger.error("save_amount unexpected error: {}", e)
        return {"status": "error", "error": f"Failed to save amount: {e}"}


async def save_delivery_method(tool_context: ToolContext, delivery_method: str) -> dict:
    """Validate and save the delivery method. Country must be set first.
    Valid methods depend on country (e.g., 'bank_deposit', 'mobile_wallet').

    Args:
        delivery_method: Delivery method key (e.g., 'bank_deposit', 'mobile_wallet').
    """
    try:
        _auto_clear_if_needed(tool_context)

        active_country = tool_context.state.get("transfer_country")
        if not active_country:
            missing = _finish(tool_context)
            return {"status": "error", "error": "Please select a destination country before choosing a delivery method.", "missing_fields": missing}

        country_info = SUPPORTED_COUNTRIES.get(active_country, {})
        available = country_info.get("delivery_methods", [])
        if delivery_method not in available:
            missing = _finish(tool_context)
            return {"status": "error", "error": f"'{delivery_method}' is not available for {active_country}. Available: {', '.join(available)}.", "missing_fields": missing}

        detail = DELIVERY_METHOD_DETAILS.get(delivery_method, {})
        tool_context.state["transfer_delivery_method"] = delivery_method

        missing = _finish(tool_context)
        logger.info("save_delivery_method: delivery_method={} validated for {}", delivery_method, active_country)
        return {
            "status": "success",
            "delivery_method": delivery_method,
            "delivery_method_name": detail.get("name", delivery_method),
            "fee": detail.get("fee", 0),
            "eta": detail.get("eta", "Unknown"),
            "missing_fields": missing,
        }
    except Exception as e:
        logger.error("save_delivery_method unexpected error: {}", e)
        return {"status": "error", "error": f"Failed to save delivery method: {e}"}


async def save_beneficiary(tool_context: ToolContext, name: str = "", account: str = "") -> dict:
    """Save beneficiary name and/or account. Optimized for parallel execution.
    Name must be a full name (first and last, at least two words).
    For bank_deposit, account should be 'BankName / AccountNumber'.
    For mobile_wallet, account is the phone number.

    Args:
        name: Beneficiary's full name (e.g., 'Maria Garcia').
        account: Account number, phone, or 'BankName / AccountNumber'.
    """
    try:
        _auto_clear_if_needed(tool_context)

        saved = {}
        errors = []

        if name:
            name = name.strip()
            if len(name.split()) < 2:
                errors.append(f"'{name}' is not a complete name. Please provide the beneficiary's full name (first and last).")
            else:
                name = name.title()
                tool_context.state["transfer_beneficiary_name"] = name
                saved["beneficiary_name"] = name

        if account:
            tool_context.state["transfer_beneficiary_account"] = account
            saved["beneficiary_account"] = account

        if not saved and not errors:
            missing = _finish(tool_context)
            return {"status": "error", "error": "No beneficiary details provided.", "missing_fields": missing}

        missing = _finish(tool_context)

        if errors:
            return {"status": "error" if not saved else "partial_error", "saved": saved, "errors": errors, "missing_fields": missing}

        logger.info("save_beneficiary: saved={}", saved)
        return {"status": "success", "saved": saved, "missing_fields": missing}
    except Exception as e:
        logger.error("save_beneficiary unexpected error: {}", e)
        return {"status": "error", "error": f"Failed to save beneficiary: {e}"}


# --- Transfer lifecycle tools ---

def review_transfer(tool_context: ToolContext) -> dict:
    """Get the current transfer state and generate a summary for review. Sets status to 'reviewing' if all fields are complete."""
    try:
        state = tool_context.state
        country = state.get("transfer_country", "")
        amount = state.get("transfer_amount", 0)
        currency = state.get("transfer_currency", DEFAULT_CURRENCY)
        beneficiary = state.get("transfer_beneficiary_name", "")
        account = state.get("transfer_beneficiary_account", "")
        method_key = state.get("transfer_delivery_method", "")
        status = state.get("transfer_status", "not_started")

        current = {
            "country": country,
            "amount": amount,
            "currency": currency,
            "beneficiary_name": beneficiary,
            "beneficiary_account": account or "Not provided",
            "delivery_method": method_key,
            "status": status,
        }

        missing = []
        if not country:
            missing.append("destination country")
        if not amount:
            missing.append("amount")
        if not beneficiary:
            missing.append("beneficiary name")
        if not method_key:
            missing.append("delivery method")

        if missing:
            logger.info("review_transfer: incomplete, missing={}", missing)
            return {"status": "incomplete", "transfer": current, "missing": missing}

        method_detail = DELIVERY_METHOD_DETAILS.get(method_key, {})
        fee = method_detail.get("fee", 0)
        country_info = SUPPORTED_COUNTRIES.get(country, {})

        tool_context.state["transfer_status"] = "reviewing"

        logger.info("review_transfer: ${:,.2f} to {} via {}", amount, country, method_key)
        return {
            "status": "success",
            "summary": {
                "destination_country": country,
                "send_amount": f"${amount:,.2f} {currency}",
                "beneficiary_name": beneficiary,
                "beneficiary_account": account or "Not provided",
                "delivery_method": method_detail.get("name", method_key),
                "fee": f"${fee:.2f}",
                "total_cost": f"${amount + fee:,.2f} {currency}",
                "estimated_delivery": method_detail.get("eta", "Unknown"),
            },
        }
    except Exception as e:
        logger.error("review_transfer unexpected error: {}", e)
        return {"status": "error", "error": f"Failed to review transfer: {e}"}


def confirm_transfer(tool_context: ToolContext) -> dict:
    """Confirm and submit the transfer for processing."""
    try:
        state = tool_context.state
        if state.get("transfer_status") != "reviewing":
            return {"status": "error", "message": "Please review the transfer summary before confirming."}

        required = ["transfer_country", "transfer_amount", "transfer_beneficiary_name", "transfer_delivery_method"]
        missing = [f for f in required if not state.get(f)]
        if missing:
            return {"status": "error", "message": f"Cannot confirm. Missing: {', '.join(missing)}."}

        ref = f"TXN-{uuid.uuid4().hex[:8].upper()}"
        state["transfer_status"] = "confirmed"
        state["transfer_confirmed_at"] = time.time()
        state["transfer_reference"] = ref
        _save_to_history(state, ref)

        logger.info("confirm_transfer: ${} to {} confirmed", state.get("transfer_amount"), state.get("transfer_country"))
        return {
            "status": "success",
            "message": "Transfer confirmed! It will be automatically submitted in 60 seconds.",
            "confirmation_details": {
                "country": state.get("transfer_country"),
                "amount": state.get("transfer_amount"),
                "beneficiary": state.get("transfer_beneficiary_name"),
                "delivery_method": state.get("transfer_delivery_method"),
                "reference_number": ref,
            },
        }
    except Exception as e:
        logger.error("confirm_transfer unexpected error: {}", e)
        return {"status": "error", "error": f"Failed to confirm transfer: {e}"}


def cancel_transfer(tool_context: ToolContext) -> dict:
    """Cancel the current transfer. Details are kept visible for reference."""
    try:
        if tool_context.state.get("transfer_status") == "submitted":
            return {"status": "error", "message": "Transfer already submitted. Cancellation is no longer available."}

        ref = tool_context.state.get("transfer_reference", "")
        tool_context.state["transfer_status"] = "cancelled"
        tool_context.state["transfer_confirmed_at"] = ""
        _update_history_status(tool_context.state, ref, "cancelled")

        logger.info("cancel_transfer: transfer cancelled, details preserved")
        return {"status": "success", "message": "Transfer cancelled. Details preserved for reference. You can start a new transfer anytime."}
    except Exception as e:
        logger.error("cancel_transfer unexpected error: {}", e)
        return {"status": "error", "error": f"Failed to cancel transfer: {e}"}



async def convert_currency(amount: float, from_currency: str, to_currency: str = "USD") -> dict:
    """Convert an amount between currencies using live exchange rates.

    Args:
        amount: The amount to convert.
        from_currency: Source currency code (e.g., 'EUR', 'GBP', 'MXN').
        to_currency: Target currency code (default: 'USD').
    """
    from_currency = from_currency.upper().strip()
    to_currency = to_currency.upper().strip()

    if from_currency == to_currency:
        return {"status": "success", "from": from_currency, "to": to_currency, "amount": amount, "converted": amount, "rate": 1.0}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.frankfurter.dev/v1/latest",
                params={"amount": amount, "from": from_currency, "to": to_currency},
            )
            resp.raise_for_status()
            data = resp.json()

        converted = data["rates"][to_currency]
        rate = converted / amount if amount else 0

        logger.info("convert_currency: {:.2f} {} = {:.2f} {} (rate={:.4f})", amount, from_currency, converted, to_currency, rate)
        return {
            "status": "success",
            "from": from_currency,
            "to": to_currency,
            "amount": amount,
            "converted": round(converted, 2),
            "rate": round(rate, 6),
        }
    except httpx.HTTPStatusError as e:
        logger.error("convert_currency HTTP error: {}", e)
        return {"status": "error", "message": f"Currency conversion failed: {e.response.status_code}. Check that '{from_currency}' and '{to_currency}' are valid currency codes."}
    except Exception as e:
        logger.error("convert_currency error: {}", e)
        return {"status": "error", "message": f"Currency conversion unavailable: {e}"}
