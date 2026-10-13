"""Site adapter registry for multi-source academic harvesting."""

import importlib

SITE_REGISTRY = {
    "ieee": ("sites.ieee", "IEEESite"),
    "sciencedirect": ("sites.sciencedirect", "ScienceDirectSite"),
    "wos": ("sites.wos", "WOSSite"),
}


def list_sources() -> list[str]:
    """Return available source names."""
    return list(SITE_REGISTRY.keys())


def get_adapter(name: str):
    """Return the adapter class for a source name."""
    key = (name or "ieee").strip().lower()
    if key not in SITE_REGISTRY:
        raise KeyError(f"Unknown source '{name}'. Available: {', '.join(SITE_REGISTRY)}")
    module_name, class_name = SITE_REGISTRY[key]
    module = importlib.import_module(module_name)
    return getattr(module, class_name)
