"""
QuantForce Apex — Base Strategy (Layer 2 interface)
All strategy plugins must inherit this class.
"""
from abc import ABC, abstractmethod
from typing import Optional, List
from core.interfaces import Signal, Bar, ProductType


class BaseStrategy(ABC):
    """
    All strategies inherit this. Implement on_bar() and optionally on_news().
    """
    PRODUCT: ProductType = ProductType.STOCK
    PARAMS:  dict        = {}

    def __init__(self, params: dict = None):
        # Merge defaults with provided params
        self.params = {**self.PARAMS, **(params or {})}

    @abstractmethod
    def on_bar(self, symbol: str, bars: List[Bar], ctx: dict) -> Optional[Signal]:
        """Called on every new bar. Return Signal or None."""
        ...

    def on_news(self, symbol: str, score: float, ctx: dict) -> Optional[Signal]:
        """Called when news score is available. Override if strategy uses news."""
        return None

    def __repr__(self):
        return f"{self.__class__.__name__}(params={self.params})"
