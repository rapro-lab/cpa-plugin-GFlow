"""API modules.

The routers are resolved lazily (PEP 562). The stdio worker only needs
``routes``; importing ``admin`` eagerly made every ``import src.api`` pull in
the browser dashboard router, which the frozen worker never serves. Keeping the
package import side-effect free also lets the bundle drop ``src.api.admin``.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import APIRouter

__all__ = ["api_router", "admin_router"]

_ROUTER_MODULES = {"api_router": ".routes", "admin_router": ".admin"}


def __getattr__(name: str) -> Any:
    module = _ROUTER_MODULES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    router = getattr(import_module(module, __name__), "router")
    globals()[name] = router
    return router


def __dir__() -> list:
    return sorted(set(globals()) | set(__all__))
