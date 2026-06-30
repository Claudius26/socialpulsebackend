"""
JWT auth that records the user's last activity so admins can see who's online.

We update `last_seen` at most once per minute per user (throttled via cache) to
avoid a DB write on every request. Works for both the website and the app since
both authenticate with JWT.
"""
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework_simplejwt.authentication import JWTAuthentication


class LastSeenJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            user, _ = result
            try:
                key = f"seen:{user.pk}"
                if not cache.get(key):
                    type(user).objects.filter(pk=user.pk).update(last_seen=timezone.now())
                    cache.set(key, 1, 60)
            except Exception:
                # If the cache is down, fall back to a direct (still cheap) update.
                try:
                    type(user).objects.filter(pk=user.pk).update(last_seen=timezone.now())
                except Exception:
                    pass
        return result
