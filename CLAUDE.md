# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

MCP server (FastMCP 4, stdio) exposing the MyAnimeList API v2 as tools, plus helpers for OAuth, an upcoming episode digest and a watch queue (air times from AniList) and MAL news. See README.md for configuration (`.env`, `MAL_*` variables) and the tool list.

## Commands

```bash
uv sync                                    # install, including dev group (pytest, pytest-cov)
uv run main.py                             # run the server on stdio
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

## Architecture

- `main.py`: `create_server()` builds `FastMCP("myanimelist", mask_error_details=True)` and calls `register_tools`; `main()` installs a SIGINT handler that logs to stderr (stdout is the protocol) and runs the server on stdio. Importing `main` has no side effects.
- `tools/tools.py` defines every tool as a closure inside `register_tools`, sharing one `MALClient`.
  - Every tool carries `annotations=READ_ONLY` or `MUTATING` (`ToolAnnotations(read_only_hint=...)`); a test fails if one is unmarked.
  - Error boundary: tools catch only `_TOOL_ERRORS` (`MALAPIError`, `MALAuthError`, `httpx.HTTPError`, `ValueError`) and return `api_error_payload(e)`, which redacts secrets via `safe_error`. Any other exception is a bug and propagates to FastMCP, which logs it and masks the message.
  - Argument limits live in the signatures (`Annotated[..., Field(ge=..., le=...)]`) and are enforced by FastMCP before the body runs; there is no validation inside tool bodies.
- Field selection (`utils/schemas.py`, `utils/mal_client.py`):
  - `AnimeFields` and `MangaFields` are separate `StrEnum`s (all API enums are `StrEnum`, so members are used directly in paths and params; avoid `.value`). Manga tools must use `MangaFields`.
  - `build_fields(requested, defaults)` puts requested fields first, then the defaults, without duplicates, and expands `authors` to `authors{first_name,last_name}`.
  - Every tool that returns anime or manga always sends its defaults (`_DEFAULT_ANIME_FIELDS` / `_DEFAULT_MANGA_FIELDS`: id, title, alternative_titles, main_picture, nsfw, mean), even without a `fields` argument, so responses have a stable shape. User-list tools append `list_status` via `_with_list_status`. Details tools request every enum field.
- `utils/mal_client.py` `MALClient.request`: sends `X-MAL-CLIENT-ID` for public calls and `Authorization: Bearer` for authed ones, paces requests (`MAL_RATE_LIMIT_DELAY`), turns 204/empty/`null`/`[]` bodies into `{"ok": True}`, and raises `MALAPIError` for 200 bodies that contain `error`.
- `utils/auth.py`: OAuth with PKCE. `login_initiate` starts a one-shot local callback server as a task on the running event loop (it raises without one) and verifies `state`. Tokens refresh silently under a lock; `get_mal_access_token` raises `MALAuthError` when no token is usable. `load_dotenv()` runs at import and module-level settings (`CLIENT_ID`, `REDIRECT_URI`, ...) are copied from the environment at import time.
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

- `tests/conftest.py` has an autouse fixture that removes all `MAL_*` variables, resets the import-time settings in `utils.auth`, points `HERMES_HOME` at a temp dir and blocks real HTTP through `httpx.AsyncClient`, so `.env` and the real token file never leak into tests (`tests/test_isolation.py` guards this).
- The `mal` fixture registers the tools against a mock client: `mal.client` (the `AsyncMock` with `get_public`, `get_authed`, `patch_authed`, `delete_authed`), `mal.call(name, **kwargs)` (calls the function directly and skips argument validation), and `mal.call_over_mcp(name, arguments)` (through an in-memory FastMCP client; use it to test validation and serialisation).
- `tests/fixtures/mal/` holds anonymised recordings of real responses. They are generated, never hand-edited: real captures go into the git-ignored `tests/tool_responses/` (`NN_<tool>[_<suffix>].json`, plus raw list, AniList and `.xml` inputs of derived tools) and `scripts/anonymize_fixtures.py` writes the fixtures, failing if any real value survives. New tools need an anonymisation rule (`HANDLERS`, `PASSTHROUGH` or `RAW_INPUTS`), or the script refuses to run. Subdirectories of `tests/tool_responses/` are ignored by the script.
- `tests/fixtures/mal_docs/` holds the update samples from MAL's documentation and the recorded delete and 404 bodies; list-changing tools are not recorded routinely because that changes the user's list.
- `tests/test_recorded_responses.py` rebuilds the watch queue and episode digest from recorded inputs, freezing time at the digest's recorded `from`.
