"""Vercel serverless entry point: serves the FastAPI application from ``backend/``."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.main import app

__all__ = ["app"]
