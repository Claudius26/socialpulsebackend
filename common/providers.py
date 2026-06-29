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
# Registry — call-sites ask for the active provider by domain.
# --------------------------------------------------------------------------- #
SMM_PROVIDERS = {"resellersmm": ResellerSmmProvider}
OTP_PROVIDERS = {"zapotp": ZapOtpProvider}


def get_smm_provider(name="resellersmm") -> BaseSMMProvider:
    return SMM_PROVIDERS[name]()


def get_otp_provider(name="zapotp") -> BaseOTPProvider:
    return OTP_PROVIDERS[name]()
