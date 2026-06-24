# Storage Backends

Workforce Runtime uses a small persistence boundary instead of depending on
SQLite directly throughout the runtime.

## Current Backend

`SQLiteStore` is the only supported backend today. It implements the
`RuntimeStore` protocol in `workforce_runtime/storage/base.py`.

The default runtime path still works as before:

```python
from workforce_runtime.server.runtime import WorkforceRuntime

with WorkforceRuntime(".workforce_runtime/runtime.sqlite") as runtime:
    ...
```

For tests or custom embedding, a store can be injected:

```python
from workforce_runtime.server.runtime import WorkforceRuntime
from workforce_runtime.storage import SQLiteStore

store = SQLiteStore(".workforce_runtime/runtime.sqlite")
runtime = WorkforceRuntime(store=store)
```

When a store is injected, the caller owns its lifecycle. When no store is
injected, `WorkforceRuntime` creates and closes the default SQLite store.

## Adding MySQL Later

A future MySQL backend should:

1. Implement every method in `RuntimeStore`.
2. Preserve list ordering semantics used by the dashboard and event stream.
3. Keep `save_event()` append-only and preserve unique `event_id`.
4. Implement atomic queue claim/update behavior for `WorkItem`.
5. Register the adapter in `workforce_runtime/storage/factory.py`.

Call sites should depend on `RuntimeStore`, not a concrete database class.
