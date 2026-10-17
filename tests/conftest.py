import os
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import auth


@pytest.fixture(autouse=True)
def isolate_from_local_environment(monkeypatch, tmp_path):
    """Keep values from .env, the real token store and the network out of tests.

    load_dotenv() runs when tools and auth are imported, so .env values are in
    os.environ and in auth's module-level settings for the whole session.
    """
    for name in list(os.environ):
        if name.startswith("MAL_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))

    # Module-level copies taken at import; values match utils/auth.py defaults.
    monkeypatch.setattr(auth, "CLIENT_ID", None)
    monkeypatch.setattr(auth, "CLIENT_SECRET", None)
    monkeypatch.setattr(auth, "REDIRECT_URI", "http://localhost:8080/callback")
    monkeypatch.setattr(auth, "CALLBACK_HOST", "127.0.0.1")
    monkeypatch.setattr(auth, "CALLBACK_PORT", 8080)
    monkeypatch.setattr(auth, "CALLBACK_TIMEOUT_SECONDS", 300.0)
    monkeypatch.setattr(auth, "PKCE_METHOD", "plain")

    async def no_network(self, request, **kwargs):
        raise AssertionError(
            f"Test attempted a real HTTP request to {request.url}"
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
