import json
import os
import stat
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from utils.token_store import StoredTokens, TokenStore


def test_token_store_round_trip_and_uses_private_file_mode(tmp_path: Path):
    path = tmp_path / "mal_tokens.json"
    store = TokenStore(path)

    tokens = StoredTokens(
        access_token="access-123",
        refresh_token="refresh-456",
        expires_at=1234567890.0,
        token_type="Bearer",
    )

    store.save(tokens)

    assert store.load() == tokens
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_token_store_returns_none_for_missing_or_invalid_file(tmp_path: Path):
    store = TokenStore(tmp_path / "missing.json")
    assert store.load() is None

    invalid = tmp_path / "invalid.json"
    invalid.write_text("not json")
    assert TokenStore(invalid).load() is None


def test_token_store_normalizes_non_string_refresh_token(tmp_path: Path):
    path = tmp_path / "mal_tokens.json"
    path.write_text(
        json.dumps(
            {
                "access_token": "access",
                "refresh_token": 123,
                "expires_at": time.time() + 3600,
            }
        ),
        encoding="utf-8",
    )

    tokens = TokenStore(path).load()

    assert tokens is not None
    assert tokens.refresh_token == "123"


def test_token_store_can_clear_tokens(tmp_path: Path):
    path = tmp_path / "mal_tokens.json"
    store = TokenStore(path)
    store.save(StoredTokens("access", "refresh", time.time() + 3600))

    store.clear()

    assert not path.exists()
    assert store.load() is None


def test_token_store_temp_file_is_created_private(monkeypatch, tmp_path: Path):
    observed_modes: list[int] = []
    real_open = os.open

    def recording_open(path, flags, mode=0o777, *args, **kwargs):
        observed_modes.append(mode)
        return real_open(path, flags, mode, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording_open)

    TokenStore(tmp_path / "mal_tokens.json").save(
        StoredTokens("access", "refresh", time.time() + 3600)
    )

    assert 0o600 in observed_modes


def test_token_store_default_path_uses_hermes_home(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))

    path = TokenStore.default_path()

    assert path == tmp_path / "hermes" / "secrets" / "mal_tokens.json"


def test_token_store_env_path_overrides_default(monkeypatch, tmp_path: Path):
    explicit = tmp_path / "custom" / "tokens.json"
    monkeypatch.setenv("MAL_TOKEN_STORAGE_PATH", str(explicit))

    assert TokenStore.default_path() == explicit


def _tokens() -> StoredTokens:
    return StoredTokens(access_token="a", refresh_token="r", expires_at=1.0)


def test_save_failure_raises_original_error(monkeypatch, tmp_path: Path):
    store = TokenStore(tmp_path / "tokens.json")
    monkeypatch.setattr(
        "utils.token_store.json.dumps",
        Mock(side_effect=TypeError("not serializable")),
    )

    with pytest.raises(TypeError, match="not serializable"):
        store.save(_tokens())

    assert store.load() is None


def test_save_succeeds_when_permissions_cannot_be_changed(
    monkeypatch, tmp_path: Path
):
    store = TokenStore(tmp_path / "tokens.json")
    monkeypatch.setattr(
        "utils.token_store.os.chmod", Mock(side_effect=PermissionError)
    )

    store.save(_tokens())

    assert store.load() == _tokens()


def test_clear_without_stored_tokens_does_nothing(tmp_path: Path):
    store = TokenStore(tmp_path / "tokens.json")

    store.clear()

    assert store.load() is None


def test_save_closes_descriptor_when_file_cannot_be_opened(
    monkeypatch, tmp_path: Path
):
    store = TokenStore(tmp_path / "tokens.json")
    close = Mock(wraps=os.close)
    monkeypatch.setattr(
        "utils.token_store.os.fdopen", Mock(side_effect=OSError("no fdopen"))
    )
    monkeypatch.setattr("utils.token_store.os.close", close)

    with pytest.raises(OSError, match="no fdopen"):
        store.save(_tokens())

    close.assert_called_once()
