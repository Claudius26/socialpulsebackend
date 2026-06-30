"""
External provider abstraction.

All third-party HTTP integrations (SMM growth provider, OTP/virtual-number
providers) share the same mechanics: build auth, call with a timeout, parse
JSON-or-text, handle failure. That lives once in ``BaseHTTPProvider``.

Each *domain* then has an abstract interface — ``BaseSMMProvider`` and
``BaseOTPProvider`` — with concrete implementations (``ResellerSmmProvider``,
``ZapOtpProvider``). Adding a new provider later is a new subclass + a registry
entry, not new ``if`` branches scattered through the views (polymorphism).

A small registry exposes the active provider so call-sites stay decoupled.
"""
import os
import logging
from decimal import Decimal

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """Raised when an external provider call fails or returns an error."""


class BaseHTTPProvider:
    """Shared HTTP mechanics: timeouts, JSON-or-text parsing, typed errors."""

    timeout = 20

    def _request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        try:
            resp = requests.request(method, url, **kwargs)
        except requests.RequestException as exc:
            logger.warning("Provider request failed: %s %s — %s", method, url, exc)
            raise ProviderError(str(exc)) from exc
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text, "status_code": resp.status_code}

    def _get(self, url, **kwargs):
        return self._request("GET", url, **kwargs)

    def _post(self, url, **kwargs):
        return self._request("POST", url, **kwargs)


# --------------------------------------------------------------------------- #
# SMM (social-media boost) providers
# --------------------------------------------------------------------------- #
class BaseSMMProvider(BaseHTTPProvider):
    def list_services(self):
        raise NotImplementedError

    def usd_to_ngn_rate(self) -> Decimal:
        raise NotImplementedError

    def place_order(self, service_id, link, quantity) -> dict:
        raise NotImplementedError

    def order_status(self, order_id) -> dict:
        raise NotImplementedError


class ResellerSmmProvider(BaseSMMProvider):
    SERVICES_CACHE_KEY = "smm:services:catalog"
    SERVICES_TTL = 600  # 10 min — the catalog rarely changes
    RATE_CACHE_KEY = "fx:usd_ngn"
    RATE_TTL = 900  # 15 min
    DEFAULT_RATE = Decimal("1550.00")

    @property
    def api_key(self):
        return os.getenv("SMM_API_KEY")

    @property
    def api_url(self):
        return os.getenv("SMM_API_URL", "https://resellersmm.com/api/v2")

    def list_services(self):
        """Full service catalogue, cached so we don't re-download it per request."""
        cached = cache.get(self.SERVICES_CACHE_KEY)
        if cached is not None:
            return cached
        try:
            data = self._post(self.api_url, data={"key": self.api_key, "action": "services"})
        except ProviderError:
            return []
        services = data if isinstance(data, list) else []
        if services:
            cache.set(self.SERVICES_CACHE_KEY, services, self.SERVICES_TTL)
        return services

    def usd_to_ngn_rate(self) -> Decimal:
        cached = cache.get(self.RATE_CACHE_KEY)
        if cached is not None:
            return Decimal(str(cached))
        api_key = os.getenv("EXCHANGE_RATE_API_KEY")
        try:
            data = self._get(f"https://v6.exchangerate-api.com/v6/{api_key}/latest/USD", timeout=10)
            rate = Decimal(str(data["conversion_rates"]["NGN"]))
        except (ProviderError, KeyError, TypeError, ValueError):
            return self.DEFAULT_RATE
        cache.set(self.RATE_CACHE_KEY, str(rate), self.RATE_TTL)
        return rate

    def place_order(self, service_id, link, quantity) -> dict:
        return self._post(self.api_url, data={
            "key": self.api_key, "action": "add",
            "service": service_id, "link": link, "quantity": quantity,
        })

    def order_status(self, order_id) -> dict:
        return self._post(self.api_url, data={
            "key": self.api_key, "action": "status", "order": order_id,
        })


# --------------------------------------------------------------------------- #
# OTP (virtual-number) providers
# --------------------------------------------------------------------------- #
class BaseOTPProvider(BaseHTTPProvider):
    def list_pools(self, country, service) -> dict:
        raise NotImplementedError

    def rent(self, country, service, pool_id, provider=None) -> dict:
        raise NotImplementedError

    def get_sms(self, order_id) -> dict:
        raise NotImplementedError

    def cancel(self, order_id) -> dict:
        raise NotImplementedError


class ZapOtpProvider(BaseOTPProvider):
    BASE_URL = "https://zapotp.com/account/api/v1"
    CANCEL_URL = "https://www.zapotp.com/account/smspool/cancel_order.php"

    @property
    def headers(self):
        return {
            "Authorization": f"Bearer {os.getenv('ZAPOTP_API_KEY')}",
            "Content-Type": "application/json",
        }

    def list_pools(self, country, service) -> dict:
        return self._get(
            f"{self.BASE_URL}/services.php",
            headers=self.headers,
            params={"country": country, "service": service},
        )

    def rent(self, country, service, pool_id, provider=None) -> dict:
        # ZapOTP rents via POST /rent.php (NOT orders.php, which is GET-list only).
        payload = {"country": country, "service": service, "pool": int(pool_id)}
        if provider in ("global", "usa"):
            payload["provider"] = provider
        return self._post(f"{self.BASE_URL}/rent.php", headers=self.headers, json=payload)

    def get_sms(self, order_id) -> dict:
        return self._get(
            f"{self.BASE_URL}/sms.php", headers=self.headers, params={"order_id": str(order_id)}
        )

    def cancel(self, order_id) -> dict:
        return self._post(self.CANCEL_URL, headers=self.headers, json={"order_id": str(order_id)})


# --------------------------------------------------------------------------- #
# Gift-card providers (CardPulse)
# --------------------------------------------------------------------------- #
class BaseGiftcardProvider(BaseHTTPProvider):
    def list_countries(self) -> list:
        raise NotImplementedError

    def list_products(self, country=None, page=1, size=50, product_name=None) -> dict:
        raise NotImplementedError

    def get_product(self, product_id) -> dict:
        raise NotImplementedError

    def order(self, product_id, unit_price, quantity=1, recipient_email=None,
              custom_identifier=None) -> dict:
        raise NotImplementedError

    def redeem_code(self, transaction_id) -> dict:
        raise NotImplementedError


class ReloadlyGiftcardProvider(BaseGiftcardProvider):
    """Reloadly Gift Cards API.

    Auth is OAuth2 client-credentials: we exchange the client id/secret for a
    bearer token (cached until just before it expires). The ``audience`` we ask
    for IS the gift-cards base URL, which also selects sandbox vs live.
    """
    AUTH_URL = "https://auth.reloadly.com/oauth/token"
    SANDBOX_URL = "https://giftcards-sandbox.reloadly.com"
    LIVE_URL = "https://giftcards.reloadly.com"
    ACCEPT = "application/com.reloadly.giftcards-v1+json"
    TOKEN_CACHE_KEY = "reloadly:giftcards:token"

    @property
    def env(self):
        return (os.getenv("RELOADLY_ENV") or "sandbox").strip().lower()

    @property
    def base_url(self):
        return self.LIVE_URL if self.env == "live" else self.SANDBOX_URL

    @property
    def client_id(self):
        return os.getenv("RELOADLY_CLIENT_ID")

    @property
    def client_secret(self):
        return os.getenv("RELOADLY_CLIENT_SECRET")

    def _token(self) -> str:
        try:
            cached = cache.get(self.TOKEN_CACHE_KEY)
        except Exception:  # cache down — fetch fresh
            cached = None
        if cached:
            return cached

        data = self._post(self.AUTH_URL, json={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
            "audience": self.base_url,
        })
        token = (data or {}).get("access_token")
        if not token:
            raise ProviderError(f"Reloadly auth failed: {data}")
        ttl = int((data or {}).get("expires_in", 3600)) - 60
        try:
            cache.set(self.TOKEN_CACHE_KEY, token, max(60, ttl))
        except Exception:
            pass
        return token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Accept": self.ACCEPT,
            "Content-Type": "application/json",
        }

    def list_countries(self) -> list:
        data = self._get(f"{self.base_url}/countries", headers=self._headers())
        return data if isinstance(data, list) else []

    def list_products(self, country=None, page=1, size=50, product_name=None) -> dict:
        params = {"page": page, "size": size}
        if country:
            params["countryCode"] = country
        if product_name:
            params["productName"] = product_name
        return self._get(f"{self.base_url}/products", headers=self._headers(), params=params)

    def get_product(self, product_id) -> dict:
        return self._get(f"{self.base_url}/products/{product_id}", headers=self._headers())

    def order(self, product_id, unit_price, quantity=1, recipient_email=None,
              custom_identifier=None) -> dict:
        payload = {
            "productId": int(product_id),
            "quantity": int(quantity),
            "unitPrice": float(unit_price),
        }
        if recipient_email:
            payload["recipientEmail"] = recipient_email
        if custom_identifier:
            payload["customIdentifier"] = custom_identifier
        return self._post(f"{self.base_url}/orders", headers=self._headers(), json=payload)

    def redeem_code(self, transaction_id) -> dict:
        return self._get(
            f"{self.base_url}/orders/transactions/{transaction_id}/cards",
            headers=self._headers(),
        )


# --------------------------------------------------------------------------- #
# Bank-payout providers (CardPulse withdrawals)
# --------------------------------------------------------------------------- #
class BasePayoutProvider(BaseHTTPProvider):
    def list_banks(self) -> list:
        raise NotImplementedError

    def resolve_account(self, account_number, bank_code) -> dict:
        raise NotImplementedError

    def create_recipient(self, name, account_number, bank_code) -> dict:
        raise NotImplementedError

    def initiate_transfer(self, recipient_code, amount, reference, reason="") -> dict:
        raise NotImplementedError


class PaystackPayoutProvider(BasePayoutProvider):
    """Paystack Transfers — pay out NGN to a Nigerian bank account.

    Amounts are in NAIRA at this boundary; we convert to kobo for Paystack.
    """
    BASE = "https://api.paystack.co"

    @property
    def _headers(self):
        return {
            "Authorization": f"Bearer {os.getenv('PAYSTACK_SECRET_KEY')}",
            "Content-Type": "application/json",
        }

    def list_banks(self) -> list:
        data = self._get(f"{self.BASE}/bank", headers=self._headers, params={"currency": "NGN"})
        return (data or {}).get("data", []) if isinstance(data, dict) else []

    def resolve_account(self, account_number, bank_code) -> dict:
        return self._get(
            f"{self.BASE}/bank/resolve", headers=self._headers,
            params={"account_number": account_number, "bank_code": bank_code},
        )

    def create_recipient(self, name, account_number, bank_code) -> dict:
        return self._post(f"{self.BASE}/transferrecipient", headers=self._headers, json={
            "type": "nuban", "name": name, "account_number": account_number,
            "bank_code": bank_code, "currency": "NGN",
        })

    def initiate_transfer(self, recipient_code, amount, reference, reason="") -> dict:
        return self._post(f"{self.BASE}/transfer", headers=self._headers, json={
            "source": "balance", "amount": int(Decimal(str(amount)) * 100),
            "recipient": recipient_code, "reference": reference,
            "reason": reason or "CardPulse withdrawal",
        })


# --------------------------------------------------------------------------- #
# Registry — call-sites ask for the active provider by domain.
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Giftcard VALIDATION/buyback providers (for user-submitted cards to sell).
# No universal API exists for validating arbitrary third-party cards, so the
# default returns "pending" — swap in a real provider when one is available.
# --------------------------------------------------------------------------- #
class BaseCardValidationProvider:
    def validate(self, *, brand, country, currency, face_value, code=None, image=None) -> dict:
        """Return {"status": "approved"|"rejected"|"pending", "ref": str, "reason": str}."""
        raise NotImplementedError


class NullValidationProvider(BaseCardValidationProvider):
    """No real validator wired yet — every submission waits for one."""
    def validate(self, **kwargs) -> dict:
        return {"status": "pending", "ref": "", "reason": "Awaiting validation provider."}


SMM_PROVIDERS = {"resellersmm": ResellerSmmProvider}
OTP_PROVIDERS = {"zapotp": ZapOtpProvider}
GIFTCARD_PROVIDERS = {"reloadly": ReloadlyGiftcardProvider}
PAYOUT_PROVIDERS = {"paystack": PaystackPayoutProvider}
CARD_VALIDATION_PROVIDERS = {"null": NullValidationProvider}


def get_smm_provider(name="resellersmm") -> BaseSMMProvider:
    return SMM_PROVIDERS[name]()


def get_otp_provider(name="zapotp") -> BaseOTPProvider:
    return OTP_PROVIDERS[name]()


def get_giftcard_provider(name="reloadly") -> BaseGiftcardProvider:
    return GIFTCARD_PROVIDERS[name]()


def get_payout_provider(name="paystack") -> BasePayoutProvider:
    return PAYOUT_PROVIDERS[name]()


def get_card_validation_provider(name=None) -> BaseCardValidationProvider:
    name = name or os.getenv("CARD_VALIDATION_PROVIDER", "null")
    return CARD_VALIDATION_PROVIDERS.get(name, NullValidationProvider)()
