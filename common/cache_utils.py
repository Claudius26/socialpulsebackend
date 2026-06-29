from django.core.cache import cache

def get_or_set_cache(key, fetch_function, timeout=300):
    cached_data = cache.get(key)
    if cached_data is not None:
        return cached_data

    data = fetch_function()
    cache.set(key, data, timeout=timeout)
    return data

def delete_cache_keys(*keys):
    for key in keys:
        cache.delete(key)


def invalidate_user_wallet_caches(user_id):
    """Clear the per-user caches whose values depend on the wallet balance or
    spend totals. Call this after ANY wallet mutation so /me and the summary
    never show a stale balance."""
    from .cache_keys import user_profile_key, user_summary_key, user_transactions_key

    delete_cache_keys(
        user_profile_key(user_id),
        user_summary_key(user_id),
        user_transactions_key(user_id),
    )