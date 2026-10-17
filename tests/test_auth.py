import asyncio
import socketserver
import time
import urllib.parse
from unittest.mock import AsyncMock, Mock, patch

import pytest

from utils import auth
from utils.token_store import StoredTokens


def test_capture_authorization_code_binds_to_configured_host(monkeypatch):
    observed: dict[str, object] = {}

    class FakeServer:
        allow_reuse_address = False

        def __init__(self, address, handler):
            observed["address"] = address
            self.timeout = None

        def __enter__(self):
            auth.CALLBACK_CODE = "code"
            auth.CALLBACK_STATE = "expected"
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def handle_request(self):
            return None

    monkeypatch.setattr(socketserver, "TCPServer", FakeServer)
    monkeypatch.setattr(auth, "CALLBACK_HOST", "127.0.0.1")
    monkeypatch.setattr(auth, "CALLBACK_PORT", 8080)

    code = auth.capture_authorization_code("expected")

    assert code == "code"
    assert observed["address"] == ("127.0.0.1", 8080)


def test_capture_authorization_code_ignores_empty_probe_requests(monkeypatch):
    calls = {"count": 0}

    class FakeServer:
        allow_reuse_address = False

        def __init__(self, address, handler):
            self.timeout = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def handle_request(self):
            calls["count"] += 1
            if calls["count"] == 2:
                auth.CALLBACK_CODE = "code"
                auth.CALLBACK_STATE = "expected"

    monkeypatch.setattr(socketserver, "TCPServer", FakeServer)
    monkeypatch.setattr(auth, "CALLBACK_TIMEOUT_SECONDS", 30)

    code = auth.capture_authorization_code("expected")

    assert code == "code"
    assert calls["count"] == 2


def test_generate_pkce_defaults_to_plain_for_myanimelist(monkeypatch):
    monkeypatch.setattr(auth, "PKCE_METHOD", "plain")

    verifier, challenge, method = auth._generate_pkce()

    assert verifier == challenge
    assert method == "plain"


def test_authorization_url_uses_configured_pkce_method(monkeypatch):
    monkeypatch.setenv("MAL_CLIENT_ID", "client-id")
    monkeypatch.setattr(auth, "REDIRECT_URI", "http://localhost:8080/callback")

    url = auth._authorization_url("state", "challenge", "plain")
    params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    assert params["client_id"] == ["client-id"]
    assert params["state"] == ["state"]
    assert params["code_challenge"] == ["challenge"]
    assert params["code_challenge_method"] == ["plain"]
    assert params["redirect_uri"] == ["http://localhost:8080/callback"]


# ---------------------------------------------------------------------------
# Silent refresh tests for get_auth_status
# ---------------------------------------------------------------------------


def test_get_auth_status_valid_token_returns_authenticated(
    monkeypatch, tmp_path
):
    now = time.time()
    tokens = StoredTokens(
        access_token="access", refresh_token="refresh", expires_at=now + 3600
    )
    token_path = tmp_path / "mal_tokens.json"

    store = auth.TokenStore(token_path)
    store.save(tokens)

    monkeypatch.setattr(auth, "_token_store", lambda: store)

    result = asyncio.run(auth.get_auth_status())

    assert result["authenticated"] is True
    assert result["provider"] == "myanimelist"
    assert result["expires_at"] == pytest.approx(now + 3600)
    assert "token_path" in result


def test_get_auth_status_expired_token_refreshes_and_returns_authenticated(
    monkeypatch, tmp_path
):
    now = time.time()
    expired_tokens = StoredTokens(
        access_token="old-access",
        refresh_token="old-refresh",
        expires_at=now - 10,
    )
    token_path = tmp_path / "mal_tokens.json"

    store = auth.TokenStore(token_path)
    store.save(expired_tokens)

    monkeypatch.setattr(auth, "_token_store", lambda: store)

    refreshed = StoredTokens(
        access_token="new-access",
        refresh_token="new-refresh",
        expires_at=now + 7200,
    )

    async def fake_refresh(_refresh_token):
        store.save(refreshed)
        return refreshed

    mock_refresh = AsyncMock(side_effect=fake_refresh)
    monkeypatch.setattr(auth, "_refresh_tokens", mock_refresh)

    result = asyncio.run(auth.get_auth_status())

    assert result["authenticated"] is True
    assert result["expires_at"] == pytest.approx(now + 7200)
    mock_refresh.assert_awaited_once_with("old-refresh")

    persisted = store.load()
    assert persisted is not None
    assert persisted.access_token == "new-access"
    assert persisted.refresh_token == "new-refresh"


def test_get_auth_status_near_expiry_token_refreshes(monkeypatch, tmp_path):
    now = time.time()
    near_expiry = StoredTokens(
        access_token="near-access",
        refresh_token="near-refresh",
        expires_at=now + 30,
    )
    token_path = tmp_path / "mal_tokens.json"

    store = auth.TokenStore(token_path)
    store.save(near_expiry)

    monkeypatch.setattr(auth, "_token_store", lambda: store)

    refreshed = StoredTokens(
        access_token="refreshed-access",
        refresh_token="refreshed-refresh",
        expires_at=now + 3600,
    )
    mock_refresh = AsyncMock(return_value=refreshed)
    monkeypatch.setattr(auth, "_refresh_tokens", mock_refresh)

    result = asyncio.run(auth.get_auth_status())

    assert result["authenticated"] is True
    mock_refresh.assert_awaited_once()


def test_get_auth_status_no_stored_tokens_returns_unauthenticated(
    monkeypatch, tmp_path
):
    store = auth.TokenStore(tmp_path / "nonexistent.json")
    monkeypatch.setattr(auth, "_token_store", lambda: store)

    result = asyncio.run(auth.get_auth_status())

    assert result["authenticated"] is False
    assert result["reason"] == "no_stored_tokens"


def test_get_auth_status_refresh_failure_returns_unauthenticated(
    monkeypatch, tmp_path
):
    now = time.time()
    expired = StoredTokens(
        access_token="old", refresh_token="bad-refresh", expires_at=now - 100
    )
    token_path = tmp_path / "mal_tokens.json"

    store = auth.TokenStore(token_path)
    store.save(expired)

    monkeypatch.setattr(auth, "_token_store", lambda: store)

    mock_refresh = AsyncMock(side_effect=Exception("refresh rejected"))
    monkeypatch.setattr(auth, "_refresh_tokens", mock_refresh)

    result = asyncio.run(auth.get_auth_status())

    assert result["authenticated"] is False
    assert "token_path" in result
    assert "access_token" not in result
    assert "refresh_token" not in result
    mock_refresh.assert_awaited_once()


def test_get_auth_status_expired_no_refresh_token_returns_unauthenticated(
    monkeypatch, tmp_path
):
    now = time.time()
    expired_no_refresh = StoredTokens(
        access_token="old", refresh_token=None, expires_at=now - 100
    )
    token_path = tmp_path / "mal_tokens.json"

    store = auth.TokenStore(token_path)
    store.save(expired_no_refresh)

    monkeypatch.setattr(auth, "_token_store", lambda: store)

    mock_refresh = AsyncMock()
    monkeypatch.setattr(auth, "_refresh_tokens", mock_refresh)

    result = asyncio.run(auth.get_auth_status())

    assert result["authenticated"] is False
    assert result["reason"] == "token_expired"
    mock_refresh.assert_not_awaited()


def test_get_auth_status_does_not_invoke_mal_auth_login(monkeypatch, tmp_path):
    now = time.time()
    expired = StoredTokens(
        access_token="old", refresh_token="old-refresh", expires_at=now - 10
    )
    token_path = tmp_path / "mal_tokens.json"

    store = auth.TokenStore(token_path)
    store.save(expired)

    monkeypatch.setattr(auth, "_token_store", lambda: store)

    refreshed = StoredTokens(
        access_token="new", refresh_token="new-refresh", expires_at=now + 3600
    )
    mock_refresh = AsyncMock(return_value=refreshed)
    monkeypatch.setattr(auth, "_refresh_tokens", mock_refresh)

    login_called = Mock()
    monkeypatch.setattr(auth, "login_initiate", login_called)

    result = asyncio.run(auth.get_auth_status())

    assert result["authenticated"] is True
    login_called.assert_not_called()


def test_get_auth_status_refresh_token_rotation_persisted(
    monkeypatch, tmp_path
):
    now = time.time()
    expired = StoredTokens(
        access_token="old",
        refresh_token="rotating-refresh",
        expires_at=now - 10,
    )
    token_path = tmp_path / "mal_tokens.json"

    store = auth.TokenStore(token_path)
    store.save(expired)

    monkeypatch.setattr(auth, "_token_store", lambda: store)

    rotated = StoredTokens(
        access_token="rotated-access",
        refresh_token="rotated-refresh",
        expires_at=now + 3600,
    )

    async def fake_refresh(_refresh_token):
        store.save(rotated)
        return rotated

    mock_refresh = AsyncMock(side_effect=fake_refresh)
    monkeypatch.setattr(auth, "_refresh_tokens", mock_refresh)

    result = asyncio.run(auth.get_auth_status())

    assert result["authenticated"] is True

    persisted = store.load()
    assert persisted is not None
    assert persisted.access_token == "rotated-access"
    assert persisted.refresh_token == "rotated-refresh"


def test_get_auth_status_does_not_expose_token_secrets_on_refresh_failure(
    monkeypatch, tmp_path
):
    now = time.time()
    expired = StoredTokens(
        access_token="secret-access",
        refresh_token="secret-refresh",
        expires_at=now - 10,
    )
    token_path = tmp_path / "mal_tokens.json"

    store = auth.TokenStore(token_path)
    store.save(expired)

    monkeypatch.setattr(auth, "_token_store", lambda: store)

    mock_refresh = AsyncMock(side_effect=Exception("invalid_grant"))
    monkeypatch.setattr(auth, "_refresh_tokens", mock_refresh)

    result = asyncio.run(auth.get_auth_status())

    assert result["authenticated"] is False
    assert "access_token" not in result
    assert "refresh_token" not in result
    assert "secret-access" not in str(result)
    assert "secret-refresh" not in str(result)
    assert "Bearer" not in str(result)


# ---------------------------------------------------------------------------
# get_mal_access_token tests
# ---------------------------------------------------------------------------


def test_get_mal_access_token_valid_returns_token(monkeypatch, tmp_path):
    now = time.time()
    tokens = StoredTokens(
        access_token="access", refresh_token="refresh", expires_at=now + 3600
    )
    token_path = tmp_path / "mal_tokens.json"

    store = auth.TokenStore(token_path)
    store.save(tokens)

    monkeypatch.setattr(auth, "_token_store", lambda: store)

    result = asyncio.run(auth.get_mal_access_token())

    assert result == "access"


def test_get_mal_access_token_expired_refreshes_and_returns_token(
    monkeypatch, tmp_path
):
    now = time.time()
    expired = StoredTokens(
        access_token="old-access",
        refresh_token="old-refresh",
        expires_at=now - 10,
    )
    token_path = tmp_path / "mal_tokens.json"

    store = auth.TokenStore(token_path)
    store.save(expired)

    monkeypatch.setattr(auth, "_token_store", lambda: store)

    refreshed = StoredTokens(
        access_token="new-access",
        refresh_token="new-refresh",
        expires_at=now + 7200,
    )

    async def fake_refresh(_refresh_token):
        store.save(refreshed)
        return refreshed

    mock_refresh = AsyncMock(side_effect=fake_refresh)
    monkeypatch.setattr(auth, "_refresh_tokens", mock_refresh)

    result = asyncio.run(auth.get_mal_access_token())

    assert result == "new-access"
    mock_refresh.assert_awaited_once_with("old-refresh")


def test_get_mal_access_token_near_expiry_refreshes(monkeypatch, tmp_path):
    now = time.time()
    near_expiry = StoredTokens(
        access_token="near-access",
        refresh_token="near-refresh",
        expires_at=now + 30,
    )
    token_path = tmp_path / "mal_tokens.json"

    store = auth.TokenStore(token_path)
    store.save(near_expiry)

    monkeypatch.setattr(auth, "_token_store", lambda: store)

    refreshed = StoredTokens(
        access_token="refreshed-access",
        refresh_token="refreshed-refresh",
        expires_at=now + 3600,
    )
    mock_refresh = AsyncMock(return_value=refreshed)
    monkeypatch.setattr(auth, "_refresh_tokens", mock_refresh)

    result = asyncio.run(auth.get_mal_access_token())

    assert result == "refreshed-access"
    mock_refresh.assert_awaited_once()


def test_get_mal_access_token_no_stored_tokens_raises(monkeypatch, tmp_path):
    store = auth.TokenStore(tmp_path / "nonexistent.json")
    monkeypatch.setattr(auth, "_token_store", lambda: store)

    with pytest.raises(RuntimeError, match="mal_auth_login"):
        asyncio.run(auth.get_mal_access_token())


def test_get_mal_access_token_expired_no_refresh_token_raises(
    monkeypatch, tmp_path
):
    now = time.time()
    expired_no_refresh = StoredTokens(
        access_token="old", refresh_token=None, expires_at=now - 100
    )
    token_path = tmp_path / "mal_tokens.json"

    store = auth.TokenStore(token_path)
    store.save(expired_no_refresh)

    monkeypatch.setattr(auth, "_token_store", lambda: store)

    with pytest.raises(RuntimeError, match="mal_auth_login"):
        asyncio.run(auth.get_mal_access_token())


def test_get_mal_access_token_refresh_failure_raises(monkeypatch, tmp_path):
    now = time.time()
    expired = StoredTokens(
        access_token="old", refresh_token="bad-refresh", expires_at=now - 100
    )
    token_path = tmp_path / "mal_tokens.json"

    store = auth.TokenStore(token_path)
    store.save(expired)

    monkeypatch.setattr(auth, "_token_store", lambda: store)

    mock_refresh = AsyncMock(side_effect=Exception("refresh rejected"))
    monkeypatch.setattr(auth, "_refresh_tokens", mock_refresh)

    with pytest.raises(RuntimeError, match="mal_auth_login"):
        asyncio.run(auth.get_mal_access_token())

    mock_refresh.assert_awaited_once()


def test_get_mal_access_token_uses_lock_and_double_check(
    monkeypatch, tmp_path
):
    now = time.time()
    expired = StoredTokens(
        access_token="old", refresh_token="old-refresh", expires_at=now - 10
    )
    token_path = tmp_path / "mal_tokens.json"

    store = auth.TokenStore(token_path)
    store.save(expired)

    monkeypatch.setattr(auth, "_token_store", lambda: store)

    refreshed = StoredTokens(
        access_token="refreshed",
        refresh_token="new-refresh",
        expires_at=now + 3600,
    )

    refresh_calls = 0

    async def fake_refresh(token):
        nonlocal refresh_calls
        refresh_calls += 1
        await asyncio.sleep(0.01)
        store.save(refreshed)
        return refreshed

    mock_refresh = AsyncMock(side_effect=fake_refresh)
    monkeypatch.setattr(auth, "_refresh_tokens", mock_refresh)

    async def concurrent_call():
        auth._REFRESH_LOCK = asyncio.Lock()
        return await asyncio.gather(
            auth.get_mal_access_token(),
            auth.get_mal_access_token(),
        )

    results = asyncio.run(concurrent_call())

    assert results == ["refreshed", "refreshed"]
    assert refresh_calls == 1


def test_get_mal_access_token_does_not_invoke_mal_auth_login(
    monkeypatch, tmp_path
):
    now = time.time()
    expired = StoredTokens(
        access_token="old", refresh_token="old-refresh", expires_at=now - 10
    )
    token_path = tmp_path / "mal_tokens.json"

    store = auth.TokenStore(token_path)
    store.save(expired)

    monkeypatch.setattr(auth, "_token_store", lambda: store)

    refreshed = StoredTokens(
        access_token="new", refresh_token="new-refresh", expires_at=now + 3600
    )
    mock_refresh = AsyncMock(return_value=refreshed)
    monkeypatch.setattr(auth, "_refresh_tokens", mock_refresh)

    login_called = Mock()
    monkeypatch.setattr(auth, "login_initiate", login_called)

    result = asyncio.run(auth.get_mal_access_token())

    assert result == "new"
    login_called.assert_not_called()


def test_get_mal_access_token_does_not_expose_secrets_on_failure(
    monkeypatch, tmp_path
):
    now = time.time()
    expired = StoredTokens(
        access_token="secret-access",
        refresh_token="secret-refresh",
        expires_at=now - 10,
    )
    token_path = tmp_path / "mal_tokens.json"

    store = auth.TokenStore(token_path)
    store.save(expired)

    monkeypatch.setattr(auth, "_token_store", lambda: store)

    mock_refresh = AsyncMock(side_effect=Exception("invalid_grant"))
    monkeypatch.setattr(auth, "_refresh_tokens", mock_refresh)

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(auth.get_mal_access_token())

    msg = str(excinfo.value)
    assert "secret-access" not in msg
    assert "secret-refresh" not in msg
    assert "Bearer" not in msg


# ---------------------------------------------------------------------------
# OAuth callback handler
# ---------------------------------------------------------------------------


def _handle_callback(monkeypatch, path):
    monkeypatch.setattr(auth, "CALLBACK_CODE", None)
    monkeypatch.setattr(auth, "CALLBACK_STATE", None)
    handler = object.__new__(auth._CallbackHandler)
    handler.path = path
    responses: list[tuple[int, str]] = []
    handler._send_html = lambda status, body: responses.append((status, body))
    handler.do_GET()
    return responses


def test_callback_handler_stores_code_and_state(monkeypatch):
    responses = _handle_callback(monkeypatch, "/callback?code=abc&state=xyz")

    assert responses[0][0] == 200
    assert auth.CALLBACK_CODE == "abc"
    assert auth.CALLBACK_STATE == "xyz"


def test_callback_handler_rejects_unknown_path(monkeypatch):
    responses = _handle_callback(monkeypatch, "/other?code=abc&state=xyz")

    assert responses[0][0] == 404
    assert auth.CALLBACK_CODE is None


def test_callback_handler_reports_escaped_error(monkeypatch):
    responses = _handle_callback(
        monkeypatch, "/callback?error=%3Cscript%3Ealert(1)%3C%2Fscript%3E"
    )

    status, body = responses[0]
    assert status == 400
    assert "<script>" not in body
    assert "&lt;script&gt;" in body
    assert auth.CALLBACK_CODE is None


def test_callback_handler_ignores_code_without_state(monkeypatch):
    responses = _handle_callback(monkeypatch, "/callback?code=abc")

    assert responses[0][0] == 200
    assert auth.CALLBACK_CODE is None


# ---------------------------------------------------------------------------
# capture_authorization_code state verification
# ---------------------------------------------------------------------------


def _fake_server(on_request):
    class FakeServer:
        allow_reuse_address = False

        def __init__(self, address, handler):
            self.timeout = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def handle_request(self):
            on_request()

    return FakeServer


def test_capture_authorization_code_rejects_mismatched_state(monkeypatch):
    callbacks = iter(
        [("forged-code", "attacker-state"), ("real-code", "expected")]
    )

    def on_request():
        auth.CALLBACK_CODE, auth.CALLBACK_STATE = next(callbacks)

    monkeypatch.setattr(socketserver, "TCPServer", _fake_server(on_request))
    monkeypatch.setattr(auth, "CALLBACK_TIMEOUT_SECONDS", 30)

    code = auth.capture_authorization_code("expected")

    assert code == "real-code"


def test_capture_authorization_code_returns_none_on_timeout(monkeypatch):
    monkeypatch.setattr(socketserver, "TCPServer", _fake_server(lambda: None))
    monkeypatch.setattr(auth, "CALLBACK_TIMEOUT_SECONDS", 0)

    assert auth.capture_authorization_code("expected") is None


# ---------------------------------------------------------------------------
# Token endpoint calls
# ---------------------------------------------------------------------------


def _mock_token_endpoint(payload=None, error=None):
    response = Mock()
    response.raise_for_status = Mock(side_effect=error)
    response.json = Mock(return_value=payload)

    http = AsyncMock()
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=False)
    http.post = AsyncMock(return_value=response)
    return http


@pytest.fixture
def oauth_client(monkeypatch):
    monkeypatch.setenv("MAL_CLIENT_ID", "client-id")
    monkeypatch.setenv("MAL_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(auth, "REDIRECT_URI", "http://localhost:8080/callback")


def test_exchange_code_for_token_posts_authorization_code_grant(oauth_client):
    http = _mock_token_endpoint(
        {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 3600,
        }
    )

    with patch("utils.auth.httpx.AsyncClient", return_value=http):
        tokens = asyncio.run(auth._exchange_code_for_token("code", "verifier"))

    http.post.assert_awaited_once_with(
        auth._MAL_OAUTH_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "code": "code",
            "code_verifier": "verifier",
            "redirect_uri": "http://localhost:8080/callback",
        },
    )
    assert tokens.access_token == "access"
    assert tokens.refresh_token == "refresh"
    assert tokens.is_valid


def test_refresh_tokens_saves_tokens_and_keeps_old_refresh_token(
    oauth_client, monkeypatch, tmp_path
):
    store = auth.TokenStore(tmp_path / "mal_tokens.json")
    monkeypatch.setattr(auth, "_token_store", lambda: store)
    http = _mock_token_endpoint(
        {"access_token": "new-access", "expires_in": 3600}
    )

    with patch("utils.auth.httpx.AsyncClient", return_value=http):
        tokens = asyncio.run(auth._refresh_tokens("old-refresh"))

    assert http.post.call_args.kwargs["data"]["grant_type"] == "refresh_token"
    assert http.post.call_args.kwargs["data"]["refresh_token"] == "old-refresh"
    assert tokens.refresh_token == "old-refresh"
    assert store.load() == tokens


def test_refresh_tokens_http_error_keeps_stored_tokens(
    oauth_client, monkeypatch, tmp_path
):
    store = auth.TokenStore(tmp_path / "mal_tokens.json")
    old = StoredTokens(
        access_token="old", refresh_token="old-refresh", expires_at=0
    )
    store.save(old)
    monkeypatch.setattr(auth, "_token_store", lambda: store)
    http = _mock_token_endpoint(error=RuntimeError("401 invalid_grant"))

    with (
        patch("utils.auth.httpx.AsyncClient", return_value=http),
        pytest.raises(RuntimeError, match="invalid_grant"),
    ):
        asyncio.run(auth._refresh_tokens("old-refresh"))

    assert store.load() == old


def test_exchange_code_requires_client_secret(monkeypatch):
    monkeypatch.setenv("MAL_CLIENT_ID", "client-id")

    with pytest.raises(ValueError, match="MAL_CLIENT_SECRET"):
        asyncio.run(auth._exchange_code_for_token("code", "verifier"))


# ---------------------------------------------------------------------------
# Background login watcher
# ---------------------------------------------------------------------------


@pytest.fixture
def login_env(monkeypatch, tmp_path):
    store = auth.TokenStore(tmp_path / "mal_tokens.json")
    state: dict = {}
    monkeypatch.setattr(auth, "_token_store", lambda: store)
    monkeypatch.setattr(auth, "_login_state", state)
    return store, state


def test_watch_login_saves_tokens_on_success(login_env, monkeypatch):
    store, state = login_env
    tokens = StoredTokens(
        access_token="access",
        refresh_token="refresh",
        expires_at=time.time() + 3600,
    )
    captured = Mock(return_value="code")
    exchange = AsyncMock(return_value=tokens)
    monkeypatch.setattr(auth, "capture_authorization_code", captured)
    monkeypatch.setattr(auth, "_exchange_code_for_token", exchange)

    asyncio.run(auth._watch_login("state", "verifier"))

    captured.assert_called_once_with("state")
    exchange.assert_awaited_once_with("code", "verifier")
    assert store.load() == tokens
    assert "completed_at" in state
    assert "error" not in state


def test_watch_login_records_timeout(login_env, monkeypatch):
    store, state = login_env
    monkeypatch.setattr(
        auth, "capture_authorization_code", Mock(return_value=None)
    )
    exchange = AsyncMock()
    monkeypatch.setattr(auth, "_exchange_code_for_token", exchange)

    asyncio.run(auth._watch_login("state", "verifier"))

    assert "timed out" in state["error"]
    exchange.assert_not_awaited()
    assert store.load() is None


def test_watch_login_records_exchange_failure(login_env, monkeypatch):
    store, state = login_env
    monkeypatch.setattr(
        auth, "capture_authorization_code", Mock(return_value="code")
    )
    monkeypatch.setattr(
        auth,
        "_exchange_code_for_token",
        AsyncMock(side_effect=RuntimeError("invalid_grant")),
    )

    asyncio.run(auth._watch_login("state", "verifier"))

    assert state["error"] == "Login failed: invalid_grant"
    assert store.load() is None


# ---------------------------------------------------------------------------
# login_initiate / login_status / revoke_auth
# ---------------------------------------------------------------------------


def test_login_initiate_skips_login_when_already_authenticated(login_env):
    store, state = login_env
    store.save(
        StoredTokens(
            access_token="access",
            refresh_token=None,
            expires_at=time.time() + 3600,
        )
    )

    result = auth.login_initiate()

    assert result["status"] == "already_authenticated"
    assert result["authorization_url"] is None
    assert state == {}


def test_login_initiate_requires_client_secret(login_env, monkeypatch):
    _, state = login_env
    monkeypatch.setenv("MAL_CLIENT_ID", "client-id")

    with pytest.raises(ValueError, match="MAL_CLIENT_SECRET"):
        auth.login_initiate()

    assert "task" not in state


def test_login_initiate_starts_one_watcher_and_reuses_pending_login(
    login_env, oauth_client, monkeypatch
):
    _, state = login_env
    monkeypatch.setattr(auth, "PKCE_METHOD", "plain")
    started: list[tuple[str, str]] = []

    async def run():
        release = asyncio.Event()

        async def fake_watch(login_state, verifier):
            started.append((login_state, verifier))
            await release.wait()

        monkeypatch.setattr(auth, "_watch_login", fake_watch)
        first = auth.login_initiate()
        await asyncio.sleep(0)
        second = auth.login_initiate()
        release.set()
        await state["task"]
        return first, second

    first, second = asyncio.run(run())

    params = urllib.parse.parse_qs(
        urllib.parse.urlparse(first["authorization_url"]).query
    )
    assert first["status"] == "pending"
    assert second["status"] == "pending"
    assert second["authorization_url"] == first["authorization_url"]
    assert started == [(state["state"], state["verifier"])]
    assert params["state"] == [state["state"]]
    assert params["code_challenge"] == [state["verifier"]]


def test_login_status_reports_authenticated(login_env):
    store, _ = login_env
    store.save(
        StoredTokens(
            access_token="access",
            refresh_token=None,
            expires_at=time.time() + 3600,
        )
    )

    result = asyncio.run(auth.login_status())

    assert result["status"] == "authenticated"
    assert result["authenticated"] is True


def test_login_status_reports_pending(login_env):
    _, state = login_env
    state["task"] = Mock(done=Mock(return_value=False))
    state["authorization_url"] = "https://example.test/authorize"

    result = asyncio.run(auth.login_status())

    assert result["status"] == "pending"
    assert result["authorization_url"] == "https://example.test/authorize"


def test_login_status_reports_failure(login_env):
    _, state = login_env
    state["task"] = Mock(done=Mock(return_value=True))
    state["error"] = "Login failed: invalid_grant"

    result = asyncio.run(auth.login_status())

    assert result["status"] == "failed"
    assert result["error"] == "Login failed: invalid_grant"


def test_login_status_reports_not_authenticated(login_env):
    result = asyncio.run(auth.login_status())

    assert result["status"] == "not_authenticated"
    assert result["authenticated"] is False


def test_revoke_auth_clears_tokens_and_login_state(login_env):
    store, state = login_env
    store.save(
        StoredTokens(
            access_token="access",
            refresh_token="refresh",
            expires_at=time.time(),
        )
    )
    state["error"] = "Login failed"

    result = auth.revoke_auth()

    assert result["authenticated"] is False
    assert store.load() is None
    assert state == {}
