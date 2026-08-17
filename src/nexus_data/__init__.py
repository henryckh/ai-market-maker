"""Nexus Data — live Skills API and historical offline provider.

Agents consume ``shared_memory["nexus"]`` only. Use::

    from nexus_data.provider import resolve_nexus_provider
    bundle = resolve_nexus_provider(run_mode=...).get_bundle(...)
"""

from nexus_data.client import NexusDataClient, NexusDataConfig
from nexus_data.feeds import nexus_feeds_enabled
from nexus_data.provider import NexusContextProvider, resolve_nexus_provider

__all__ = [
    "NexusDataClient",
    "NexusDataConfig",
    "nexus_feeds_enabled",
    "NexusContextProvider",
    "resolve_nexus_provider",
]
