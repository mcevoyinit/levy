"""Tests for the @levy decorator — the core of the library."""

import pytest
from fastapi import FastAPI, Request
from httpx import AsyncClient, ASGITransport
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
        configure(LevyConfig(secret_key="different"))
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
