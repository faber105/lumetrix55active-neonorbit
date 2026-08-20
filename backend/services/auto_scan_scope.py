from __future__ import annotations

from threading import RLock

_lock = RLock()
_assets: tuple[str, ...] = ()
_discovered_count: int = 0


def set_auto_scan_scope(assets: list[str] | tuple[str, ...], discovered_count: int) -> None:
    global _assets, _discovered_count
    normalized = tuple(dict.fromkeys(str(asset) for asset in assets if asset))
    with _lock:
        _assets = normalized
        _discovered_count = max(0, int(discovered_count or 0))


def get_auto_scan_scope() -> tuple[tuple[str, ...], int]:
    with _lock:
        return _assets, _discovered_count
