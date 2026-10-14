import asyncpg
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

    async def submit_score(self, session_id: str, final_bill: int, total_savings: int, timestamp: str, source: str = 'arcade', event: str = None):
        """Submit a new score to the database"""
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

            # For simplicity, just use current UTC time
            # The client timestamp is informational but we'll use server time for consistency
            dt = datetime.utcnow()
            
            await conn.execute("""
                INSERT INTO scores (session_id, final_bill, total_savings, timestamp, source, event)
                VALUES ($1, $2, $3, $4, $5, $6)
            """, session_id, final_bill, total_savings, dt, source, event)

    async def get_score(self, session_id: str):
        """Get a score by session ID"""
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                SELECT * FROM scores WHERE session_id = $1
            """, session_id)
    
    async def get_user_by_nickname(self, nickname: str, event: str = None):
        """Get user info by nickname, within one event (nicknames only need to be unique per board)"""
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """SELECT email, nickname FROM scores
                   WHERE nickname = $1 AND email IS NOT NULL
                     AND event IS NOT DISTINCT FROM $2
                   LIMIT 1""",
                nickname, event
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
        """Check if a score is a high score and get ranking info"""
        async with self.pool.acquire() as conn:
            # Get the score details
            score_row = await conn.fetchrow("""
                SELECT final_bill, total_savings, event FROM scores WHERE session_id = $1
            """, session_id)
            
            if not score_row:
                return None
            
            # Calculate rank based on highest total savings among CLAIMED scores only
            rank_row = await conn.fetchrow("""
                SELECT COUNT(*) + 1 as rank
                FROM scores 
                WHERE total_savings > $1
                AND email IS NOT NULL
                AND event IS NOT DISTINCT FROM $2
            """, score_row['total_savings'], score_row['event'])
            
            # Get total number of CLAIMED scores
            total_row = await conn.fetchrow("""
                SELECT COUNT(*) as total 
                FROM scores 
                WHERE email IS NOT NULL
                AND event IS NOT DISTINCT FROM $1
            """, score_row['event'])
            
            rank = rank_row['rank']
            total = total_row['total']
            
            # Consider top 10% as "high scores"
            is_high_score = rank <= max(1, total * 0.1) if total > 0 else False
            
            return {
                'event': score_row['event'],
                'is_high_score': is_high_score,
                'rank': rank,
                'total_scores': total
            }

    async def get_leaderboard(self, limit: int = None, source: str = None, event: str = None):
        """Get all scores for leaderboard (claimed and unclaimed)"""
        async with self.pool.acquire() as conn:
            # Check if nickname and company columns exist first
            try:
                nickname_exists = await conn.fetchval("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name='scores' AND column_name='nickname';
                """)
                
                company_exists = await conn.fetchval("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name='scores' AND column_name='company';
                """)
                
                # Build query based on available columns
                select_fields = [
                    "session_id",
                    "final_bill", 
                    "total_savings",
                    "email",
                    "timestamp",
                    "claimed_at",
                    "source",
                    "event"
                ]
                
                if nickname_exists:
                    select_fields.append("nickname")
                if company_exists:
                    select_fields.append("company")
                
                select_clause = ", ".join(select_fields)
                
                # event: None = every score; "none" = scores with no event; otherwise one event's board
                clauses, args = [], []
                if source:
                    args.append(source)
                    clauses.append(f"source = ${len(args)}")
                if event == "none":
                    clauses.append("event IS NULL")
                elif event:
                    args.append(event)
                    clauses.append(f"event = ${len(args)}")
                where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
                query = f"""
                    SELECT 
                        {select_clause},
                        ROW_NUMBER() OVER (ORDER BY total_savings DESC) as rank
                    FROM scores
                    {where}
                    ORDER BY total_savings DESC
                """
                
                if limit:
                    query += f" LIMIT {int(limit)}"
                    
                rows = await conn.fetch(query, *args)
                
                # Convert to list of dictionaries and ensure all expected fields exist
                result = []
                for row in rows:
                    row_dict = dict(row)
                    if 'nickname' not in row_dict:
                        row_dict['nickname'] = None
                    if 'company' not in row_dict:
                        row_dict['company'] = None
                    result.append(row_dict)
                
                return result
                
            except Exception as e:
                logger.error(f"Error in get_leaderboard: {e}")
                # Fallback to basic query without nickname/company
                query = """
                    SELECT 
                        session_id,
                        final_bill,
                        total_savings,
                        email,
                        timestamp,
                        claimed_at,
                        ROW_NUMBER() OVER (ORDER BY total_savings DESC) as rank
                    FROM scores
                    ORDER BY total_savings DESC
                """
                if limit:
                    query += f" LIMIT {limit}"
                    
                rows = await conn.fetch(query)
                result = []
                for row in rows:
                    row_dict = dict(row)
                    row_dict['nickname'] = None  # Add missing fields
                    row_dict['company'] = None
                    result.append(row_dict)
                
                return result

    async def get_all_scores_admin(self):
        """Get every score with all columns, for the admin console"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    id,
                    session_id,
                    final_bill,
                    total_savings,
                    timestamp,
                    email,
                    nickname,
                    company,
                    claimed_at,
                    created_at,
                    source,
                    event
                FROM scores
                ORDER BY total_savings DESC
            """)
            return [dict(row) for row in rows]

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