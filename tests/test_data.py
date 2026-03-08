"""Tests for data consistency."""

from send_money_agent.data import (
    SUPPORTED_COUNTRIES,
    DELIVERY_METHOD_DETAILS,
    REQUIRED_FIELDS,
    MIN_AMOUNT,
    MAX_AMOUNT,
    SUBMISSION_DELAY,
)


def test_all_country_delivery_methods_have_details():
    """Every delivery method referenced by a country must exist in DELIVERY_METHOD_DETAILS."""
    for country, info in SUPPORTED_COUNTRIES.items():
        for method in info["delivery_methods"]:
            assert method in DELIVERY_METHOD_DETAILS, (
                f"Country '{country}' references delivery method '{method}' "
                f"which is not in DELIVERY_METHOD_DETAILS"
            )


def test_all_delivery_methods_used_by_at_least_one_country():
    """Every delivery method in DELIVERY_METHOD_DETAILS should be used by at least one country."""
    used_methods = set()
    for info in SUPPORTED_COUNTRIES.values():
        used_methods.update(info["delivery_methods"])

    for method in DELIVERY_METHOD_DETAILS:
        assert method in used_methods, (
            f"Delivery method '{method}' in DELIVERY_METHOD_DETAILS "
            f"is not used by any country"
        )


def test_required_fields_are_valid_state_keys():
    """REQUIRED_FIELDS should all be plausible state keys."""
    valid_prefixes = ("transfer_",)
    for field in REQUIRED_FIELDS:
        assert any(field.startswith(p) for p in valid_prefixes), (
            f"Required field '{field}' doesn't start with a valid prefix"
        )


def test_countries_have_required_keys():
    """Every country entry must have code, currency, and delivery_methods."""
    for country, info in SUPPORTED_COUNTRIES.items():
        assert "code" in info, f"Country '{country}' missing 'code'"
        assert "currency" in info, f"Country '{country}' missing 'currency'"
        assert "delivery_methods" in info, f"Country '{country}' missing 'delivery_methods'"
        assert len(info["delivery_methods"]) > 0, f"Country '{country}' has no delivery methods"


def test_delivery_method_details_have_required_keys():
    """Every delivery method must have name, fee, and eta."""
    for method, detail in DELIVERY_METHOD_DETAILS.items():
        assert "name" in detail, f"Method '{method}' missing 'name'"
        assert "fee" in detail, f"Method '{method}' missing 'fee'"
        assert "eta" in detail, f"Method '{method}' missing 'eta'"
        assert detail["fee"] >= 0, f"Method '{method}' has negative fee"


def test_amount_limits():
    """MIN_AMOUNT must be less than MAX_AMOUNT and both positive."""
    assert MIN_AMOUNT > 0
    assert MAX_AMOUNT > MIN_AMOUNT


def test_submission_delay_is_positive():
    """SUBMISSION_DELAY must be a positive number."""
    assert SUBMISSION_DELAY > 0
