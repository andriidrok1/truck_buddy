"""Unit tests (no server) for the per-driver token bucket in services/auth.py."""
from services import auth


def test_bucket_allows_then_rejects_then_refills():
    auth._buckets.clear()
    now = 1000.0
    allowed = sum(auth.take_token("drv_a", now, per_min=40) for _ in range(41))
    assert allowed == 40
    assert auth.take_token("drv_a", now + 0.1, per_min=40) is False
    assert auth.take_token("drv_a", now + 1.6, per_min=40) is True  # 1.5 s refills one request at 40/min
    assert auth.take_token("drv_b", now, per_min=40) is True  # another driver has their own budget


def test_zero_disables():
    auth._buckets.clear()
    assert all(auth.take_token("drv_c", 0.0, per_min=0) for _ in range(500))
