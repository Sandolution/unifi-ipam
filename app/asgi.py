"""Uvicorn entry point for UniFi IPAM: `uvicorn app.asgi:app`."""

import logging

from app.main import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
app = create_app()
