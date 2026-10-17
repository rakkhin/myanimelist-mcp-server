import asyncio
import os

import httpx
import pytest

from utils import auth
from utils.token_store import TokenStore


def test_no_mal_environment_variables_are_visible():
    assert [name for name in os.environ if name.startswith("MAL_")] == []


def test_auth_settings_do_not_come_from_dotenv():
    assert auth.CLIENT_ID is None
    assert auth.CLIENT_SECRET is None
    with pytest.raises(ValueError, match="MAL_CLIENT_ID"):
        auth._require_client_id()
    with pytest.raises(ValueError, match="MAL_CLIENT_SECRET"):
        auth._require_client_secret()


def test_default_token_store_is_in_temporary_directory(tmp_path):
    assert TokenStore.default_path().is_relative_to(tmp_path)


def test_real_http_requests_are_blocked():
    async def fetch():
        async with httpx.AsyncClient() as client:
            await client.get("https://myanimelist.net")

    with pytest.raises(AssertionError, match="real HTTP request"):
        asyncio.run(fetch())
