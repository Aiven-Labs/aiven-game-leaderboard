# Aiven Game Leaderboard

A shared leaderboard, score-claim and events service for games in the Aiven ecosystem.
It was extracted from [inkless-game-web](https://github.com/Aiven-Labs/inkless-game-web)
(branch `spacetimedb-authoritative`), with the history of `score_server/` preserved.
Disk Invaders is still its first game.

Built with FastAPI and PostgreSQL.

## What it does

- **Games**: any number of games, each with its own score label, format (`int`, `money`,
  `time_ms`) and sort order.
- **Per-game API tokens**: a game server posts scores with a bearer token scoped to one game.
- **Events**: self-serve event boards (conferences, booths) that can overlap in time.
  Every (game, event) pair gets its own leaderboard. See [EVENTS.md](EVENTS.md).
- **Claims**: players claim a score with a nickname and email, usually from a QR code.
- **Admin console** at `/admin` for scores, games, events and tokens, plus CSV export.

## Adding a game

The integration guide is served at `/guide`. Its source is [app/guide.md](app/guide.md).
In short: create the game and a token in `/admin`, then have your game **server**
`POST /api/v1/scores`.

## Endpoints

| Path | Purpose |
|---|---|
| `GET /api/v1/games` | Public list of games |
| `GET /api/v1/games/{game}/leaderboard?event=&source=&limit=` | Public leaderboard |
| `POST /api/v1/scores` | Game server submits a score (bearer token) |
| `/api/v1/admin/...` | Admin API (admin token or console session) |
| `GET /api/events` | Public list of events |
| `/claim/{session_id}` | Player claim page |
| `/leaderboard?game=&event=` | Leaderboard page |
| `/admin` | Admin console |
| `/guide` | Integration guide |
| `/docs` | OpenAPI docs |

The legacy Disk Invaders endpoints (`/api/scores`, `/api/leaderboard`,
`/api/check-high-score/...`) are kept so the current game keeps working.

## Configuration

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | PostgreSQL connection string. Tables are created and migrated at startup |
| `ADMIN_KEY` | yes | Admin console password |
| `INGEST_SECRET` | no | When set, the legacy `/api/scores` endpoint requires `X-Ingest-Secret` |
| `CORS_ORIGINS` | no | Comma-separated origins |
| `PUBLIC_GAME_URL` | no | Base URL for event game links and QR codes (default `https://inkless.dev`) |
| `LOG_LEVEL` | no | Default `INFO` |

## Running locally

With Docker:

```bash
docker compose up --build        # http://localhost:8000/admin  (password: local_dev_admin_key)
```

Without Docker, against any Postgres:

```bash
uv venv && uv pip install -r requirements.txt
DATABASE_URL=postgresql://... ADMIN_KEY=dev uv run uvicorn app.main:app --reload
```

## Tests

`tests/api_smoke.py` is a stdlib-only end-to-end check. It creates a throwaway game, so run
it against a local or staging instance, never production:

```bash
ADMIN_KEY=local_dev_admin_key INGEST_SECRET=local_dev_ingest python3 tests/api_smoke.py http://localhost:8000
```
