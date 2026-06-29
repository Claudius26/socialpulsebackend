from django.utils import timezone
from rest_framework import authentication, exceptions

from .models import ApiKey, hash_key


class ApiKeyAuthentication(authentication.BaseAuthentication):
    """
    Authenticate developer requests with an API key, sent as either:
        Authorization: Api-Key sp_live_xxx
        X-API-Key: sp_live_xxx
    The key is matched by its SHA-256 hash; the raw key is never stored.
    """
    keyword = "Api-Key"

    def _extract_key(self, request):
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if auth.startswith(self.keyword + " "):
            return auth[len(self.keyword) + 1:].strip()
        return request.META.get("HTTP_X_API_KEY")

    def authenticate(self, request):
        key = self._extract_key(request)
        if not key:
            return None  # no key provided — let other auth/permission handle it

        try:
            api_key = ApiKey.objects.select_related("user").get(
                key_hash=hash_key(key), is_active=True
            )
        except ApiKey.DoesNotExist:
            raise exceptions.AuthenticationFailed("Invalid or revoked API key.")

        # Best-effort usage timestamp (doesn't block the request).
        ApiKey.objects.filter(pk=api_key.pk).update(last_used_at=timezone.now())
        return (api_key.user, api_key)

    def authenticate_header(self, request):
        return self.keyword
