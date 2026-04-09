"""Tests for the @levy decorator — the core of the library."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from httpx import AsyncClient, ASGITransport
from mpp import Credential, ChallengeEcho, Receipt
from mpp.methods.tempo import TempoAccount

from levy import levy, LevyConfig
from levy.decorator import configure, _get_mpp


# Test config — testnet, no real money
TEST_CONFIG = LevyConfig(
    recipient=TempoAccount.from_key("0x" + "cd" * 32).address,
    secret_key="test-levy-secret",
    chain_id=42431,
    rpc_url="https://rpc.moderato.tempo.xyz",
)


def make_app() -> FastAPI:
    configure(TEST_CONFIG)
    app = FastAPI()

    @app.get("/free")
    async def free():
        return {"free": True}

    @app.get("/paid")
    @levy("0.01", description="Test endpoint")
    async def paid(request: Request, *, credential, receipt):
        return {
            "paid": True,
            "amount": "0.01",
            "payer": credential.source,
            "receipt_ref": receipt.reference,
        }

    @app.get("/expensive")
    @levy("1.00")
    async def expensive(request: Request, *, credential, receipt):
        return {"tier": "premium", "paid": True}

    @app.get("/with-params")
    @levy("0.05")
    async def with_params(request: Request, query: str = "default", *, credential, receipt):
        return {"query": query, "paid": True}

    return app


@pytest.fixture
def app():
    return make_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestFreeEndpoints:
    @pytest.mark.asyncio
    async def test_free_endpoint_works(self, client):
        r = await client.get("/free")
        assert r.status_code == 200
        assert r.json()["free"] is True


class TestPaymentRequired:
    @pytest.mark.asyncio
    async def test_returns_402_without_auth(self, client):
        r = await client.get("/paid")
        assert r.status_code == 402

    @pytest.mark.asyncio
    async def test_402_has_www_authenticate_header(self, client):
        r = await client.get("/paid")
        assert "WWW-Authenticate" in r.headers
        assert r.headers["WWW-Authenticate"].startswith("Payment ")

    @pytest.mark.asyncio
    async def test_402_body_has_error(self, client):
        r = await client.get("/paid")
        body = r.json()
        assert body["error"] == "Payment Required"
        assert body["amount"] == "0.01"

    @pytest.mark.asyncio
    async def test_402_body_has_endpoint_name(self, client):
        r = await client.get("/paid")
        assert r.json()["endpoint"] == "paid"

    @pytest.mark.asyncio
    async def test_expensive_also_402(self, client):
        r = await client.get("/expensive")
        assert r.status_code == 402
        assert "WWW-Authenticate" in r.headers

    @pytest.mark.asyncio
    async def test_with_params_also_402(self, client):
        r = await client.get("/with-params?query=hello")
        assert r.status_code == 402


class TestChallengeFormat:
    @pytest.mark.asyncio
    async def test_challenge_contains_method(self, client):
        r = await client.get("/paid")
        www_auth = r.headers["WWW-Authenticate"]
        assert 'method="tempo"' in www_auth

    @pytest.mark.asyncio
    async def test_challenge_contains_intent(self, client):
        r = await client.get("/paid")
        www_auth = r.headers["WWW-Authenticate"]
        assert 'intent="charge"' in www_auth

    @pytest.mark.asyncio
    async def test_challenge_has_id(self, client):
        r = await client.get("/paid")
        www_auth = r.headers["WWW-Authenticate"]
        assert 'id="' in www_auth

    @pytest.mark.asyncio
    async def test_challenge_has_request(self, client):
        r = await client.get("/paid")
        www_auth = r.headers["WWW-Authenticate"]
        assert 'request="' in www_auth


class TestMetadata:
    def test_levy_stores_amount_on_function(self):
        @levy("0.01")
        async def test_fn(request, *, credential, receipt):
            pass
        assert test_fn.__wrapped__._levy_amount == "0.01"

    def test_levy_stores_description(self):
        @levy("0.05", description="test desc")
        async def test_fn(request, *, credential, receipt):
            pass
        assert test_fn.__wrapped__._levy_description == "test desc"


class TestMultipleEndpoints:
    @pytest.mark.asyncio
    async def test_different_prices(self, client):
        r1 = await client.get("/paid")
        r2 = await client.get("/expensive")
        assert r1.status_code == 402
        assert r2.status_code == 402
        assert r1.json()["amount"] == "0.01"
        # expensive endpoint should have different challenge

    @pytest.mark.asyncio
    async def test_free_and_paid_coexist(self, client):
        r_free = await client.get("/free")
        r_paid = await client.get("/paid")
        assert r_free.status_code == 200
        assert r_paid.status_code == 402


class TestMppIntegration:
    def test_mpp_instance_created(self):
        configure(TEST_CONFIG)
        mpp = _get_mpp()
        assert mpp is not None

    def test_mpp_reuses_instance(self):
        configure(TEST_CONFIG)
        m1 = _get_mpp()
        m2 = _get_mpp()
        assert m1 is m2

    def test_reconfigure_creates_new_instance(self):
        configure(TEST_CONFIG)
        m1 = _get_mpp()
        configure(LevyConfig(recipient=TEST_CONFIG.recipient, secret_key="different"))
        m2 = _get_mpp()
        assert m1 is not m2


class TestInvalidAuth:
    @pytest.mark.asyncio
    async def test_garbage_auth_returns_402(self, client):
        r = await client.get("/paid", headers={"Authorization": "garbage"})
        # Should still return 402 since auth is invalid
        assert r.status_code == 402

    @pytest.mark.asyncio
    async def test_bearer_token_returns_402(self, client):
        r = await client.get("/paid", headers={"Authorization": "Bearer abc123"})
        assert r.status_code == 402


def _make_fake_credential_receipt():
    """Build a (Credential, Receipt) tuple for happy-path mocking."""
    echo = ChallengeEcho(
        id="test-id",
        realm="test",
        method="tempo",
        intent="charge",
        request="eyJ0ZXN0IjoxfQ",
    )
    credential = Credential(
        challenge=echo,
        payload={"type": "transaction", "signature": "0xdeadbeef"},
        source="0x" + "ab" * 20,
    )
    receipt = Receipt(
        status="success",
        timestamp=datetime.now(UTC),
        reference="0x" + "ff" * 32,
        method="tempo",
    )
    return credential, receipt


class TestHappyPath:
    """Prove that a valid payment credential → 200 with credential/receipt data."""

    @pytest.mark.asyncio
    async def test_valid_payment_returns_200(self):
        """When mpp.charge() returns (credential, receipt), the handler
        receives them and the endpoint returns 200."""
        credential, receipt = _make_fake_credential_receipt()

        mock_mpp = AsyncMock()
        mock_mpp.charge = AsyncMock(return_value=(credential, receipt))

        with patch("levy.decorator._get_mpp", return_value=mock_mpp):
            app = FastAPI()

            @app.get("/paid")
            @levy("0.01", description="Test endpoint")
            async def paid(request: Request, *, credential, receipt):
                return {
                    "paid": True,
                    "payer": credential.source,
                    "receipt_ref": receipt.reference,
                }

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/paid",
                    headers={"Authorization": "Payment test-credential"},
                )

        assert r.status_code == 200
        body = r.json()
        assert body["paid"] is True
        assert body["payer"] == credential.source
        assert body["receipt_ref"] == receipt.reference

    @pytest.mark.asyncio
    async def test_credential_and_receipt_injected_into_handler(self):
        """Verify the exact credential and receipt objects are passed through."""
        credential, receipt = _make_fake_credential_receipt()
        captured = {}

        mock_mpp = AsyncMock()
        mock_mpp.charge = AsyncMock(return_value=(credential, receipt))

        with patch("levy.decorator._get_mpp", return_value=mock_mpp):
            app = FastAPI()

            @app.get("/capture")
            @levy("0.50")
            async def capture(request: Request, *, credential, receipt):
                captured["credential_source"] = credential.source
                captured["receipt_reference"] = receipt.reference
                captured["receipt_status"] = receipt.status
                return {"ok": True}

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/capture",
                    headers={"Authorization": "Payment test-credential"},
                )

        assert r.status_code == 200
        assert captured["credential_source"] == "0x" + "ab" * 20
        assert captured["receipt_reference"] == "0x" + "ff" * 32
        assert captured["receipt_status"] == "success"

    @pytest.mark.asyncio
    async def test_happy_path_with_query_params(self):
        """Verify query params still work when payment succeeds."""
        credential, receipt = _make_fake_credential_receipt()

        mock_mpp = AsyncMock()
        mock_mpp.charge = AsyncMock(return_value=(credential, receipt))

        with patch("levy.decorator._get_mpp", return_value=mock_mpp):
            app = FastAPI()

            @app.get("/search")
            @levy("0.01")
            async def search(request: Request, query: str = "default", *, credential, receipt):
                return {"query": query, "paid": True}

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/search?query=hello",
                    headers={"Authorization": "Payment test-credential"},
                )

        assert r.status_code == 200
        body = r.json()
        assert body["query"] == "hello"
        assert body["paid"] is True


class TestChargeErrorHandling:
    """Prove that exceptions in mpp.charge() return 500 instead of crashing."""

    @pytest.mark.asyncio
    async def test_charge_exception_returns_500(self):
        """When mpp.charge() raises, the endpoint should return 500 JSON."""
        mock_mpp = AsyncMock()
        mock_mpp.charge = AsyncMock(side_effect=RuntimeError("RPC connection failed"))

        with patch("levy.decorator._get_mpp", return_value=mock_mpp):
            app = FastAPI()

            @app.get("/paid")
            @levy("0.01")
            async def paid(request: Request, *, credential, receipt):
                return {"paid": True}

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/paid",
                    headers={"Authorization": "Payment test-credential"},
                )

        assert r.status_code == 500
        body = r.json()
        assert body["error"] == "Internal Server Error"
        assert "detail" not in body

    @pytest.mark.asyncio
    async def test_charge_value_error_returns_500(self):
        """ValueError from charge() (e.g. missing recipient) should also 500."""
        mock_mpp = AsyncMock()
        mock_mpp.charge = AsyncMock(
            side_effect=ValueError("recipient must be set on the method")
        )

        with patch("levy.decorator._get_mpp", return_value=mock_mpp):
            app = FastAPI()

            @app.get("/paid")
            @levy("0.01")
            async def paid(request: Request, *, credential, receipt):
                return {"paid": True}

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get("/paid")

        assert r.status_code == 500
        body = r.json()
        assert body["error"] == "Internal Server Error"
        assert "detail" not in body
