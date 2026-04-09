"""End-to-end blackbox tests for Levy.

Real FastAPI app, real ASGI transport, real HTTP requests.
The only mock is mpp.charge() for the happy path (can't do real
on-chain payments in a test). Everything else is real.
"""

import inspect
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from httpx import AsyncClient, ASGITransport
from mpp import Credential, ChallengeEcho, Receipt

from levy import levy, LevyConfig
from levy.decorator import configure, _get_mpp


# ── Test config ───────────────────────────────────────────────

TEST_CONFIG = LevyConfig(
    recipient="0xB02abaA5FD4Caf4E16b7583232cddbE43BeC66AF",
    secret_key="e2e-test-secret",
    chain_id=42431,
    rpc_url="https://rpc.moderato.tempo.xyz",
)


def make_app() -> FastAPI:
    """Build a fresh FastAPI app with free + paid endpoints."""
    configure(TEST_CONFIG)
    app = FastAPI()

    @app.get("/free")
    async def free():
        return {"message": "no payment needed"}

    @app.get("/cheap")
    @levy("0.01", description="Cheap endpoint")
    async def cheap(request: Request, *, credential, receipt):
        return {
            "paid": True,
            "amount": "0.01",
            "payer": credential.source,
        }

    @app.get("/expensive")
    @levy("0.50", description="Premium endpoint")
    async def expensive(request: Request, *, credential, receipt):
        return {
            "paid": True,
            "amount": "0.50",
            "receipt": receipt.reference,
        }

    @app.get("/with-query")
    @levy("0.05")
    async def with_query(request: Request, q: str = "default", *, credential, receipt):
        return {"query": q, "paid": True}

    return app


# ── 402 Challenge Flow ────────────────────────────────────────

class TestPaymentRequired:
    """Unauthenticated requests get 402 with proper WWW-Authenticate."""

    @pytest.mark.asyncio
    async def test_free_endpoint_returns_200(self):
        app = make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/free")
        assert r.status_code == 200
        assert r.json()["message"] == "no payment needed"

    @pytest.mark.asyncio
    async def test_cheap_without_auth_returns_402(self):
        app = make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/cheap")
        assert r.status_code == 402
        assert "WWW-Authenticate" in r.headers

    @pytest.mark.asyncio
    async def test_expensive_without_auth_returns_402(self):
        app = make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/expensive")
        assert r.status_code == 402

    @pytest.mark.asyncio
    async def test_402_body_structure(self):
        app = make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/cheap")
        body = r.json()
        assert body["error"] == "Payment Required"
        assert body["amount"] == "0.01"
        assert body["endpoint"] == "cheap"

    @pytest.mark.asyncio
    async def test_www_authenticate_header_format(self):
        """Header must follow MPP spec: Payment scheme with tempo method."""
        app = make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/expensive")

        www_auth = r.headers["WWW-Authenticate"]
        assert www_auth.startswith("Payment "), f"Expected 'Payment ' prefix, got: {www_auth[:20]}"
        assert 'method="tempo"' in www_auth
        assert 'intent="charge"' in www_auth
        assert 'id="' in www_auth
        assert 'request="' in www_auth

    @pytest.mark.asyncio
    async def test_different_endpoints_different_amounts(self):
        app = make_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r_cheap = await client.get("/cheap")
            r_expensive = await client.get("/expensive")

        assert r_cheap.json()["amount"] == "0.01"
        assert r_expensive.json()["amount"] == "0.50"


# ── Happy Path (mocked charge, real everything else) ──────────

def make_mock_mpp():
    """Create a mock Mpp that returns real Credential/Receipt objects."""
    echo = ChallengeEcho(
        id="e2e-test-id",
        realm="e2e",
        method="tempo",
        intent="charge",
        request="eyJ0ZXN0IjoxfQ",
    )
    credential = Credential(
        challenge=echo,
        payload={"type": "transaction", "signature": "0xdeadbeef"},
        source="0x" + "42" * 20,
    )
    receipt = Receipt(
        status="success",
        timestamp=datetime.now(UTC),
        reference="0x" + "ee" * 32,
        method="tempo",
    )
    mock_mpp = AsyncMock()
    mock_mpp.charge = AsyncMock(return_value=(credential, receipt))
    return mock_mpp, credential, receipt


class TestHappyPath:
    """Payment succeeds → handler receives credential and receipt."""

    @pytest.mark.asyncio
    async def test_valid_payment_returns_200(self):
        mock_mpp, cred, rcpt = make_mock_mpp()

        with patch("levy.decorator._get_mpp", return_value=mock_mpp):
            app = make_app()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/cheap",
                    headers={"Authorization": "Payment valid-credential"},
                )

        assert r.status_code == 200
        body = r.json()
        assert body["paid"] is True
        assert body["payer"] == cred.source

    @pytest.mark.asyncio
    async def test_receipt_reference_passed_to_handler(self):
        mock_mpp, cred, rcpt = make_mock_mpp()

        with patch("levy.decorator._get_mpp", return_value=mock_mpp):
            app = make_app()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/expensive",
                    headers={"Authorization": "Payment valid-credential"},
                )

        assert r.status_code == 200
        assert r.json()["receipt"] == rcpt.reference

    @pytest.mark.asyncio
    async def test_query_params_preserved_with_payment(self):
        mock_mpp, _, _ = make_mock_mpp()

        with patch("levy.decorator._get_mpp", return_value=mock_mpp):
            app = make_app()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/with-query?q=hello",
                    headers={"Authorization": "Payment valid-credential"},
                )

        assert r.status_code == 200
        assert r.json()["query"] == "hello"


# ── Error Handling ────────────────────────────────────────────

class TestErrorHandling:
    """charge() exceptions return 500 without leaking internals."""

    @pytest.mark.asyncio
    async def test_charge_exception_returns_500(self):
        mock_mpp = AsyncMock()
        mock_mpp.charge = AsyncMock(side_effect=RuntimeError("RPC node unreachable"))

        with patch("levy.decorator._get_mpp", return_value=mock_mpp):
            app = make_app()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/cheap",
                    headers={"Authorization": "Payment some-credential"},
                )

        assert r.status_code == 500
        body = r.json()
        assert body["error"] == "Internal Server Error"
        assert "detail" not in body  # no internal leak
        assert "RPC node unreachable" not in str(body)  # exception text hidden


# ── Decorator Mechanics ───────────────────────────────────────

class TestDecoratorMechanics:
    """@levy preserves function metadata and strips injected params."""

    def test_preserves_function_name(self):
        app = make_app()
        # Get the endpoint function from the app's routes
        for route in app.routes:
            if hasattr(route, "endpoint") and route.path == "/cheap":
                assert route.endpoint.__name__ == "cheap"
                break

    def test_strips_credential_receipt_from_signature(self):
        configure(TEST_CONFIG)

        @levy("0.01")
        async def my_endpoint(request: Request, *, credential, receipt):
            """My doc."""
            pass

        sig = inspect.signature(my_endpoint)
        param_names = list(sig.parameters.keys())
        assert "credential" not in param_names
        assert "receipt" not in param_names
        assert "request" in param_names

    def test_preserves_docstring(self):
        configure(TEST_CONFIG)

        @levy("0.01")
        async def documented(request: Request, *, credential, receipt):
            """This is my docstring."""
            pass

        assert documented.__doc__ == "This is my docstring."

    def test_levy_metadata_accessible(self):
        configure(TEST_CONFIG)

        @levy("0.01", description="Test")
        async def meta_test(request: Request, *, credential, receipt):
            pass

        assert hasattr(meta_test, "__wrapped__")
        assert meta_test.__wrapped__._levy_amount == "0.01"
