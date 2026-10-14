import asyncpg
import json
import os
from datetime import datetime
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.pool = None
        self.database_url = os.getenv("DATABASE_URL")
        if not self.database_url:
            raise ValueError("DATABASE_URL environment variable is required. Please check your .env file.")

    async def connect(self):
        """Create database connection pool"""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=1,
                max_size=10,
                command_timeout=60
            )
            logger.info("Database connection pool created")
            await self.create_tables()
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise

    async def disconnect(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()
            logger.info("Database connection pool closed")

    async def create_tables(self):
        """Create database tables if they don't exist and handle migrations"""
        async with self.pool.acquire() as conn:
            # Create the base table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS scores (
                    id SERIAL PRIMARY KEY,
                    session_id VARCHAR(50) UNIQUE NOT NULL,
                    final_bill INTEGER NOT NULL,
                    total_savings INTEGER NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    email VARCHAR(255),
                    claimed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            
            # Add nickname column if it doesn't exist (migration)
            try:
                await conn.execute("""
                    ALTER TABLE scores ADD COLUMN IF NOT EXISTS nickname VARCHAR(50);
                """)
            except Exception as e:
                # If ALTER TABLE IF NOT EXISTS doesn't work, try checking if column exists first
                try:
                    column_exists = await conn.fetchval("""
                        SELECT column_name FROM information_schema.columns 
                        WHERE table_name='scores' AND column_name='nickname';
                    """)
                    if not column_exists:
                        await conn.execute("ALTER TABLE scores ADD COLUMN nickname VARCHAR(25);")
                except Exception as e2:
                    logger.warning(f"Could not add nickname column: {e2}")
            
            # Add company column if it doesn't exist (new migration)
            try:
                await conn.execute("""
                    ALTER TABLE scores ADD COLUMN IF NOT EXISTS company VARCHAR(50);
                """)
            except Exception as e:
                # If ALTER TABLE IF NOT EXISTS doesn't work, try checking if column exists first
                try:
                    column_exists = await conn.fetchval("""
                        SELECT column_name FROM information_schema.columns 
                        WHERE table_name='scores' AND column_name='company';
                    """)
                    if not column_exists:
                        await conn.execute("ALTER TABLE scores ADD COLUMN company VARCHAR(50);")
                except Exception as e2:
                    logger.warning(f"Could not add company column: {e2}")
            
            # Where the score was played: 'arcade' (the cabinet) or 'web'. Every score
            # that existed before this column came from the arcade, hence the default.
            await conn.execute("""
                ALTER TABLE scores ADD COLUMN IF NOT EXISTS source VARCHAR(10) NOT NULL DEFAULT 'arcade';
            """)

            # Which event a score was played at (events.slug), or NULL for none
            await conn.execute("""
                ALTER TABLE scores ADD COLUMN IF NOT EXISTS event VARCHAR(40);
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_event ON scores(event) WHERE event IS NOT NULL;
            """)

            # Events run side by side (two on the same dates is fine), so a score is tagged with
            # its event when it is played rather than inferred from the date. The window, if
            # set, only stops late plays being tagged to a finished event.
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    slug VARCHAR(40) PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    starts_at TIMESTAMP,
                    ends_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # Starter events; names and dates are editable in the admin console and never overwritten
            await conn.execute("""
                INSERT INTO events (slug, name) VALUES
                    ('ato-2026', 'All Things Open 2026'),
                    ('pgconf-2026', 'PGConf 2026')
                ON CONFLICT (slug) DO NOTHING;
            """)

            # ---- multiple games --------------------------------------------------------
            # A game says how its scores rank and display, so one leaderboard service can serve
            # any number of games. Disk Invaders is seeded as game zero.
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS games (
                    slug VARCHAR(40) PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    score_label VARCHAR(40) NOT NULL DEFAULT 'Score',
                    score_format VARCHAR(10) NOT NULL DEFAULT 'int',
                    sort_order VARCHAR(4) NOT NULL DEFAULT 'desc',
                    secondary_label VARCHAR(40),
                    secondary_format VARCHAR(10),
                    highscore_text VARCHAR(200) NOT NULL DEFAULT 'Your score: {score}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await conn.execute("""
                INSERT INTO games (slug, name, score_label, score_format, sort_order,
                                   secondary_label, secondary_format, highscore_text)
                VALUES ('disk-invaders', 'Disk Invaders', 'Saved', 'money', 'desc',
                        'Final bill', 'money', 'You saved {score}!')
                ON CONFLICT (slug) DO NOTHING;
            """)
            await conn.execute("ALTER TABLE scores ADD COLUMN IF NOT EXISTS game VARCHAR(40) NOT NULL DEFAULT 'disk-invaders';")
            await conn.execute("ALTER TABLE scores ADD COLUMN IF NOT EXISTS score BIGINT;")
            await conn.execute("ALTER TABLE scores ADD COLUMN IF NOT EXISTS secondary BIGINT;")
            await conn.execute("ALTER TABLE scores ADD COLUMN IF NOT EXISTS details JSONB;")
            # Every score so far is a Disk Invaders score: savings is the ranked score, the bill its
            # secondary. total_savings / final_bill stay populated for Disk Invaders (safe to roll back).
            await conn.execute("UPDATE scores SET score = total_savings, secondary = final_bill WHERE score IS NULL;")
            await conn.execute("ALTER TABLE scores ALTER COLUMN total_savings DROP NOT NULL;")
            await conn.execute("ALTER TABLE scores ALTER COLUMN final_bill DROP NOT NULL;")
            await conn.execute("ALTER TABLE scores ALTER COLUMN score SET NOT NULL;")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_game_event_score ON scores(game, event, score);")

            # API tokens: only a hash is stored, so a lost token is replaced, never recovered.
            #   kind 'game'  - may submit scores for one game
            #   kind 'admin' - may use the management API (cannot mint tokens)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS api_tokens (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    kind VARCHAR(10) NOT NULL,
                    game VARCHAR(40),
                    token_prefix VARCHAR(16) NOT NULL,
                    token_hash VARCHAR(64) UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TIMESTAMP,
                    revoked_at TIMESTAMP
                );
            """)

            # Create indexes for better performance
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_id ON scores(session_id);
                CREATE INDEX IF NOT EXISTS idx_total_savings_desc ON scores(total_savings DESC);
                CREATE INDEX IF NOT EXISTS idx_claimed ON scores(email) WHERE email IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_timestamp ON scores(timestamp DESC);
            """)
            
            # Create nickname and company indexes only if columns exist
            try:
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_nickname ON scores(nickname) WHERE nickname IS NOT NULL;
                    CREATE INDEX IF NOT EXISTS idx_email ON scores(email) WHERE email IS NOT NULL;
                    CREATE INDEX IF NOT EXISTS idx_company ON scores(company) WHERE company IS NOT NULL;
                """)
            except Exception as e:
                logger.warning(f"Could not create nickname/company indexes: {e}")
            
        logger.info("Database tables created/verified")

    async def insert_score(self, game: str, session_id: str, score: int, secondary: int = None,
                           details: dict = None, source: str = 'arcade', event: str = None):
        """Store a score for any game. Unclaimed until its owner attaches a nickname and email."""
        async with self.pool.acquire() as conn:
            # Only tag the score if the event exists and is open right now; otherwise it is
            # still saved, just not on any event board
            if event:
                open_event = await conn.fetchval("""
                    SELECT slug FROM events
                    WHERE slug = $1
                      AND (starts_at IS NULL OR starts_at <= (NOW() AT TIME ZONE 'UTC'))
                      AND (ends_at IS NULL OR ends_at >= (NOW() AT TIME ZONE 'UTC'))
                """, event)
                if not open_event:
                    logger.info(f"Score {session_id}: event '{event}' unknown or closed, saving untagged")
                    event = None

            # Server time, not the caller's, so boards can't be back- or forward-dated
            dt = datetime.utcnow()
            # Disk Invaders also keeps its original columns filled, so a rollback loses nothing
            legacy_savings = score if game == 'disk-invaders' else None
            legacy_bill = secondary if game == 'disk-invaders' else None

            await conn.execute("""
                INSERT INTO scores (session_id, game, score, secondary, details, total_savings, final_bill,
                                    timestamp, source, event)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10)
            """, session_id, game, score, secondary,
                json.dumps(details) if details is not None else None,
                legacy_savings, legacy_bill, dt, source, event)

    async def submit_score(self, session_id: str, final_bill: int, total_savings: int, timestamp: str, source: str = 'arcade', event: str = None):
        """Original Disk Invaders entry point (the game server still posts here)"""
        await self.insert_score('disk-invaders', session_id, total_savings, final_bill, None, source, event)

    async def get_score(self, session_id: str):
        """Get a score by session ID"""
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                SELECT * FROM scores WHERE session_id = $1
            """, session_id)
    
    async def get_user_by_nickname(self, nickname: str, event: str = None, game: str = 'disk-invaders'):
        """Get user info by nickname, within one board (nicknames only need to be unique per game and event)"""
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """SELECT email, nickname FROM scores
                   WHERE nickname = $1 AND email IS NOT NULL
                     AND game = $3
                     AND event IS NOT DISTINCT FROM $2
                   LIMIT 1""",
                nickname, event, game
            )

    async def claim_score(self, session_id: str, email: str, nickname: str, company: str = None):
        """Claim a score with email, nickname, and optional company"""
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE scores 
                SET email = $1, nickname = $2, company = $3, claimed_at = CURRENT_TIMESTAMP
                WHERE session_id = $4 AND email IS NULL
            """, email, nickname, company, session_id)
            
            # Check if update was successful (row was found and updated)
            return result == "UPDATE 1"

    async def check_nickname_taken(self, nickname: str, email: str = None, exclude_session: str = None):
        """Check if a nickname is already taken by another player with different email"""
        async with self.pool.acquire() as conn:
            if email:
                # Allow same email to reuse nickname
                if exclude_session:
                    result = await conn.fetchval("""
                        SELECT COUNT(*) FROM scores 
                        WHERE LOWER(nickname) = LOWER($1) 
                        AND email != $2
                        AND session_id != $3
                        AND nickname IS NOT NULL
                    """, nickname, email, exclude_session)
                else:
                    result = await conn.fetchval("""
                        SELECT COUNT(*) FROM scores 
                        WHERE LOWER(nickname) = LOWER($1)
                        AND email != $2
                        AND nickname IS NOT NULL
                    """, nickname, email)
            else:
                # Original logic for when no email provided
                if exclude_session:
                    result = await conn.fetchval("""
                        SELECT COUNT(*) FROM scores 
                        WHERE LOWER(nickname) = LOWER($1) 
                        AND session_id != $2
                        AND nickname IS NOT NULL
                    """, nickname, exclude_session)
                else:
                    result = await conn.fetchval("""
                        SELECT COUNT(*) FROM scores 
                        WHERE LOWER(nickname) = LOWER($1)
                        AND nickname IS NOT NULL
                    """, nickname)
            
            return result > 0

    async def check_high_score(self, session_id: str):
        """Check if a score is a high score and get ranking info (within its own game and event)"""
        async with self.pool.acquire() as conn:
            score_row = await conn.fetchrow("""
                SELECT game, score, event FROM scores WHERE session_id = $1
            """, session_id)
            if not score_row:
                return None

            game = await conn.fetchrow("SELECT sort_order FROM games WHERE slug = $1", score_row['game'])
            better = "<" if game and game['sort_order'] == 'asc' else ">"

            # Rank among CLAIMED scores only
            rank_row = await conn.fetchrow(f"""
                SELECT COUNT(*) + 1 as rank
                FROM scores
                WHERE game = $1 AND score {better} $2
                AND email IS NOT NULL
                AND event IS NOT DISTINCT FROM $3
            """, score_row['game'], score_row['score'], score_row['event'])

            total_row = await conn.fetchrow("""
                SELECT COUNT(*) as total
                FROM scores
                WHERE game = $1 AND email IS NOT NULL
                AND event IS NOT DISTINCT FROM $2
            """, score_row['game'], score_row['event'])

            rank = rank_row['rank']
            total = total_row['total']

            # Consider top 10% as "high scores"
            is_high_score = rank <= max(1, total * 0.1) if total > 0 else False

            return {
                'game': score_row['game'],
                'event': score_row['event'],
                'is_high_score': is_high_score,
                'rank': rank,
                'total_scores': total
            }

    async def get_leaderboard(self, limit: int = None, source: str = None, event: str = None, game: str = 'disk-invaders'):
        """Scores for one game's leaderboard (claimed and unclaimed; callers filter), best first.

        event: None = every score; "none" = scores with no event; otherwise one event's board.
        Ranks honour the game's sort order (lowest wins for time-based games)."""
        async with self.pool.acquire() as conn:
            game_row = await conn.fetchrow("SELECT sort_order FROM games WHERE slug = $1", game)
            direction = "ASC" if game_row and game_row['sort_order'] == 'asc' else "DESC"

            clauses, args = ["game = $1"], [game]
            if source:
                args.append(source)
                clauses.append(f"source = ${len(args)}")
            if event == "none":
                clauses.append("event IS NULL")
            elif event:
                args.append(event)
                clauses.append(f"event = ${len(args)}")

            query = f"""
                SELECT session_id, game, score, secondary, details, email, nickname, company,
                       timestamp, claimed_at, source, event,
                       ROW_NUMBER() OVER (ORDER BY score {direction}, timestamp ASC) as rank
                FROM scores
                WHERE {" AND ".join(clauses)}
                ORDER BY score {direction}, timestamp ASC
            """
            if limit:
                query += f" LIMIT {int(limit)}"

            rows = await conn.fetch(query, *args)
            return [dict(r) for r in rows]

    async def get_all_scores_admin(self, game: str = None):
        """Scores with every column, for the admin console (optionally one game's)"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(f"""
                SELECT
                    s.id,
                    s.session_id,
                    s.game,
                    s.score,
                    s.secondary,
                    s.details,
                    s.timestamp,
                    s.email,
                    s.nickname,
                    s.company,
                    s.claimed_at,
                    s.created_at,
                    s.source,
                    s.event,
                    COALESCE(g.sort_order, 'desc') AS sort_order
                FROM scores s
                LEFT JOIN games g ON g.slug = s.game
                {"WHERE s.game = $1" if game else ""}
                ORDER BY s.game, CASE WHEN COALESCE(g.sort_order, 'desc') = 'asc' THEN s.score END ASC,
                         s.score DESC
            """, *([game] if game else []))
            return [dict(row) for row in rows]

    # ---- games --------------------------------------------------------------

    async def list_games(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT g.*, (SELECT COUNT(*) FROM scores s WHERE s.game = g.slug) AS score_count
                FROM games g ORDER BY g.created_at, g.slug
            """)
            return [dict(r) for r in rows]

    async def get_game(self, slug: str):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM games WHERE slug = $1", slug)
            return dict(row) if row else None

    async def upsert_game(self, slug: str, name: str, score_label: str, score_format: str,
                          sort_order: str, secondary_label, secondary_format, highscore_text: str):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO games (slug, name, score_label, score_format, sort_order,
                                   secondary_label, secondary_format, highscore_text)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name,
                    score_label = EXCLUDED.score_label, score_format = EXCLUDED.score_format,
                    sort_order = EXCLUDED.sort_order, secondary_label = EXCLUDED.secondary_label,
                    secondary_format = EXCLUDED.secondary_format, highscore_text = EXCLUDED.highscore_text
            """, slug, name, score_label, score_format, sort_order, secondary_label, secondary_format, highscore_text)

    # ---- API tokens ---------------------------------------------------------

    async def create_token(self, name: str, kind: str, game, token_prefix: str, token_hash: str):
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO api_tokens (name, kind, game, token_prefix, token_hash)
                VALUES ($1, $2, $3, $4, $5) RETURNING id
            """, name, kind, game, token_prefix, token_hash)

    async def list_tokens(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, name, kind, game, token_prefix, created_at, last_used_at, revoked_at
                FROM api_tokens ORDER BY created_at DESC
            """)
            return [dict(r) for r in rows]

    async def revoke_token(self, token_id: int) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE api_tokens SET revoked_at = (NOW() AT TIME ZONE 'UTC')
                WHERE id = $1 AND revoked_at IS NULL
            """, token_id)
            return result == "UPDATE 1"

    async def authenticate_token(self, token_hash: str):
        """The live token row for this hash (and stamp it as used), or None"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE api_tokens SET last_used_at = (NOW() AT TIME ZONE 'UTC')
                WHERE token_hash = $1 AND revoked_at IS NULL
                RETURNING id, name, kind, game
            """, token_hash)
            return dict(row) if row else None

    async def list_events(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT slug, name, starts_at, ends_at FROM events ORDER BY created_at, slug")
            return [dict(r) for r in rows]

    async def upsert_event(self, slug: str, name: str, starts_at, ends_at):
        """Create an event or edit its name and window (naive UTC datetimes)"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO events (slug, name, starts_at, ends_at) VALUES ($1, $2, $3, $4)
                ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name,
                    starts_at = EXCLUDED.starts_at, ends_at = EXCLUDED.ends_at
            """, slug, name, starts_at, ends_at)

    async def admin_update_score(self, session_id: str, nickname, company, email):
        """Update the player-supplied fields of a score.

        Only nickname, company and email are editable - the bill and savings are
        the game's result and are deliberately not writable from the console.
        A score that gains an email counts as claimed, so claimed_at is
        backfilled when it is still null.
        """
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE scores
                SET nickname = $1::varchar,
                    company = $2::varchar,
                    email = $3::varchar,
                    claimed_at = CASE
                        WHEN $3::varchar IS NOT NULL AND claimed_at IS NULL THEN CURRENT_TIMESTAMP
                        WHEN $3::varchar IS NULL THEN NULL
                        ELSE claimed_at
                    END
                WHERE session_id = $4
            """, nickname, company, email, session_id)

            return result == "UPDATE 1"

    async def admin_delete_score(self, session_id: str):
        """Delete a score outright - used to clear out test and junk entries"""
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                DELETE FROM scores WHERE session_id = $1
            """, session_id)

            return result == "DELETE 1"


# Global database instance - will be initialized when the module loads
database = Database()