from __future__ import annotations

from pathlib import Path
from typing import Callable

from workforce_runtime.storage.base import RuntimeStore
from workforce_runtime.storage.sqlite_store import SQLiteStore


RuntimeStoreFactory = Callable[[str | Path], RuntimeStore]


def create_runtime_store(backend: str, path: str | Path) -> RuntimeStore:
    normalized = backend.strip().lower()
    if normalized in {"", "sqlite", "sqlite3"}:
        return SQLiteStore(path)
    raise ValueError(f"unsupported runtime store backend: {backend!r}; currently supported: sqlite")


def runtime_store_factory(backend: str = "sqlite") -> RuntimeStoreFactory:
    def _factory(path: str | Path) -> RuntimeStore:
        return create_runtime_store(backend, path)

    return _factory
