# payments/utils.py
import hmac
import hashlib
import os

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")

def verify_paystack_signature(raw_body: bytes, signature: str) -> bool:
    if not signature or not PAYSTACK_SECRET_KEY:
        return False
    computed = hmac.new(
        PAYSTACK_SECRET_KEY.encode("utf-8"),
        raw_body,
        hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(computed, signature)
