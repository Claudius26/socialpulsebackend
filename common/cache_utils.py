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