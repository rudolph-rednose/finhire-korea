"""Vercel Python Function entrypoint for FinHire Korea."""
from app import App


class handler(App):
    """Explicit handler declaration so Vercel detects this Python Function."""

