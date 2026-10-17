import asyncio
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from utils.mal_client import (
    MALAPIError,
    MALClient,
    MALClientConfig,
    api_error_payload,
    build_fields,
    clamp_limit,
    safe_error,
)
from utils.schemas import AnimeFields


def test_build_fields_uses_default_when_none_or_empty():
    default = [AnimeFields.ID, AnimeFields.TITLE]
    assert build_fields(None, default) == "id,title"
    assert build_fields([], default) == "id,title"


def test_build_fields_deduplicates_and_appends_default():
    fields = [AnimeFields.ID, AnimeFields.TITLE, AnimeFields.ID]
    default = [AnimeFields.MAIN_PICTURE, AnimeFields.ID]
    assert build_fields(fields, default) == "id,title,main_picture"


def test_clamp_limit_respects_minimum_and_maximum():
    assert clamp_limit(0, maximum=500) == 1
    assert clamp_limit(999, maximum=500) == 500
    assert clamp_limit(42, maximum=500) == 42


def test_safe_error_does_not_include_response_body_or_secrets():
    err = safe_error(
        "failure with token=abc123 and Authorization: Bearer secret-token"
    )

    assert "abc123" not in err
    assert "secret-token" not in err
    assert "token=<redacted>" in err


def test_mal_client_config_requires_client_id_for_public_calls():
    with pytest.raises(ValueError, match="MAL_CLIENT_ID"):
        MALClientConfig.from_env(require_client_id=True)


class TestApiErrorPayload:
    def test_mal_api_error_with_empty_payload(self):
        err = MALAPIError(
            "MAL API returned an empty error", payload={"error": ""}
        )
        result = api_error_payload(err)
        assert result["error"] == "MAL API returned an empty error"
        assert result["status_code"] == 200

    def test_mal_api_error_with_message(self):
        err = MALAPIError(
            "MAL API returned an error: invalid season",
            payload={"error": "invalid season"},
        )
        result = api_error_payload(err)
        assert result["error"] == "MAL API returned an error: invalid season"
        assert result["status_code"] == 200

    def test_mal_api_error_fallback_for_empty_str(self):
        err = MALAPIError("", payload={"error": ""})
        result = api_error_payload(err)
        assert result["error"] == "MALAPIError"
        assert result["status_code"] == 200

    def test_http_status_error_with_valid_reason(self):
        req = Mock()
        resp = Mock()
        resp.status_code = 404
        resp.reason_phrase = "Not Found"
        err = httpx.HTTPStatusError("msg", request=req, response=resp)
        result = api_error_payload(err)
        assert "MAL API error: 404 Not Found" in result["error"]
        assert result["status_code"] == 404

    def test_http_status_error_with_empty_reason(self):
        req = Mock()
        resp = Mock()
        resp.status_code = 400
        resp.reason_phrase = ""
        err = httpx.HTTPStatusError("msg", request=req, response=resp)
        result = api_error_payload(err)
        assert "MAL API error: 400" in result["error"]
        assert result["status_code"] == 400

    def test_generic_exception_fallback_for_empty_str(self):
        err = Exception("")
        result = api_error_payload(err)
        assert result["error"] == "Exception"

    def test_generic_exception_with_no_message(self):
        err = Exception()
        result = api_error_payload(err)
        assert result["error"] == "Exception"

    def test_value_error_with_message(self):
        err = ValueError("something went wrong")
        result = api_error_payload(err)
        assert result["error"] == "something went wrong"


class TestMALClientEmptyErrorDetection:
    def test_request_raises_on_empty_error_response(self, monkeypatch):
        monkeypatch.setenv("MAL_CLIENT_ID", "test-id")
        config = MALClientConfig(client_id="test-id")
        client = MALClient(config=config)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'{"error":""}'
        mock_response.raise_for_status = Mock()
        mock_response.json = Mock(return_value={"error": ""})

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.request = AsyncMock(return_value=mock_response)

        with (
            patch(
                "utils.mal_client.httpx.AsyncClient", return_value=mock_http
            ),
            pytest.raises(
                MALAPIError, match="MAL API returned an empty error"
            ),
        ):
            asyncio.run(
                client.request(
                    "GET",
                    "/anime/season/2026/summer",
                    params={"limit": 10},
                )
            )

    def test_request_raises_on_error_with_message(self, monkeypatch):
        monkeypatch.setenv("MAL_CLIENT_ID", "test-id")
        config = MALClientConfig(client_id="test-id")
        client = MALClient(config=config)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = (
            b'{"error":"invalid season","message":"details"}'
        )
        mock_response.raise_for_status = Mock()
        mock_response.json = Mock(
            return_value={"error": "invalid season", "message": "details"}
        )

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.request = AsyncMock(return_value=mock_response)

        with (
            patch(
                "utils.mal_client.httpx.AsyncClient", return_value=mock_http
            ),
            pytest.raises(
                MALAPIError, match="MAL API returned an error: invalid season"
            ),
        ):
            asyncio.run(
                client.request(
                    "GET",
                    "/anime/season/2026/summer",
                    params={"limit": 10},
                )
            )

    def test_request_passes_through_valid_response(self, monkeypatch):
        monkeypatch.setenv("MAL_CLIENT_ID", "test-id")
        config = MALClientConfig(client_id="test-id")
        client = MALClient(config=config)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'{"data":[{"node":{"id":1,"title":"Test"}}]}'
        mock_response.raise_for_status = Mock()
        mock_response.json = Mock(
            return_value={"data": [{"node": {"id": 1, "title": "Test"}}]}
        )

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.request = AsyncMock(return_value=mock_response)

        with patch(
            "utils.mal_client.httpx.AsyncClient", return_value=mock_http
        ):
            result = asyncio.run(
                client.request(
                    "GET", "/anime/season/2024/summer", params={"limit": 10}
                )
            )
            assert result["data"][0]["node"]["title"] == "Test"


def _mock_http(status_code=200, content=b'{"data": []}', body=None):
    response = Mock()
    response.status_code = status_code
    response.content = content
    response.raise_for_status = Mock()
    response.json = Mock(
        return_value=body if body is not None else {"data": []}
    )

    http = AsyncMock()
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=False)
    http.request = AsyncMock(return_value=response)
    return http


@pytest.fixture
def mal_http(monkeypatch):
    http = _mock_http()
    monkeypatch.setattr(
        "utils.mal_client.httpx.AsyncClient", Mock(return_value=http)
    )
    return http


def _client(client_id="test-id"):
    return MALClient(
        config=MALClientConfig(
            client_id=client_id, api_url="https://api.test", rate_limit_delay=0
        )
    )


class TestRequestHeadersAndMethods:
    def test_public_get_sends_client_id(self, mal_http):
        asyncio.run(_client().get_public("/anime", params={"q": "x"}))

        mal_http.request.assert_awaited_once_with(
            "GET",
            "https://api.test/anime",
            headers={"X-MAL-CLIENT-ID": "test-id"},
            params={"q": "x"},
            data=None,
        )

    def test_authed_get_sends_bearer_token_instead_of_client_id(
        self, mal_http
    ):
        asyncio.run(_client().get_authed("/users/@me", "tok"))

        mal_http.request.assert_awaited_once_with(
            "GET",
            "https://api.test/users/@me",
            headers={"Authorization": "Bearer tok"},
            params=None,
            data=None,
        )

    def test_put_sends_form_encoded_data(self, mal_http):
        asyncio.run(
            _client().put_authed(
                "/anime/1/my_list_status", "tok", data={"score": 9}
            )
        )

        mal_http.request.assert_awaited_once_with(
            "PUT",
            "https://api.test/anime/1/my_list_status",
            headers={
                "Authorization": "Bearer tok",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            params=None,
            data={"score": 9},
        )

    def test_delete_uses_delete_method(self, mal_http):
        asyncio.run(_client().delete_authed("/anime/1/my_list_status", "tok"))

        assert mal_http.request.call_args.args == (
            "DELETE",
            "https://api.test/anime/1/my_list_status",
        )

    def test_public_request_without_client_id_fails_before_sending(
        self, mal_http
    ):
        with pytest.raises(ValueError, match="MAL_CLIENT_ID is required"):
            asyncio.run(_client(client_id=None).get_public("/anime"))

        mal_http.request.assert_not_awaited()


@pytest.mark.parametrize(("status_code", "content"), [(204, b""), (200, b"")])
def test_request_returns_ok_for_empty_response(
    monkeypatch, status_code, content
):
    http = _mock_http(status_code=status_code, content=content)
    monkeypatch.setattr(
        "utils.mal_client.httpx.AsyncClient", Mock(return_value=http)
    )

    result = asyncio.run(
        _client().delete_authed("/anime/1/my_list_status", "t")
    )

    assert result == {"ok": True}
    http.request.return_value.json.assert_not_called()


def test_requests_are_spaced_by_rate_limit_delay(mal_http, monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("utils.mal_client.asyncio.sleep", sleep)
    client = MALClient(
        config=MALClientConfig(client_id="test-id", rate_limit_delay=10)
    )

    async def two_requests():
        await client.get_public("/anime")
        await client.get_public("/anime")

    asyncio.run(two_requests())

    sleep.assert_awaited_once()
    assert 9 < sleep.call_args.args[0] <= 10
