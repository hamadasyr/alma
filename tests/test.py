"""
Tests for the Alma framework.
Run with: python -m pytest test_alma.py -v
"""

import threading
import time

import pytest

import alma
from alma import AlmaVar, AlmaError, FrozenError, RollbackError, AlmaRegistry


# ---------------------------------------------------------------------------
# Basic get / set / history
# ---------------------------------------------------------------------------


def test_initial_value():
    var = AlmaVar("x", 42)
    assert var.get() == 42


def test_set_updates_value():
    var = AlmaVar("x", 0)
    var.set(99)
    assert var.get() == 99


def test_history_tracks_all_changes():
    var = AlmaVar("x", 0)
    var.set(1)
    var.set(2)
    hist = var.history()
    assert len(hist) == 3  # initial + 2 sets
    assert [r.value for r in hist] == [0, 1, 2]


def test_history_returns_copy():
    var = AlmaVar("x", 0)
    h1 = var.history()
    var.set(1)
    h2 = var.history()
    assert len(h1) == 1
    assert len(h2) == 2


def test_last_change_is_initial_on_fresh_var():
    var = AlmaVar("x", "hello")
    lc = var.last_change()
    assert lc.label == "initial"
    assert lc.value == "hello"


def test_last_change_after_set():
    var = AlmaVar("x", 0)
    var.set(7, label="lucky")
    lc = var.last_change()
    assert lc.value == 7
    assert lc.label == "lucky"


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


def test_rollback_to_initial():
    var = AlmaVar("x", 10)
    var.set(20).set(30)
    var.rollback(0)
    assert var.get() == 10


def test_rollback_to_middle():
    var = AlmaVar("x", "a")
    var.set("b")
    var.set("c")
    var.rollback(1)
    assert var.get() == "b"


def test_rollback_is_appended_to_history():
    var = AlmaVar("x", 1)
    var.set(2)
    var.rollback(0)
    assert len(var.history()) == 3  # initial, set(2), rollback


def test_rollback_out_of_range_raises():
    var = AlmaVar("x", 0)
    with pytest.raises(RollbackError):
        var.rollback(5)


def test_rollback_negative_index_raises():
    var = AlmaVar("x", 0)
    with pytest.raises(RollbackError):
        var.rollback(-1)


def test_reset_restores_initial():
    var = AlmaVar("x", "start")
    var.set("middle").set("end")
    var.reset()
    assert var.get() == "start"


# ---------------------------------------------------------------------------
# Deep copy isolation
# ---------------------------------------------------------------------------


def test_deep_copy_isolates_mutable_values():
    data = {"count": 0}
    var = AlmaVar("d", data, deep_copy=True)
    data["count"] = 99           # mutate original
    assert var.get()["count"] == 0


def test_no_deep_copy_shares_reference():
    data = {"count": 0}
    var = AlmaVar("d", data, deep_copy=False)
    data["count"] = 99
    assert var.get()["count"] == 99


# ---------------------------------------------------------------------------
# Freeze / thaw
# ---------------------------------------------------------------------------


def test_freeze_prevents_set():
    var = AlmaVar("x", 1)
    var.freeze()
    with pytest.raises(FrozenError):
        var.set(2)


def test_freeze_prevents_rollback():
    var = AlmaVar("x", 1)
    var.set(2)
    var.freeze()
    with pytest.raises(FrozenError):
        var.rollback(0)


def test_thaw_allows_mutation():
    var = AlmaVar("x", 1)
    var.freeze()
    var.thaw()
    var.set(2)
    assert var.get() == 2


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def test_validator_rejects_bad_value():
    var = AlmaVar("age", 0)
    var.add_validator(lambda v: (_ for _ in ()).throw(ValueError("negative")) if v < 0 else None)
    # simpler validator
    var2 = AlmaVar("age2", 0)

    def must_be_positive(v):
        if v < 0:
            raise ValueError(f"{v} is negative")

    var2.add_validator(must_be_positive)
    with pytest.raises(ValueError):
        var2.set(-1)


def test_validator_allows_good_value():
    var = AlmaVar("age", 0)

    def must_be_positive(v):
        if v < 0:
            raise ValueError

    var.add_validator(must_be_positive)
    var.set(5)
    assert var.get() == 5


# ---------------------------------------------------------------------------
# Listeners
# ---------------------------------------------------------------------------


def test_listener_called_on_set():
    events = []
    var = AlmaVar("x", 0, on_change=[lambda v, r: events.append(r.value)])
    var.set(1)
    var.set(2)
    assert events == [1, 2]


def test_listener_exception_is_swallowed():
    def bad_listener(var, record):
        raise RuntimeError("boom")

    var = AlmaVar("x", 0, on_change=[bad_listener])
    var.set(1)           # must not raise
    assert var.get() == 1


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def test_diff_returns_correct_count():
    var = AlmaVar("x", 0)
    var.set(1)
    var.set(2)
    diffs = var.diff()
    assert len(diffs) == 2


def test_diff_captures_from_to_values():
    var = AlmaVar("x", "a")
    var.set("b")
    d = var.diff()[0]
    assert d["from_value"] == "a"
    assert d["to_value"] == "b"


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_concurrent_sets_are_safe():
    var = AlmaVar("counter", 0, deep_copy=False)
    lock = threading.Lock()
    results = []

    def worker():
        for _ in range(50):
            with lock:
                current = var.get()
                var.set(current + 1)
        with lock:
            results.append(var.get())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert var.get() == 200


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_watch_returns_alma_var():
    reg = AlmaRegistry()
    v = reg.watch("temp", 0)
    assert isinstance(v, AlmaVar)


def test_registry_prevents_duplicate_names():
    reg = AlmaRegistry()
    reg.watch("x", 0)
    with pytest.raises(AlmaError):
        reg.watch("x", 1)


def test_registry_overwrite_allowed():
    reg = AlmaRegistry()
    reg.watch("x", 0)
    v2 = reg.watch("x", 99, overwrite=True)
    assert v2.get() == 99


def test_registry_get_var():
    reg = AlmaRegistry()
    reg.watch("pi", 3.14)
    assert reg.get_var("pi").get() == 3.14


def test_registry_get_var_missing_raises():
    reg = AlmaRegistry()
    with pytest.raises(AlmaError):
        reg.get_var("missing")


def test_registry_snapshot():
    reg = AlmaRegistry()
    reg.watch("a", 1)
    reg.watch("b", 2)
    snap = reg.snapshot()
    assert snap == {"a": 1, "b": 2}


def test_registry_unregister():
    reg = AlmaRegistry()
    reg.watch("tmp", 0)
    reg.unregister("tmp")
    with pytest.raises(AlmaError):
        reg.get_var("tmp")


# ---------------------------------------------------------------------------
# Repr / equality
# ---------------------------------------------------------------------------


def test_repr_contains_name_and_value():
    var = AlmaVar("foo", 123)
    r = repr(var)
    assert "foo" in r
    assert "123" in r


def test_equality_with_raw_value():
    var = AlmaVar("x", 7)
    assert var == 7


def test_equality_between_vars():
    a = AlmaVar("a", "hello")
    b = AlmaVar("b", "hello")
    assert a == b
