from django.core.cache import cache

from .models import MobileNumberAllocation

CACHE_KEY = "ncc:allocations:v1"
CACHE_TTL = 300  # seconds


def allocation_map() -> dict[str, dict]:
    """`{"0803": {...}}` for the whole table.

    Cached: this is read on every login attempt but changes a few times a
    year. Plain dicts, not model instances, so the cache survives model
    changes and stays cheap to pickle.
    """
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    table = {
        row["ndc"]: row
        for row in MobileNumberAllocation.objects.values(
            "ndc", "operator", "nsn_length", "status", "allows_user_accounts"
        )
    }
    cache.set(CACHE_KEY, table, CACHE_TTL)
    return table


def invalidate_allocation_cache() -> None:
    cache.delete(CACHE_KEY)
