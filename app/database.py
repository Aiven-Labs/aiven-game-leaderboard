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
                    ALTER TABLE scores ADD COLUMN IF NOT EXISTS nickname VARCHAR(25);
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
            
            # Create indexes for better performance
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_id ON scores(session_id);
                CREATE INDEX IF NOT EXISTS idx_total_savings_desc ON scores(total_savings DESC);
                CREATE INDEX IF NOT EXISTS idx_claimed ON scores(email) WHERE email IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_timestamp ON scores(timestamp DESC);
            """)
            
            # Create nickname index only if column exists
            try:
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_nickname ON scores(nickname) WHERE nickname IS NOT NULL;
                    CREATE INDEX IF NOT EXISTS idx_email ON scores(email) WHERE email IS NOT NULL;
                """)
            except Exception as e:
                logger.warning(f"Could not create nickname indexes: {e}")
            
        logger.info("Database tables created/verified")

    async def submit_score(self, session_id: str, final_bill: int, total_savings: int, timestamp: str):
        """Submit a new score to the database"""
        async with self.pool.acquire() as conn:
            # For simplicity, just use current UTC time
            # The client timestamp is informational but we'll use server time for consistency
            dt = datetime.utcnow()
            
            await conn.execute("""
                INSERT INTO scores (session_id, final_bill, total_savings, timestamp)
                VALUES ($1, $2, $3, $4)
            """, session_id, final_bill, total_savings, dt)

    async def get_score(self, session_id: str):
        """Get a score by session ID"""
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                SELECT * FROM scores WHERE session_id = $1
            """, session_id)

    async def claim_score(self, session_id: str, email: str, nickname: str):
        """Claim a score with both email and nickname"""
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE scores 
                SET email = $1, nickname = $2, claimed_at = CURRENT_TIMESTAMP
                WHERE session_id = $3 AND email IS NULL
            """, email, nickname, session_id)
            
            # Check if update was successful (row was found and updated)
            return result == "UPDATE 1"

    async def check_nickname_taken(self, nickname: str, exclude_session: str = None):
        """Check if a nickname is already taken by another player"""
        async with self.pool.acquire() as conn:
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
                SELECT final_bill, total_savings FROM scores WHERE session_id = $1
            """, session_id)
            
            if not score_row:
                return None
            
            # Calculate rank based on highest total savings (better score = more savings)
            rank_row = await conn.fetchrow("""
                SELECT COUNT(*) + 1 as rank
                FROM scores 
                WHERE total_savings > $1
            """, score_row['total_savings'])
            
            # Get total number of scores
            total_row = await conn.fetchrow("SELECT COUNT(*) as total FROM scores")
            
            rank = rank_row['rank']
            total = total_row['total']
            
            # Consider top 10% as "high scores"
            is_high_score = rank <= max(1, total * 0.1)
            
            return {
                'is_high_score': is_high_score,
                'rank': rank,
                'total_scores': total
            }

    async def get_leaderboard(self, limit: int = 10):
        """Get top scores leaderboard based on total savings"""
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
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
                LIMIT $1
            """, limit)

# Global database instance - will be initialized when the module loads
database = Database()