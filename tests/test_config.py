"""Tests for LevyConfig."""

import os
from levy.config import LevyConfig
from pytempo.contracts.addresses import PATH_USD


class TestDefaults:
    def test_default_chain_id(self):
        c = LevyConfig()
        assert c.chain_id == 4217

    def test_default_currency(self):
        c = LevyConfig()
        assert c.currency == PATH_USD

    def test_default_rpc(self):
        c = LevyConfig()
        assert c.rpc_url == "https://rpc.tempo.xyz"

    def test_custom_values(self):
        c = LevyConfig(recipient="0xabc", secret_key="s", chain_id=42431)
        assert c.recipient == "0xabc"
        assert c.secret_key == "s"
        assert c.chain_id == 42431


class TestFromEnv:
    def test_reads_env(self):
        os.environ["LEVY_RECIPIENT"] = "0xtest"
        os.environ["LEVY_SECRET_KEY"] = "env-secret"
        os.environ["LEVY_CHAIN_ID"] = "42431"
        try:
            c = LevyConfig.from_env()
            assert c.recipient == "0xtest"
            assert c.secret_key == "env-secret"
            assert c.chain_id == 42431
        finally:
            os.environ.pop("LEVY_RECIPIENT", None)
            os.environ.pop("LEVY_SECRET_KEY", None)
            os.environ.pop("LEVY_CHAIN_ID", None)

    def test_defaults_without_env(self):
        for key in ["LEVY_RECIPIENT", "LEVY_SECRET_KEY", "LEVY_CHAIN_ID"]:
            os.environ.pop(key, None)
        c = LevyConfig.from_env()
        assert c.chain_id == 4217
        assert c.secret_key == "levy-dev-secret"
