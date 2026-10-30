"""全局域 WatchProvider。"""
from .base import BaseProvider, ProviderFrame, ProviderTarget, WatchProvider
from .core import CoreProvider, PlatformProvider

__all__ = [
    "BaseProvider", "ProviderFrame", "ProviderTarget", "WatchProvider",
    "CoreProvider", "PlatformProvider",
]
