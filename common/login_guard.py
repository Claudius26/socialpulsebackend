"""
Brute-force login lockout.

After too many failed attempts for the same identifier (email / username / @tag)
the account is locked for a cooling-off period. The lock is stored in the cache
keyed by identifier, so it works across processes without a DB write.

Usage in a login view:

    from common import login_guard
    locked = login_guard.locked_seconds(identifier)
    if locked:
        return Response({"error": login_guard.lock_message(locked),
                         "retry_after": locked}, status=429)
    ...authenticate...
    if not user:
        login_guard.record_failure(identifier)   # may start a lock
        ...
    else:
        login_guard.clear_failures(identifier)
"""
import math
import time

from django.core.cache import cache

MAX_ATTEMPTS = 5           # failed tries before the lock kicks in
LOCK_SECONDS = 30 * 60     # 30-minute lockout


def _fail_key(identifier: str) -> str:
    return f"loginfail:{(identifier or '').strip().lower()}"


def _lock_key(identifier: str) -> str:
    return f"loginlock:{(identifier or '').strip().lower()}"


def locked_seconds(identifier: str) -> int:
    """Remaining lockout in seconds (0 if not locked)."""
    try:
        until = cache.get(_lock_key(identifier))
    except Exception:
        return 0
    if until:
        remaining = int(until - time.time())
        if remaining > 0:
            return remaining
    return 0


def record_failure(identifier: str) -> int:
    """Count a failed attempt; start a lock once MAX_ATTEMPTS is reached.
    Returns the remaining lock seconds (0 if not yet locked)."""
    try:
        key = _fail_key(identifier)
        attempts = (cache.get(key) or 0) + 1
        cache.set(key, attempts, LOCK_SECONDS)
        if attempts >= MAX_ATTEMPTS:
            cache.set(_lock_key(identifier), time.time() + LOCK_SECONDS, LOCK_SECONDS)
            cache.delete(key)  # the lock now governs
            return LOCK_SECONDS
    except Exception:
        pass
    return 0


def clear_failures(identifier: str) -> None:
    """Reset on a successful login."""
    try:
        cache.delete(_fail_key(identifier))
        cache.delete(_lock_key(identifier))
    except Exception:
        pass


def lock_message(seconds: int) -> str:
    minutes = max(1, math.ceil(seconds / 60))
    return (f"Too many failed login attempts. Please try again in "
            f"{minutes} minute{'s' if minutes != 1 else ''}.")
