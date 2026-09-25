"""SignalNYC explorer API: read-only FastAPI app over the pilot snapshot."""

from .app import create_app

__all__ = ["create_app"]
