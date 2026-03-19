# payments/services/paystack.py
import os
import requests

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
BASE_URL = "https://api.paystack.co"

def paystack_headers():
    return {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }

def create_or_get_customer(email, first_name="", last_name=""):
    payload = {"email": email, "first_name": first_name, "last_name": last_name}
    r = requests.post(f"{BASE_URL}/customer", json=payload, headers=paystack_headers(), timeout=20)
    data = r.json()
    if not data.get("status"):
        raise Exception(data.get("message", "Failed to create customer"))
    return data["data"]  # includes customer_code

def create_dedicated_virtual_account(customer_code):
    payload = {"customer": customer_code}
    r = requests.post(f"{BASE_URL}/dedicated_account", json=payload, headers=paystack_headers(), timeout=20)
    data = r.json()
    if not data.get("status"):
        raise Exception(data.get("message", "Failed to create dedicated account"))
    return data["data"]
