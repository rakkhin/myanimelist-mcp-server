# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

MCP server (FastMCP 4, stdio or streamable HTTP) exposing the MyAnimeList API v2 as tools, plus helpers for OAuth, an upcoming episode digest and a watch queue (air times from AniList) and MAL news. See README.md for configuration (`.env`, `MAL_*` variables) and the tool list.

## Commands

```bash
uv sync                                    # install, including dev group (pytest, pytest-cov)
uv run main.py                             # run the server on stdio
MAL_TRANSPORT=http uv run main.py          # run it on streamable HTTP (127.0.0.1:8000/mcp)
uv run pytest -q                           # all tests
uv run pytest -q tests/test_read_tools.py::TestPublicTools::test_forum_boards
uv run pytest -q --cov=. --cov-report=term-missing   # fails if total < 95%
uv run python scripts/check_coverage.py              # after a --cov run: fails if any file < 95%
uv run ruff format . && uv run ruff check .
uv run python scripts/anonymize_fixtures.py  # regenerate tests/fixtures/mal/
```

- Ruff line length is 79 (`.ruff.toml`). Coverage must be at least 95% in total (`fail_under` in `pyproject.toml`) and per file (`scripts/check_coverage.py`); CI enforces both.
- Dependencies are managed with uv; the Dockerfile installs with `uv sync --frozen`, so update `uv.lock` (`uv lock`) whenever `pyproject.toml` changes. `fastmcp` is pinned to `~=4.0` on purpose.

## Git workflow

- The repo uses the feature branch workflow: work on a feature branch, never commit directly to the default branch (`master`), and merge through a pull request.
- Prefer squash merges when completing a pull request into the default branch, so each feature branch lands as one commit.
- Commit messages follow [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/): `<type>[optional scope][!]: <description>` (e.g. `feat:`, `fix:`, `build:`, `refactor:`, `docs:`, `test:`, `ci:`, `chore:`), with breaking changes marked by `!` or a `BREAKING CHANGE:` footer. Pull request titles follow the same format, since the squash commit takes its message from the title.
- Versions come from GitVersion (`GitVersion.yaml`): the highest bump among the commit messages since the latest tag (`!` or `BREAKING CHANGE:` major, `feat` minor, anything else patch), so pick each commit's type with that in mind. The Master workflow builds, pushes and tags a release only for pushes that change the server's code or dependencies, `GitVersion.yaml`, or the Master, Version or Container workflows (`paths` in `.github/workflows/master.yml`), so Dependabot's action updates are releases too; other commits are not tagged and count towards the next release.

## Architecture

- `main.py`: `create_server()` builds `FastMCP("myanimelist", mask_error_details=True)` and calls `register_tools`. `main()` picks the transport from `MAL_TRANSPORT` (`stdio` default, or `http`) and validates the settings before anything starts. HTTP listens on `MAL_HTTP_HOST:MAL_HTTP_PORT` at `MAL_HTTP_PATH` (default `127.0.0.1:8000`, `/mcp`); a reverse proxy or tunnel forwards the public path unchanged, so `MAL_HTTP_PATH` must match it. The Cloudflare Access check is opt-in: it is on when `MAL_CF_ACCESS_TEAM_DOMAIN` and `MAL_CF_ACCESS_AUD` are set, off (with a warning on stderr) when neither is, for servers behind another authentication layer; one without the other fails at startup. uvicorn handles signals for HTTP: it shuts down, then re-raises the signal. For stdio, `main()` installs a SIGINT/SIGTERM handler that logs to stderr (stdout is the protocol) and exits with `os._exit(128 + signum)` (shell convention), then runs the server on stdio. The stdio transport reads stdin in an uncancellable non-daemon thread, so unwinding on a signal hangs until the next stdin line; clients normally stop the server by closing stdin. Importing `main` has no side effects.
- `tools/tools.py` defines every tool as a closure inside `register_tools`, sharing one `MALClient`.
  - Every tool carries `annotations=READ_ONLY` or `MUTATING` (`ToolAnnotations(read_only_hint=...)`); a test fails if one is unmarked.
  - Every tool returns `ToolResult` through `_tool_result(data)`, whose only content is the text block `format_tool_payload(data)` (`utils/serialization.py`: TOON when strictly shorter than minified JSON, else that JSON). There is deliberately no `structured_content` and no output schema: Claude Code shows the model `structuredContent` instead of the text when both are present, and the spec expects JSON text next to structured content. A test checks that no tool declares an output schema.
  - Error boundary: tools catch only `_TOOL_ERRORS` (`MALAPIError`, `MALAuthError`, `httpx.HTTPError`, `ValueError`) and return `api_error_payload(e)`, which redacts secrets via `safe_error`. Any other exception is a bug and propagates to FastMCP, which logs it and masks the message.
  - Argument limits live in the signatures (`Annotated[..., Field(ge=..., le=...)]`) and are enforced by FastMCP before the body runs; there is no validation inside tool bodies.
- Field selection (`utils/schemas.py`, `utils/mal_client.py`):
  - `AnimeFields` and `MangaFields` are separate `StrEnum`s (all API enums are `StrEnum`, so members are used directly in paths and params; avoid `.value`). Manga tools must use `MangaFields`.
  - `build_fields(requested, defaults)` puts requested fields first, then the defaults, without duplicates, and expands `authors` to `authors{first_name,last_name}`.
  - Every tool that returns anime or manga always sends its defaults (`_DEFAULT_ANIME_FIELDS` / `_DEFAULT_MANGA_FIELDS`: id, title, alternative_titles, main_picture, nsfw, mean), even without a `fields` argument, so responses have a stable shape. User-list tools append `list_status` via `_with_list_status`. Details tools request every enum field.
- `utils/mal_client.py` `MALClient.request`: sends `X-MAL-CLIENT-ID` for public calls and `Authorization: Bearer` for authed ones, paces requests (`MAL_RATE_LIMIT_DELAY`), turns 204/empty/`null`/`[]` bodies into `{"ok": True}`, and raises `MALAPIError` for 200 bodies that contain `error`.
- `utils/auth.py`: OAuth with PKCE. `login_initiate` starts a one-shot local callback server as a task on the running event loop (it raises without one) and verifies `state`. Tokens refresh silently under a lock; `get_mal_access_token` raises `MALAuthError` when no token is usable. MAL can revoke a token before its stored expiry: tools send authenticated requests through `authed` (in `register_tools`), which on a 401 calls `expire_access_token` (keeps the refresh token, so the next call refreshes and `login_initiate` no longer reports `already_authenticated`) and retries once. `load_dotenv()` runs at import and module-level settings (`CLIENT_ID`, `REDIRECT_URI`, ...) are copied from the environment at import time.
- `utils/cloudflare_access.py`: `AccessJWTMiddleware` answers 403 to every HTTP request (not lifespan events) without a valid `Cf-Access-Jwt-Assertion` JWT, verified by FastMCP's `JWTVerifier` against the team's certs endpoint, issuer and the Access application's AUD tag. Cloudflare Access does the real gatekeeping in front of the tunnel; this check also refuses requests that reach the port another way (local processes, DNS rebinding).
- `utils/token_store.py`: JSON token file with `0600` permissions at `MAL_TOKEN_STORAGE_PATH`, else `$HERMES_HOME/secrets/mal_tokens.json` (`~/.hermes` by default).
- `utils/anilist.py`: `fetch_upcoming_episodes` asks AniList's public GraphQL API for the not yet aired episodes of MAL ids (`idMal_in`, `type: ANIME`, pages of 50) and raises `AniListError` (returned by the tools via `payload()`) for HTTP or GraphQL errors.
- `utils/episodes.py`: `airing_ids` (titles worth asking AniList about), `upcoming_episodes` (digest for the `watching` and `plan_to_watch` lists) and `build_watch_queue` (the `watching` list against the next scheduled episode).

## MAL API behaviour (verified against the live API)

The official docs are wrong or silent on several of these:

- User lists only include `list_status` (status, score, progress) when it is in `fields`; node fields are listed at the top level of `fields` (no `anime{...}` wrapper).
- Manga `authors` only carry names when requested as `authors{first_name,last_name}`.
- `my_list_status` is only returned for requests made with a user token.
- Deletes return 200 with `[]` (the docs show `null`), even for a title that is not on the list; 404 with `{"message":"","error":"not_found"}` only for an id that does not exist.
- The news RSS puts the thumbnail URL in the `media:thumbnail` element text, not a `url` attribute.
- `GET /forum/topics` without a filter such as `q` times out on MAL's side.

## Tests

- `tests/conftest.py` has an autouse fixture that removes all `MAL_*` variables, resets the import-time settings in `utils.auth`, points `HERMES_HOME` at a temp dir and blocks real HTTP through `httpx.AsyncClient` and `httpx2.AsyncClient` (used by FastMCP's `JWTVerifier` for signing keys), so `.env` and the real token file never leak into tests (`tests/test_isolation.py` guards this).
- The `mal` fixture registers the tools against a mock client: `mal.client` (the `AsyncMock` with `get_public`, `get_authed`, `patch_authed`, `delete_authed`), `mal.call(name, **kwargs)` (calls the function directly, skips argument validation and returns the decoded payload), and `mal.call_over_mcp(name, arguments)` (through an in-memory FastMCP client; use it to test validation and serialisation). `tests/payloads.py` `tool_payload(result)` decodes a result's text block and checks that it is the only content and exactly what `format_tool_payload` writes; use it instead of reading `structured_content`.
- `tests/fixtures/mal/` holds anonymised recordings of real responses. They are generated, never hand-edited: real captures go into the git-ignored `tests/tool_responses/` (`NN_<tool>[_<suffix>].json`, plus raw list, AniList and `.xml` inputs of derived tools) and `scripts/anonymize_fixtures.py` writes the fixtures, failing if any real value survives. New tools need an anonymisation rule (`HANDLERS`, `PASSTHROUGH` or `RAW_INPUTS`), or the script refuses to run. Subdirectories of `tests/tool_responses/` are ignored by the script.
- `tests/fixtures/mal_docs/` holds the update samples from MAL's documentation and the recorded delete and 404 bodies; list-changing tools are not recorded routinely because that changes the user's list.
- `tests/test_recorded_responses.py` rebuilds the watch queue and episode digest from recorded inputs, freezing time at the digest's recorded `from`.
