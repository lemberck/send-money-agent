"""Static data and validation rules for the Send Money Agent."""

SUPPORTED_COUNTRIES = {
    "Mexico": {"code": "MX", "currency": "MXN", "delivery_methods": ["bank_deposit", "mobile_wallet"]},
    "Philippines": {"code": "PH", "currency": "PHP", "delivery_methods": ["bank_deposit", "mobile_wallet"]},
    "India": {"code": "IN", "currency": "INR", "delivery_methods": ["bank_deposit", "mobile_wallet"]},
    "Colombia": {"code": "CO", "currency": "COP", "delivery_methods": ["bank_deposit", "mobile_wallet"]},
    "Guatemala": {"code": "GT", "currency": "GTQ", "delivery_methods": ["bank_deposit", "mobile_wallet"]},
    "United Kingdom": {"code": "GB", "currency": "GBP", "delivery_methods": ["bank_deposit"]},
    "Canada": {"code": "CA", "currency": "CAD", "delivery_methods": ["bank_deposit", "mobile_wallet"]},
    "Brazil": {"code": "BR", "currency": "BRL", "delivery_methods": ["bank_deposit", "mobile_wallet"]},
    "Nigeria": {"code": "NG", "currency": "NGN", "delivery_methods": ["bank_deposit", "mobile_wallet"]},
    "Kenya": {"code": "KE", "currency": "KES", "delivery_methods": ["mobile_wallet"]},
}

DELIVERY_METHOD_DETAILS = {
    "bank_deposit": {"name": "Bank Deposit", "description": "Deposit to bank account. Required: beneficiary name, bank name, and account number.", "fee": 3.99, "eta": "1-2 business days"},
    "mobile_wallet": {"name": "Mobile Wallet", "description": "Sent to mobile wallet. Required: beneficiary name and phone number.", "fee": 1.99, "eta": "Instant to 1 hour"},
}

MIN_AMOUNT = 1.0
MAX_AMOUNT = 10000.0
DEFAULT_CURRENCY = "USD"
SUBMISSION_DELAY = 60  # seconds before confirmed → submitted

REQUIRED_FIELDS = [
    "transfer_country",
    "transfer_amount",
    "transfer_beneficiary_name",
    "transfer_delivery_method",
]
