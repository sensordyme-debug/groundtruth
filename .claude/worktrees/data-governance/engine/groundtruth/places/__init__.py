"""Third-party place intelligence: Foursquare, Yelp and Google adapters behind one PlaceRecord model, a usage
ledger with spend caps, cross-provider resolution and the ``search_places`` orchestrator.

Provider data is provider-reported evidence, not ground truth. See docs/API_PROVIDER_MATRIX.md and
docs/compliance/API_TERMS_REVIEW.md before enabling or displaying any provider.
"""
from .base import PlaceRecord, Provider, ProviderError, SpendCapExceeded, UsageLedger, load_conf
from .orchestrate import build_providers, parse_area, search_places
from .resolve import resolve

__all__ = ["PlaceRecord", "Provider", "ProviderError", "SpendCapExceeded", "UsageLedger", "load_conf",
           "build_providers", "parse_area", "search_places", "resolve"]
