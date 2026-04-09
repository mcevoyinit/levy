"""Levy — one decorator to monetize any FastAPI endpoint via Tempo/MPP."""

from levy.decorator import levy
from levy.config import LevyConfig

__all__ = ["levy", "LevyConfig"]
