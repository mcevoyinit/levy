"""Levy configuration — reads from env vars or explicit params."""

from __future__ import annotations

import os
from dataclasses import dataclass

from pytempo.contracts.addresses import PATH_USD


@dataclass
class LevyConfig:
    """Global config for Levy payment middleware."""

    recipient: str = ""        # who receives payments
    secret_key: str = ""       # HMAC secret for challenge IDs
    realm: str = ""            # protection space (auto-detected from host if empty)
    currency: str = PATH_USD   # TIP-20 token address
    chain_id: int = 4217       # Tempo mainnet
    rpc_url: str = "https://rpc.tempo.xyz"
    description: str = ""      # human-readable description for challenges

    @classmethod
    def from_env(cls) -> LevyConfig:
        return cls(
            recipient=os.environ.get("LEVY_RECIPIENT", ""),
            secret_key=os.environ.get("LEVY_SECRET_KEY", "levy-dev-secret"),
            realm=os.environ.get("LEVY_REALM", ""),
            currency=os.environ.get("LEVY_CURRENCY", PATH_USD),
            chain_id=int(os.environ.get("LEVY_CHAIN_ID", "4217")),
            rpc_url=os.environ.get("LEVY_RPC_URL", "https://rpc.tempo.xyz"),
            description=os.environ.get("LEVY_DESCRIPTION", ""),
        )
