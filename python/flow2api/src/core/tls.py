"""Outbound TLS verification policy.

Certificate verification is enabled by default. It may only be disabled
explicitly, for deployments that sit behind a TLS-inspecting proxy:

    [flow]
    tls_verify = false

or with ``GFLOW_TLS_VERIFY=0`` in the environment. Prefer trusting a private
CA instead of disabling verification:

    [flow]
    tls_ca_bundle = "C:/certs/corporate-root.pem"

The helpers return the value expected by ``curl_cffi``/``requests``:
``True`` (system trust store), ``False`` (no verification) or a CA bundle path.
"""

from __future__ import annotations

import os
from typing import Any

_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_warned = False


def _config() -> Any:
    try:
        from .config import config

        return config
    except Exception:
        return None


def tls_verify_enabled() -> bool:
    """Whether outbound TLS certificates must be verified."""
    env = os.environ.get("GFLOW_TLS_VERIFY")
    if env is not None and env.strip():
        return env.strip().lower() not in _FALSE_VALUES
    cfg = _config()
    if cfg is None:
        return True
    return bool(cfg.tls_verify)


def tls_ca_bundle() -> str:
    """Optional CA bundle path; empty means the system trust store."""
    env = os.environ.get("GFLOW_TLS_CA_BUNDLE")
    if env and env.strip():
        return env.strip()
    cfg = _config()
    return cfg.tls_ca_bundle if cfg is not None else ""


def tls_verify_param() -> Any:
    """Value for the ``verify=`` argument of an HTTP client."""
    if not tls_verify_enabled():
        _warn_once()
        return False
    return tls_ca_bundle() or True


def _warn_once() -> None:
    global _warned
    if _warned:
        return
    _warned = True
    try:
        from .logger import debug_logger

        debug_logger.log_warning(
            "[TLS] Certificate verification is disabled (flow.tls_verify=false "
            "or GFLOW_TLS_VERIFY=0); outbound traffic can be intercepted."
        )
    except Exception:
        pass
