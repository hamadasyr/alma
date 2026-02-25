# Alma — Time-Travel Variable Framework

> "What if every variable remembered where it came from?"

Alma is a lightweight, thread-safe Python framework that wraps ordinary values in
observable, auditable containers — giving you a complete mutation history and the
ability to roll back to any past state.

---

## Installation

No external dependencies. Copy `alma.py` into your project or install once a package
is published:

```bash
pip install alma   # (future)
```

---

## Quick start

```python
import alma

# 1. Create a watched variable
score = alma.watch("score", 0)

# 2. Mutate it
score.set(10).set(20).set(30)   # fluent chaining

# 3. Inspect
print(score.get())              # 30
print(score.last_change())      # ChangeRecord(index=3, value=30, ...)

# 4. Full audit trail
for record in score.history():
    print(record)

# 5. Time-travel
score.rollback(1)               # back to 10
print(score.get())              # 10

# 6. Restore initial value
score.reset()
print(score.get())              # 0
```

---

## API reference

### `alma.watch(name, initial_value, *, deep_copy=True, validators=None, on_change=None, overwrite=False) → AlmaVar`

Create a watched variable in the global default registry.

| Argument | Type | Description |
|---|---|---|
| `name` | `str` | Unique identifier |
| `initial_value` | `Any` | Starting value |
| `deep_copy` | `bool` | Deep-copy every stored value (default `True`) |
| `validators` | `list[callable]` | Raise `ValueError` to reject a value |
| `on_change` | `list[callable]` | `(var, record)` listeners fired after every `set` |
| `overwrite` | `bool` | Replace an existing registration |

---

### `AlmaVar`

| Method | Returns | Description |
|---|---|---|
| `get()` | `T` | Current value |
| `set(value, *, label=None)` | `self` | Update value; append `ChangeRecord` |
| `history()` | `list[ChangeRecord]` | Full mutation history |
| `last_change()` | `ChangeRecord` | Most recent `ChangeRecord` |
| `rollback(index, *, label=None)` | `self` | Restore value at `index`; appends a new record |
| `reset()` | `self` | Shorthand for `rollback(0)` |
| `diff()` | `list[dict]` | Consecutive-change diffs with elapsed ms |
| `freeze()` | `self` | Prevent further mutations |
| `thaw()` | `self` | Re-enable mutations |
| `add_validator(fn)` | `self` | Register a validator |
| `add_listener(fn)` | `self` | Register a change listener |

---

### `ChangeRecord`

Immutable dataclass stored in every variable's history.

```python
@dataclass(frozen=True)
class ChangeRecord:
    index:     int
    value:     Any
    timestamp: datetime   # UTC
    label:     str | None
```

---

## Advanced usage

### Validators

```python
def must_be_positive(v):
    if v < 0:
        raise ValueError(f"Expected positive, got {v}")

balance = alma.watch("balance", 100.0)
balance.add_validator(must_be_positive)

balance.set(50)    # OK
balance.set(-1)    # raises ValueError
```

### Change listeners

```python
import logging

def audit(var, record):
    logging.info("[%s] → %r  (index=%d)", var.name, record.value, record.index)

price = alma.watch("price", 9.99, on_change=[audit])
price.set(12.49)   # logs immediately
```

### Freeze a variable

```python
config = alma.watch("config", {"debug": True})
config.freeze()
config.set({"debug": False})   # raises FrozenError
```

### Working with the registry directly

```python
from alma import AlmaRegistry

reg = AlmaRegistry()
x = reg.watch("x", 0)
y = reg.watch("y", 100)

print(reg.snapshot())      # {"x": 0, "y": 100}
print(reg.all_vars())      # {"x": AlmaVar(...), "y": AlmaVar(...)}
reg.unregister("x")
```

---

## Running tests

```bash
pip install pytest
pytest test_alma.py -v
```

---

## Suggested future extensions

| Extension | Description |
|---|---|
| **Persistence** | `var.save(path)` / `AlmaVar.load(path)` via JSON or pickle — persist history across restarts |
| **Branching history** | Git-like branches so rollbacks don't destroy the forward path |
| **Async support** | `async def set_async(...)` + `asyncio.Lock` for use inside async event loops |
| **`AlmaNamespace`** | Dict-like container that watches every key independently |
| **Middleware / interceptors** | Transform values before storage (e.g., encryption, compression) |
| **Remote sync** | Publish changes to Redis pub/sub or a WebSocket bus for distributed state |
| **Typed generics + Pydantic** | `AlmaVar[MyModel]` with automatic schema validation |
| **Diff engine** | Deep structural diffing for dicts/lists powered by `deepdiff` |
| **Time-windowed history** | Automatically prune records older than N seconds/minutes |
| **Export / replay** | Replay a recorded history against a function for deterministic testing |
| **CLI inspector** | `alma inspect <pickle_file>` to explore history in the terminal |
| **Jupyter widget** | Interactive timeline slider to scrub variable state in notebooks |
