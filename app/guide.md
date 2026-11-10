# Add a leaderboard to your game

Any game can use this leaderboard service. Your game's **server** posts each finished game's
score; the player claims it with a nickname and email; it appears on that game's leaderboard.
Setup is about five minutes and needs no changes to this service.

```
 game server ── POST /api/v1/scores ──▶ leaderboard service ──▶ /leaderboard?game=your-game
                  (game token)                  ▲
 player ── scans the claim QR / link ───────────┘  (nickname + email, then they are listed)
```

## 1. Set up (in the admin console)

1. **Games tab → add a game.** Give it a slug (`my-game`), a name, and say how scores work:
   - *Score label and format*: `int` (1,234), `money` ($1.2k) or `time_ms` (1:02.345)
   - *Higher or lower wins*: points rank high-to-low; times and move counts rank low-to-high
   - *Second stat* (optional): e.g. "Level" or "Time", shown next to the score
2. **API tab → new token**, type **Game**, pick your game. **Copy the token now: it is shown
   once** and only a hash is kept. Put it in your game server's secrets, e.g. `LEADERBOARD_TOKEN`.
3. *(Optional)* **Games tab:** set a **play URL** (and a **cabinet URL** for an arcade build) so
   events can link to your game. `{event}` in the URL is replaced with the event slug, or
   `?event=<slug>` is appended, so your game should read `event` from its URL and pass it on when
   it posts a score.
4. *(Optional)* **Events tab:** create events (a conference, a booth) and tick the games played
   there. Every (game, event) pair has its own board, links and QR codes.

## 2. Post a score when a game ends

From your game's **server**:

```bash
curl -X POST "$LEADERBOARD_URL/api/v1/scores" \
  -H "Authorization: Bearer $LEADERBOARD_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "game": "my-game",
        "session_id": "'"$(openssl rand -hex 16)"'",
        "score": 4200,
        "secondary": 7,
        "details": {"level": 7, "duration_ms": 91000},
        "event": "pgconf-2026"
      }'
```

```js
// Node 18+
const res = await fetch(`${process.env.LEADERBOARD_URL}/api/v1/scores`, {
  method: 'POST',
  headers: { Authorization: `Bearer ${process.env.LEADERBOARD_TOKEN}`, 'Content-Type': 'application/json' },
  body: JSON.stringify({ game: 'my-game', session_id: crypto.randomUUID().replaceAll('-', ''), score: 4200 }),
});
const { claim_url, leaderboard_url } = await res.json();
```

```python
import os, secrets, requests
r = requests.post(f"{os.environ['LEADERBOARD_URL']}/api/v1/scores",
    headers={"Authorization": f"Bearer {os.environ['LEADERBOARD_TOKEN']}"},
    json={"game": "my-game", "session_id": secrets.token_hex(16), "score": 4200})
claim_url = r.json()["claim_url"]
```

| Field | Required | Notes |
|---|---|---|
| `game` | yes | The slug from step 1; must match the token's game |
| `session_id` | yes | 8-64 chars (`A-Z a-z 0-9 - _`). **Make it random and unguessable**: it is also the player's claim key |
| `score` | yes | Whole number, 0 to 2^53. Time games: milliseconds |
| `secondary` | no | The optional second stat |
| `details` | no | Any small JSON object (under 2,000 characters), stored and returned |
| `event` | no | An event slug. Unknown or closed events, or events that don't run this game, are saved without an event, never rejected |
| `source` | no | `web` (default), `arcade` or `other` |

The response gives you `claim_url` and `leaderboard_url`.

## 3. Let the player claim it

Show the player the `claim_url`, ideally as a **QR code** on the game-over screen. They open it,
enter a nickname and email, and are listed. Until then the score is saved but not shown.
Link to the board with `leaderboard_url` (add `&event=<slug>` for an event's board).

## Keep it honest

- **Post from your server, never from the browser.** Whoever can call the endpoint can set any
  score, so the token must never ship to the client. Compute the score on the server, from
  inputs the player cannot forge, and post only that.
- One token per game, so a leaked token only affects that game. Revoke it in the API tab and
  issue a new one.
- Re-posting a `session_id` returns `409` and stores nothing, so retries are safe.
- Nicknames only need to be unique within a (game, event) board.

## Reading and managing

Public, no token: `GET /api/v1/games` and `GET /api/v1/games/{game}/leaderboard?event=&source=&limit=`.

An **Admin** token (API tab) can script the console: `GET /api/v1/admin/scores`
(`?game=&event=&claimed=&limit=&offset=`), `PATCH`/`DELETE /api/v1/admin/scores/{session_id}`,
`GET /api/v1/admin/export.csv?game=&event=`, `PUT /api/v1/admin/games/{slug}` (with `play_url` /
`cabinet_url`), `GET /api/v1/admin/events`, `PUT /api/v1/admin/events/{slug}` (with `games: [...]`; leave it out to keep the
current list. `cta_text` + `cta_url` set the post-claim button; send both as `""` to clear, leave both out to keep) and `GET /api/v1/admin/events/{slug}/qr?game=&kind=game|leaderboard|cabinet&format=png|svg`.
Admin tokens cannot create tokens; only the console can. Send it the same way:
`Authorization: Bearer lbk_...`. The full schema is at `/docs`.

| Status | Meaning |
|---|---|
| 401 | Missing, wrong or revoked token |
| 403 | Token is for a different game, or not a game token |
| 404 | The game does not exist yet (create it in the Games tab) |
| 409 | `session_id` already recorded |
| 422 | A field is invalid; the response says which |

## Games on SpacetimeDB

If your game runs in a SpacetimeDB module, post from a **scheduled procedure with retry**, not from
a reducer. Disk Invaders does exactly this (an outbox table, and a procedure that posts and backs
off), and the module template's README explains the pattern. Two things to know: module HTTP can
only reach **public** URLs (not private or VPC addresses), and the token should arrive through
an owner-only setup reducer, never be compiled into the module.
