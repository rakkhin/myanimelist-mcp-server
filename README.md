# MyAnimeList MCP Server

MCP server for interacting with the MyAnimeList API from Hermes, Claude Desktop, and other MCP clients. This fork focuses on safer unattended usage for anime-list maintenance and episode-release digests.

## What this fork adds

- Persistent OAuth token storage with private file permissions (`0600`).
- Refresh-token persistence so unattended jobs do not need to re-auth every run.
- Safer error messages that avoid returning raw API response bodies or bearer tokens.
- Small MyAnimeList API client wrapper with request timeout and basic request pacing.
- Auth helper tools: `mal_auth_login`, `mal_auth_login_status`, `mal_auth_status`, `mal_auth_revoke`.
- Episode schedule helper: `get_upcoming_anime_episodes`, upcoming episodes of the logged-in user's `watching` and `plan to watch` anime, with air times from [AniList](https://docs.anilist.co/).
- Watch queue helper: `get_watch_queue`, combines the authenticated `watching` list with AniList air times to show per-anime watched vs latest released counts.
- News RSS helper: `get_mal_news`, backed by `https://myanimelist.net/rss/news.xml`.
- Streamable HTTP transport, with an optional Cloudflare Access check, for use as a custom connector in claude.ai and the Claude apps (see [HTTP mode](#http-mode)).
- Tests, including runs of every read-only tool against anonymised recordings of real MyAnimeList responses.

## Requirements

- Python `>=3.12`
- [`uv`](https://docs.astral.sh/uv/)
- A MyAnimeList API client from <https://myanimelist.net/apiconfig>

Use app type **web** and redirect URL:

```text
http://localhost:8080/callback
```

## Configuration

Create `.env` or set environment variables:

```bash
MAL_CLIENT_ID=your_client_id
MAL_CLIENT_SECRET=your_client_secret
# Optional; defaults shown:
MAL_REDIRECT_URI=http://localhost:8080/callback
MAL_CALLBACK_HOST=127.0.0.1
MAL_CALLBACK_PORT=8080
MAL_CALLBACK_TIMEOUT_SECONDS=300
MAL_PKCE_METHOD=plain
MAL_RATE_LIMIT_DELAY=0.35
# Optional token path. Default: $HERMES_HOME/secrets/mal_tokens.json, or ~/.hermes/secrets/mal_tokens.json
MAL_TOKEN_STORAGE_PATH=/opt/data/.hermes/secrets/mal_tokens.json
# Optional, HTTP mode only (see "HTTP mode"); defaults shown:
MAL_TRANSPORT=stdio
MAL_HTTP_HOST=127.0.0.1
MAL_HTTP_PORT=8000
MAL_HTTP_PATH=/mcp
# Cloudflare Access check: set both to turn it on, neither to leave it off.
# MAL_CF_ACCESS_TEAM_DOMAIN=https://<team>.cloudflareaccess.com
# MAL_CF_ACCESS_AUD=<AUD tag of the Access application>
```

Secrets must stay local. Do not commit `.env` or token files.

## Running manually

```bash
uv run main.py
```

Run the `mal_auth_login` tool to start a one-shot callback server and receive a MyAnimeList OAuth URL. Open that URL in a browser you can access, approve the app, then run `mal_auth_login_status` until it reports `authenticated`. After authorization, tokens are stored locally and refreshed automatically.

MyAnimeList OAuth uses PKCE. This server defaults `MAL_PKCE_METHOD` to `plain`, which matches MyAnimeList compatibility. Set it to `S256` only if your MyAnimeList app/API behavior explicitly supports it.

`MAL_CALLBACK_HOST` defaults to `127.0.0.1` for safety. If the MCP server runs inside Docker and the callback must be reached through a published Docker/Tailscale port, set `MAL_CALLBACK_HOST=0.0.0.0` and make sure `MAL_REDIRECT_URI` exactly matches a redirect URL registered in the MyAnimeList app settings. The callback server answers on the path of `MAL_REDIRECT_URI`, so a reverse proxy or tunnel that forwards a public URL such as `https://mcp.example.com/auth/mal/callback` unchanged to `MAL_CALLBACK_HOST:MAL_CALLBACK_PORT` works without rewriting the path.

## HTTP mode

Set `MAL_TRANSPORT=http` to serve the tools over [streamable HTTP](https://modelcontextprotocol.io/specification/latest/basic/transports#streamable-http) instead of stdio:

```bash
MAL_TRANSPORT=http uv run main.py   # http://127.0.0.1:8000/mcp
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `MAL_TRANSPORT` | `stdio` | `stdio` or `http`. |
| `MAL_HTTP_HOST` | `127.0.0.1` | Address to listen on. |
| `MAL_HTTP_PORT` | `8000` | Port to listen on. |
| `MAL_HTTP_PATH` | `/mcp` | Path of the MCP endpoint. A reverse proxy or tunnel that forwards the public path unchanged needs the same path here. |
| `MAL_CF_ACCESS_TEAM_DOMAIN` | unset | Cloudflare Access team domain, such as `https://<team>.cloudflareaccess.com`. |
| `MAL_CF_ACCESS_AUD` | unset | AUD tag of the Access application protecting the endpoint. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1,::1` | uvicorn's setting: addresses or networks trusted to send `X-Forwarded-For`, so that the access log shows the client's address instead of the proxy's. |

Invalid values, or only one of the two `MAL_CF_ACCESS_*` settings, stop the server at startup with a message naming the variable.

The server uses one MyAnimeList account, the one whose tokens are in the token file, for every caller. **Anyone who can reach the endpoint can read and change that account's lists**, so never expose it without authentication in front of it. It listens on loopback by default, so only a local reverse proxy or tunnel connector can reach it. The `mal_auth_*` tools work over HTTP as well.

With both `MAL_CF_ACCESS_*` settings, every request must carry a valid Cloudflare Access JWT (`Cf-Access-Jwt-Assertion`, checked against the team's signing keys, issuer and the application's AUD tag); anything else gets `403`. This covers requests that reach the port without going through Cloudflare, such as other local processes or a web page in a browser on the same machine (DNS rebinding). Without them there is no check and the server prints a warning at startup; use that only behind another authentication layer.

### Behind Cloudflare Tunnel and Access (claude.ai custom connector)

One way to use the server from claude.ai and the Claude apps, without opening a port:

1. Run the server with `MAL_TRANSPORT=http` on the machine that runs [`cloudflared`](https://developers.cloudflare.com/tunnel/).
2. In Cloudflare Zero Trust, create a [service token](https://developers.cloudflare.com/cloudflare-one/identity/service-tokens/) for Claude.
3. Add a tunnel route for your hostname, such as `mcp.example.com`, with the path `^/mcp(/.*)?$`, to `http://127.0.0.1:8000`. The path is an unanchored regular expression, so anchor it, or it also matches paths such as `/foo/mcp`. Turn on "Protect with Access" for the route if you want cloudflared to also check the Access JWT before forwarding requests to the server.
4. Add an Access application for `mcp.example.com/mcp` with a policy whose action is **Service Auth** and that includes the service token. To accept the token only from Claude, add a **Require** rule for [Anthropic's egress range](https://claude.com/docs/connectors/building/authentication#network-reference) (`160.79.104.0/21`) and any other addresses you call from. Include rules are alternatives, so an IP range under Include would let requests in without the token.
5. Set `MAL_CF_ACCESS_TEAM_DOMAIN` and `MAL_CF_ACCESS_AUD` (the application's AUD tag) and restart the server.
6. In claude.ai, add a custom connector with the URL `https://mcp.example.com/mcp`, sign-in disabled, and the request headers `CF-Access-Client-Id` and `CF-Access-Client-Secret` from the service token. Request headers are only offered when your account supports them for custom connectors.

Without the token, Access answers `403` before the request reaches the tunnel.

Cloudflare puts the client's address in `X-Forwarded-For`, and uvicorn logs it only for requests from an address in `FORWARDED_ALLOW_IPS`. When `cloudflared` connects from loopback, the default already covers it; when it runs in another container, set `FORWARDED_ALLOW_IPS` to its address or the narrowest network that contains it, and nothing else, since a trusted address can put any client address in the log. uvicorn takes the rightmost untrusted address in the header, so an `X-Forwarded-For` sent by the client itself is ignored. Requests from claude.ai come from Anthropic's egress addresses, not from your device.

To log in to MyAnimeList through the tunnel (`mal_auth_login` from claude.ai):

1. Add a tunnel route for the callback path, such as `^/auth/mal/callback$` on `mcp.example.com`, to `http://127.0.0.1:8080` (`MAL_CALLBACK_HOST:MAL_CALLBACK_PORT`).
2. Protect it with an Access application whose **Service Auth** policy includes only the address of the network you log in from. The callback comes from your browser after MyAnimeList's redirect and cannot carry the service token; the address match alone lets it through, and Access refuses every other address.
3. Add `https://mcp.example.com/auth/mal/callback` to the redirect URLs of your MyAnimeList app and set `MAL_REDIRECT_URI` to it.

The callback server only listens while a login is pending and ignores callbacks whose `state` does not match.

### Docker

The image runs stdio by default. For HTTP, set `MAL_TRANSPORT=http` and `MAL_HTTP_HOST=0.0.0.0`, run the container on a network shared only with `cloudflared` (or your reverse proxy), and do not publish the port. Keep the token file on a volume (`MAL_TOKEN_STORAGE_PATH`).

## Hermes MCP setup

From the Hermes runtime/container:

```bash
hermes mcp add myanimelist --command "uv --directory /opt/data/projects/myanimelist-mcp-server run main.py"
hermes mcp test myanimelist
```

If Hermes is managed via Docker, persist this repository and the token path as mounted volumes. The token file should remain private to the container/user running Hermes.

## Available tools

Search, ranking, seasonal, list and suggestion results always include each title's id, title, alternative titles, main picture, nsfw rating and mean score. Tools with a `fields` argument add the requested fields to these.

Every tool returns its data as a single text block, without structured content: [TOON](https://toonformat.dev) when that is shorter than minified JSON, and minified JSON otherwise. Both decode to the same JSON object.

### Anime

- `get_anime`: search anime.
- `get_anime_details`: fetch anime details by MAL ID.
- `get_anime_ranking`: fetch anime rankings.
- `get_seasonal_anime`: fetch anime by year/season.
- `get_anime_list`: fetch a user's public anime list (status filter optional), including each entry's status, score and progress.
- `get_my_anime_list`: **auth required**; fetch the authenticated user's anime list, including each entry's status, score and progress.
- `get_suggested_anime`: **auth required**; fetch recommendations for the authenticated user.
- `update_myanimelist`: **auth required**; update an anime list entry.
- `delete_myanimelist_item`: **auth required**; delete an anime list entry. MyAnimeList reports success even when the title was not on the list.

### Manga

- `get_manga`: search manga.
- `get_manga_details`: fetch manga details by MAL ID.
- `get_manga_ranking`: fetch manga rankings.
- `get_manga_list`: fetch a user's public manga list (status filter optional), including each entry's status, score and progress.
- `get_my_manga_list`: **auth required**; fetch the authenticated user's manga list, including each entry's status, score and progress.
- `update_mymangalist`: **auth required**; update a manga list entry.
- `delete_mymangalist_item`: **auth required**; delete a manga list entry. MyAnimeList reports success even when the title was not on the list.

### Forum

- `get_forum_boards`: fetch all MAL forum boards.
- `get_forum_topic`: fetch a forum topic by ID.
- `get_forum_topics`: fetch forum topics with optional filters (board_id, subboard_id, search, user). Without a filter such as `q`, MyAnimeList may time out.

### User/auth

- `get_user_profile`: **auth required**; fetch current user profile, including anime statistics by default.
- `mal_auth_login`: start OAuth and return an authorization URL for headless/server setups.
- `mal_auth_login_status`: check whether the OAuth callback completed and tokens were stored.
- `mal_auth_status`: inspect local OAuth token status without exposing token values.
- `mal_auth_revoke`: delete locally stored OAuth tokens.

### Episode schedule / watch queue

Both tools read the user's lists from MyAnimeList with their login, then look up the airing and not yet aired titles on AniList's public API by MyAnimeList id, so private lists work and no AniList account is needed. AniList times are the original Japanese broadcast, not streaming releases. AniList allows about 30 requests a minute; each call makes one request per 50 airing titles, and a refusal is returned as an `AniList error` with its status code.

- `get_upcoming_anime_episodes`: **auth required**; episodes of the authenticated user's `watching` and `plan to watch` anime that air in the next `hours` (default 36, at most 14 days). Returns `hours`, the window start `from` and a text `digest` with each episode's list status.
- `get_watch_queue`: **auth required**; combines the authenticated user's `watching` anime list with AniList air times to return per-title watched episodes versus latest released episodes, plus aggregate counts. Latest released is `num_episodes` for finished shows and 0 for shows not yet aired; otherwise it is the next scheduled episode minus one. It is unknown when none of these apply.

### News

- `get_mal_news`: fetch recent anime/manga news articles from the MyAnimeList RSS feed.

## Tests

```bash
uv run pytest -q
```

## Useful resources

- <https://myanimelist.net/apiconfig/references/authorization>
- <https://myanimelist.net/apiconfig/references/api/v2>
- <https://docs.anilist.co/>
