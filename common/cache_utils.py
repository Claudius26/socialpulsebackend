import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)


def get_or_set_cache(key, fetch_function, timeout=300):
    # If the cache backend is unreachable, fall back to computing the value so
    # the request still succeeds.
    try:
        cached_data = cache.get(key)
        if cached_data is not None:
            return cached_data
    except Exception as exc:  # pragma: no cover - depends on cache backend
        logger.warning("cache get failed for %s: %s", key, exc)
        return fetch_function()

    data = fetch_function()
    try:
        cache.set(key, data, timeout=timeout)
    except Exception as exc:  # pragma: no cover
        logger.warning("cache set failed for %s: %s", key, exc)
    return data


def delete_cache_keys(*keys):
    # Cache invalidation must NEVER break the operation that triggered it
    # (e.g. a wallet transfer). Swallow backend errors.
    for key in keys:
        try:
            cache.delete(key)
        except Exception as exc:  # pragma: no cover
            logger.warning("cache delete failed for %s: %s", key, exc)


def invalidate_user_wallet_caches(user_id):
    """Clear the per-user caches whose values depend on the wallet balance or
    spend totals. Call this after ANY wallet mutation so /me and the summary
    never show a stale balance. Safe to call even if the cache is down."""
    from .cache_keys import user_profile_key, user_summary_key, user_transactions_key

    delete_cache_keys(
        user_profile_key(user_id),
        user_summary_key(user_id),
        user_transactions_key(user_id),
    )
