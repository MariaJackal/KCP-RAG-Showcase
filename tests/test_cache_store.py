import time

from services.cache_store import TTLCache


def test_ttl_cache_set_get_hit():
    cache = TTLCache(ttl_seconds=10, max_entries=10)
    cache.set("k", "v")
    assert cache.get("k") == "v"


def test_ttl_cache_expire():
    cache = TTLCache(ttl_seconds=0.01, max_entries=10)
    cache.set("k", "v")
    time.sleep(0.02)
    assert cache.get("k") is None


def test_ttl_cache_evict_when_full():
    cache = TTLCache(ttl_seconds=100, max_entries=1)
    cache.set("k1", "v1")
    cache.set("k2", "v2")
    # one entry only
    v1 = cache.get("k1")
    v2 = cache.get("k2")
    assert (v1 is None and v2 == "v2") or (v2 is None and v1 == "v1")
