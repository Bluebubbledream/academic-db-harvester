"""Site adapter registry for multi-source academic harvesting.

Two families live here:
  * browser-backed adapters  (ieee, sciencedirect, wos) - need Edge + CDP + login
  * API adapters             (openalex)                 - plain HTTPS, no browser

`requires_browser` on the adapter class decides which path `harvester.py` takes,
so adding a source never means touching the pipeline.
"""

import importlib

SITE_REGISTRY = {
    "ieee": ("sites.ieee", "IEEESite"),
    "sciencedirect": ("sites.sciencedirect", "ScienceDirectSite"),
    "wos": ("sites.wos", "WOSSite"),
    "openalex": ("sites.openalex", "OpenAlexSite"),
    "arxiv": ("sites.arxiv", "ArxivSite"),
}


def list_sources() -> list[str]:
    """Return available source names."""
    return list(SITE_REGISTRY.keys())


def needs_browser(name: str) -> bool:
    """True when the source needs Edge/CDP; False for pure API sources."""
    key = (name or "").strip().lower()
    if key not in SITE_REGISTRY:
        return True
    return bool(getattr(get_adapter(key), "requires_browser", True))


def get_adapter(name: str):
    """Return the adapter class for a source name."""
    key = (name or "ieee").strip().lower()
    if key not in SITE_REGISTRY:
        raise KeyError(f"Unknown source '{name}'. Available: {', '.join(SITE_REGISTRY)}")
    module_name, class_name = SITE_REGISTRY[key]
    module = importlib.import_module(module_name)
    return getattr(module, class_name)