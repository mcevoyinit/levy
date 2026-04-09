"""The @levy decorator — one line to monetize a FastAPI endpoint."""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any, Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse

from mpp import Challenge, Credential, Receipt, format_www_authenticate
from mpp.server import Mpp
from mpp.methods.tempo import tempo, ChargeIntent

from .config import LevyConfig

logger = logging.getLogger("levy")

# kwargs injected by levy, hidden from FastAPI's signature inspection
_INJECTED_PARAMS = {"credential", "receipt"}

# Module-level Mpp instance, lazily initialized
_mpp: Mpp | None = None
_config: LevyConfig | None = None


def _get_mpp(config: LevyConfig | None = None) -> Mpp:
    """Get or create the Mpp server instance."""
    global _mpp, _config
    if _mpp is not None and config is None:
        return _mpp

    cfg = config or _config or LevyConfig.from_env()
    _config = cfg

    recipient = cfg.recipient or None
    if not recipient:
        logger.warning(
            "LEVY_RECIPIENT is not set. All @levy endpoints will fail with "
            "a configuration error until a recipient address is provided."
        )

    _mpp = Mpp.create(
        secret_key=cfg.secret_key,
        method=tempo(
            intents={"charge": ChargeIntent(
                chain_id=cfg.chain_id,
                rpc_url=cfg.rpc_url,
            )},
            recipient=recipient,
            currency=cfg.currency,
            chain_id=cfg.chain_id,
            rpc_url=cfg.rpc_url,
        ),
    )
    return _mpp


def configure(config: LevyConfig) -> None:
    """Set global Levy config. Call before any @levy decorators execute."""
    global _config, _mpp
    _config = config
    _mpp = None  # force re-creation


def levy(
    amount: str,
    *,
    description: str | None = None,
    config: LevyConfig | None = None,
) -> Callable:
    """Decorator that requires MPP payment to access a FastAPI endpoint.

    Usage:
        @app.get("/search")
        @levy("0.01")
        async def search(request: Request, query: str, *, credential, receipt):
            return {"results": [...], "paid_by": credential.source}

    The decorated function receives two extra kwargs:
        - credential: mpp.Credential with payer info
        - receipt: mpp.Receipt confirming the payment

    Args:
        amount: Price per call in human units (e.g. "0.01" for $0.01).
        description: Optional description shown in the 402 challenge.
        config: Override global config for this endpoint.
    """

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        # Store price metadata on the function for discovery
        func._levy_amount = amount  # type: ignore[attr-defined]
        func._levy_description = description  # type: ignore[attr-defined]

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Extract the Request object from args/kwargs
            request = _find_request(args, kwargs)

            mpp_server = _get_mpp(config)
            auth_header = request.headers.get("authorization") if request else None

            # Determine realm from request
            cfg = config or _config or LevyConfig.from_env()
            realm = cfg.realm
            if not realm and request:
                realm = request.headers.get("host", "levy")

            try:
                result = await mpp_server.charge(
                    authorization=auth_header,
                    amount=amount,
                    description=description or cfg.description or f"Levy: {func.__name__}",
                )
            except Exception as exc:
                logger.exception("levy: charge() failed for %s", func.__name__)
                return JSONResponse(
                    {"error": "Internal Server Error",
                     "detail": str(exc)},
                    status_code=500,
                )

            if isinstance(result, Challenge):
                www_auth = format_www_authenticate(result, realm=realm)
                return JSONResponse(
                    {"error": "Payment Required", "amount": amount,
                     "endpoint": func.__name__},
                    status_code=402,
                    headers={"WWW-Authenticate": www_auth},
                )

            credential, receipt = result
            kwargs["credential"] = credential
            kwargs["receipt"] = receipt
            return await func(*args, **kwargs)

        # Strip credential/receipt from the wrapper's signature so FastAPI
        # doesn't try to resolve them as query parameters
        orig_sig = inspect.signature(func)
        visible_params = [
            p for name, p in orig_sig.parameters.items()
            if name not in _INJECTED_PARAMS
        ]
        wrapper.__signature__ = orig_sig.replace(parameters=visible_params)

        return wrapper
    return decorator


def _find_request(args: tuple, kwargs: dict) -> Request | None:
    """Find the FastAPI Request object in function arguments."""
    for arg in args:
        if isinstance(arg, Request):
            return arg
    for v in kwargs.values():
        if isinstance(v, Request):
            return v
    return None
