import time

from utils.cache import TTLCache


def test_set_and_get():
    c = TTLCache(default_ttl=60)
    c.set("key1", "value1")
    assert c.get("key1") == "value1"


def test_get_missing_key():
    c = TTLCache()
    assert c.get("nonexistent") is None


def test_ttl_expiry():
    c = TTLCache(default_ttl=1)
    c.set("key1", "value1", ttl=1)
    assert c.get("key1") == "value1"
    time.sleep(1.1)
    assert c.get("key1") is None


def test_custom_ttl_override():
    c = TTLCache(default_ttl=60)
    c.set("key1", "value1", ttl=1)
    time.sleep(1.1)
    assert c.get("key1") is None


def test_clear():
    c = TTLCache()
    c.set("a", 1)
    c.set("b", 2)
    c.clear()
    assert c.get("a") is None
    assert c.get("b") is None
