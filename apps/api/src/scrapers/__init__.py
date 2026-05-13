"""Scraper registry.

Adding a new upstream catalogue = new module under ``src/scrapers/<source>.py``
implementing :class:`~src.scrapers.base.BaseScraper`, plus one line here.
The runner picks scrapers up by their ``source`` ClassVar — no string
literals scattered across the codebase.

Concrete scrapers land in Faz 1; the registry is empty for now so the
foundation lands clean and CI stays green.
"""

from __future__ import annotations

from src.scrapers.base import (
    BaseScraper,
    CallLifecycleStatus,
    CallSource,
    EligibilityTag,
    NormalizedCall,
    ScraperRunResult,
)

# Concrete scrapers register here. The order in this dict is the order
# Celery beat dispatches them in :mod:`src.tasks.scheduling`; put cheap,
# fast sources first so a daily run gets useful data even if a slower
# scraper later in the queue times out.
SCRAPER_REGISTRY: dict[CallSource, type[BaseScraper]] = {}


def register_scraper(scraper_cls: type[BaseScraper]) -> type[BaseScraper]:
    """Class decorator: add ``scraper_cls`` to the registry.

    Usage::

        @register_scraper
        class EUFTPortalScraper(BaseScraper):
            source = "eu_ft_portal"
            ...

    The registry rejects duplicate ``source`` values so two modules can't
    silently shadow each other.
    """

    source = scraper_cls.source
    if source in SCRAPER_REGISTRY:
        existing = SCRAPER_REGISTRY[source].__module__
        raise ValueError(
            f"Scraper for source={source!r} already registered "
            f"({existing} vs {scraper_cls.__module__})"
        )
    SCRAPER_REGISTRY[source] = scraper_cls
    return scraper_cls


def get_scraper(source: CallSource) -> type[BaseScraper]:
    """Look up a scraper class by source; raise on unknown."""

    if source not in SCRAPER_REGISTRY:
        raise KeyError(f"No scraper registered for source={source!r}")
    return SCRAPER_REGISTRY[source]


# Concrete scraper modules are imported below so their @register_scraper
# decorators fire at package-import time (mirrors the programs registry
# pattern). Foundation lands with no concrete scrapers; Faz 1.1 onwards
# adds them here.


__all__ = [
    "BaseScraper",
    "CallLifecycleStatus",
    "CallSource",
    "EligibilityTag",
    "NormalizedCall",
    "SCRAPER_REGISTRY",
    "ScraperRunResult",
    "get_scraper",
    "register_scraper",
]
