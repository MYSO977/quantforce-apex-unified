"""
QuantForce Apex — Strategy Registry
Auto-discovery via @strategy_registry.register decorator.
"""
import logging
from typing import Dict, Type
from strategies.base import BaseStrategy

logger = logging.getLogger("StrategyRegistry")


class StrategyRegistry:
    _registry: Dict[str, Type[BaseStrategy]] = {}

    def register(self, strategy_id: str):
        """Decorator to register a strategy class."""
        def decorator(cls):
            self._registry[strategy_id] = cls
            logger.debug(f"Registered strategy: {strategy_id} -> {cls.__name__}")
            return cls
        return decorator

    def load(self, configs: list) -> list:
        """
        Load strategies from config list.
        configs = [{"id": "momentum_swing", "enabled": True, "shadow": False, "params": {...}}]
        """
        loaded = []
        for cfg in configs:
            sid     = cfg.get("id")
            enabled = cfg.get("enabled", True)
            shadow  = cfg.get("shadow", False)
            params  = cfg.get("params", {})

            if not enabled:
                logger.info(f"Strategy {sid}: disabled, skipping")
                continue

            cls = self._registry.get(sid)
            if cls is None:
                logger.warning(f"Strategy {sid}: not found in registry")
                continue

            instance = cls(params=params)
            instance._shadow = shadow
            loaded.append(instance)
            logger.info(f"Loaded strategy: {sid} (shadow={shadow})")

        return loaded

    def list_all(self):
        return list(self._registry.keys())


# Global singleton
strategy_registry = StrategyRegistry()
