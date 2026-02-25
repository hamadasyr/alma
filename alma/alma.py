"""
Alma — Time-Travel Variable Framework
======================================
A thread-safe, extensible Python framework for tracking, inspecting,
and rolling back variable state over time.

Usage:
    import alma

    counter = alma.watch("counter", 0)
    counter.set(1)
    counter.set(42)

    print(counter.get())          # 42
    print(counter.history())      # full audit trail
    counter.rollback(0)           # restore initial value
"""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Generic, List, Optional, TypeVar

__version__ = "0.1.0"
__all__ = ["watch", "AlmaVar", "ChangeRecord", "AlmaError", "AlmaRegistry"]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AlmaError(Exception):
    """Base exception for all Alma errors."""


class RollbackError(AlmaError):
    """Raised when a rollback cannot be completed."""


class FrozenError(AlmaError):
    """Raised when a frozen variable is mutated."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

T = TypeVar("T")


@dataclass(frozen=True)
class ChangeRecord:
    """
    An immutable snapshot of a single value change.

    Attributes:
        index:      Position in the history list (0 = initial value).
        value:      Deep-copied snapshot of the value at this point.
        timestamp:  UTC datetime when the change was recorded.
        label:      Optional human-readable annotation.
    """

    index: int
    value: Any
    timestamp: datetime
    label: Optional[str] = None

    def __repr__(self) -> str:
        ts = self.timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")
        tag = f" [{self.label}]" if self.label else ""
        return f"ChangeRecord(index={self.index}, value={self.value!r}, at={ts}{tag})"


# ---------------------------------------------------------------------------
# Core variable
# ---------------------------------------------------------------------------


class AlmaVar(Generic[T]):
    """
    A watched variable that records its full mutation history.

    Obtain an instance via :func:`alma.watch` rather than instantiating
    this class directly.
    """

    def __init__(
        self,
        name: str,
        initial_value: T,
        *,
        deep_copy: bool = True,
        validators: Optional[List[Callable[[Any], None]]] = None,
        on_change: Optional[List[Callable[["AlmaVar[T]", ChangeRecord], None]]] = None,
    ) -> None:
        self._name = name
        self._deep_copy = deep_copy
        self._validators: List[Callable[[Any], None]] = validators or []
        self._listeners: List[Callable[["AlmaVar[T]", ChangeRecord], None]] = on_change or []
        self._lock = threading.RLock()
        self._frozen = False
        self._history: List[ChangeRecord] = []

        # Record initial value without going through set() so no listeners fire.
        snapshot = self._snapshot(initial_value)
        record = ChangeRecord(
            index=0,
            value=snapshot,
            timestamp=datetime.now(tz=timezone.utc),
            label="initial",
        )
        self._history.append(record)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        """The name this variable was registered under."""
        return self._name

    def get(self) -> T:
        """Return the current value."""
        with self._lock:
            return self._history[-1].value

    def set(self, value: T, *, label: Optional[str] = None) -> "AlmaVar[T]":
        """
        Update the current value and append a :class:`ChangeRecord`.

        Args:
            value:  New value to store.
            label:  Optional annotation attached to this change.

        Returns:
            *self*, enabling fluent chaining: ``var.set(1).set(2)``.

        Raises:
            FrozenError:  If the variable has been frozen.
            ValueError:   If a registered validator rejects the value.
        """
        with self._lock:
            if self._frozen:
                raise FrozenError(f"Variable '{self._name}' is frozen and cannot be mutated.")

            for validate in self._validators:
                validate(value)

            snapshot = self._snapshot(value)
            record = ChangeRecord(
                index=len(self._history),
                value=snapshot,
                timestamp=datetime.now(tz=timezone.utc),
                label=label,
            )
            self._history.append(record)

        # Fire listeners outside the lock to avoid deadlocks.
        for listener in self._listeners:
            try:
                listener(self, record)
            except Exception:
                pass  # Listeners must not crash the caller.

        return self

    def history(self) -> List[ChangeRecord]:
        """Return a shallow copy of the full change history."""
        with self._lock:
            return list(self._history)

    def last_change(self) -> ChangeRecord:
        """
        Return information about the most recent change.

        Returns:
            The latest :class:`ChangeRecord` (which is the initial record
            if ``set`` has never been called).
        """
        with self._lock:
            return self._history[-1]

    def rollback(self, index: int, *, label: Optional[str] = None) -> "AlmaVar[T]":
        """
        Restore the value recorded at *index* and append a new rollback record.

        The rollback itself is tracked in history so the audit trail is never
        silently mutated.

        Args:
            index:  History position to roll back to (0 = initial value).
            label:  Optional annotation for the rollback record.

        Raises:
            RollbackError:  If *index* is out of range.
            FrozenError:    If the variable is frozen.
        """
        with self._lock:
            if self._frozen:
                raise FrozenError(f"Variable '{self._name}' is frozen and cannot be rolled back.")
            if index < 0 or index >= len(self._history):
                raise RollbackError(
                    f"Rollback index {index} is out of range "
                    f"[0, {len(self._history) - 1}] for variable '{self._name}'."
                )
            target_value = self._history[index].value

        rollback_label = label or f"rollback to index {index}"
        return self.set(self._snapshot(target_value), label=rollback_label)

    # ------------------------------------------------------------------
    # Freeze / thaw
    # ------------------------------------------------------------------

    def freeze(self) -> "AlmaVar[T]":
        """Prevent any further mutations. Returns *self*."""
        with self._lock:
            self._frozen = True
        return self

    def thaw(self) -> "AlmaVar[T]":
        """Re-enable mutations after a freeze. Returns *self*."""
        with self._lock:
            self._frozen = False
        return self

    @property
    def is_frozen(self) -> bool:
        with self._lock:
            return self._frozen

    # ------------------------------------------------------------------
    # Validators & listeners
    # ------------------------------------------------------------------

    def add_validator(self, fn: Callable[[Any], None]) -> "AlmaVar[T]":
        """
        Register a callable that raises :class:`ValueError` for invalid values.

        Example::

            var.add_validator(lambda v: v >= 0 or (_ for _ in ()).throw(
                ValueError("must be non-negative")))

        Returns *self*.
        """
        self._validators.append(fn)
        return self

    def add_listener(
        self, fn: Callable[["AlmaVar[T]", ChangeRecord], None]
    ) -> "AlmaVar[T]":
        """
        Register a change listener called after every successful ``set``.

        The listener receives ``(alma_var, change_record)`` and any exception
        it raises is silently swallowed to protect the caller.

        Returns *self*.
        """
        self._listeners.append(fn)
        return self

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def diff(self) -> List[dict]:
        """
        Return a list of diffs between consecutive history entries.

        Each entry is a dict with keys ``from_index``, ``to_index``,
        ``from_value``, ``to_value``, and ``elapsed_ms``.
        """
        records = self.history()
        result = []
        for i in range(1, len(records)):
            prev, curr = records[i - 1], records[i]
            elapsed = (curr.timestamp - prev.timestamp).total_seconds() * 1000
            result.append(
                {
                    "from_index": prev.index,
                    "to_index": curr.index,
                    "from_value": prev.value,
                    "to_value": curr.value,
                    "elapsed_ms": round(elapsed, 3),
                }
            )
        return result

    def reset(self) -> "AlmaVar[T]":
        """Shorthand for ``rollback(0)`` — restore the initial value."""
        return self.rollback(0, label="reset to initial")

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        with self._lock:
            frozen_tag = " [frozen]" if self._frozen else ""
            return (
                f"AlmaVar(name={self._name!r}, "
                f"value={self.get()!r}, "
                f"history_len={len(self._history)}{frozen_tag})"
            )

    def __eq__(self, other: object) -> bool:
        if isinstance(other, AlmaVar):
            return self.get() == other.get()
        return self.get() == other

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _snapshot(self, value: Any) -> Any:
        if self._deep_copy:
            try:
                return copy.deepcopy(value)
            except Exception:
                return value
        return value


# ---------------------------------------------------------------------------
# Registry — optional central store for named variables
# ---------------------------------------------------------------------------


class AlmaRegistry:
    """
    A thread-safe registry that maps names to :class:`AlmaVar` instances.

    The module-level :func:`watch` function delegates to a default
    singleton registry so callers rarely need to instantiate this directly.
    """

    def __init__(self) -> None:
        self._vars: dict[str, AlmaVar[Any]] = {}
        self._lock = threading.Lock()

    def watch(
        self,
        name: str,
        initial_value: Any,
        *,
        deep_copy: bool = True,
        validators: Optional[List[Callable[[Any], None]]] = None,
        on_change: Optional[List[Callable[["AlmaVar"], ChangeRecord], None]] = None,
        overwrite: bool = False,
    ) -> AlmaVar[Any]:
        """
        Create and register a watched variable.

        Args:
            name:           Unique identifier for this variable.
            initial_value:  Starting value.
            deep_copy:      If True (default), all stored values are deep-copied.
            validators:     List of validator callables.
            on_change:      List of change-listener callables.
            overwrite:      If True, re-register an existing name (resetting history).

        Raises:
            AlmaError:  If *name* is already registered and ``overwrite=False``.
        """
        with self._lock:
            if name in self._vars and not overwrite:
                raise AlmaError(
                    f"Variable '{name}' is already registered. "
                    "Use overwrite=True to replace it."
                )
            var: AlmaVar[Any] = AlmaVar(
                name,
                initial_value,
                deep_copy=deep_copy,
                validators=validators,
                on_change=on_change,
            )
            self._vars[name] = var
        return var

    def get_var(self, name: str) -> AlmaVar[Any]:
        """Retrieve a registered variable by name."""
        with self._lock:
            try:
                return self._vars[name]
            except KeyError:
                raise AlmaError(f"No variable named '{name}' is registered.") from None

    def all_vars(self) -> dict[str, AlmaVar[Any]]:
        """Return a snapshot of all registered variables."""
        with self._lock:
            return dict(self._vars)

    def unregister(self, name: str) -> None:
        """Remove a variable from the registry."""
        with self._lock:
            if name not in self._vars:
                raise AlmaError(f"No variable named '{name}' is registered.")
            del self._vars[name]

    def snapshot(self) -> dict[str, Any]:
        """Return a mapping of every variable name to its current value."""
        with self._lock:
            return {name: var.get() for name, var in self._vars.items()}

    def __repr__(self) -> str:
        with self._lock:
            names = list(self._vars)
        return f"AlmaRegistry({names!r})"


# ---------------------------------------------------------------------------
# Module-level API (default registry)
# ---------------------------------------------------------------------------

_default_registry = AlmaRegistry()


def watch(
    name: str,
    initial_value: Any,
    *,
    deep_copy: bool = True,
    validators: Optional[List[Callable[[Any], None]]] = None,
    on_change: Optional[List[Callable]] = None,
    overwrite: bool = False,
) -> AlmaVar[Any]:
    """
    Create a watched variable in the default global registry.

    This is the primary entry-point for Alma.

    Example::

        import alma

        score = alma.watch("score", 0)
        score.set(10).set(20).set(30)

        print(score.get())           # 30
        print(score.history())       # all 4 records
        score.rollback(1)            # back to 10
        print(score.last_change())   # rollback record

    Args:
        name:           Unique name for this variable.
        initial_value:  The starting value.
        deep_copy:      Whether to deep-copy values on store (default: True).
        validators:     Optional list of callables that raise on bad values.
        on_change:      Optional list of ``(var, record)`` listener callables.
        overwrite:      Allow replacing an existing registration.

    Returns:
        An :class:`AlmaVar` instance.
    """
    return _default_registry.watch(
        name,
        initial_value,
        deep_copy=deep_copy,
        validators=validators,
        on_change=on_change,
        overwrite=overwrite,
    )


def get_registry() -> AlmaRegistry:
    """Return the default global :class:`AlmaRegistry`."""
    return _default_registry
