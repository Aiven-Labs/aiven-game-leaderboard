import asyncpg
import os
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.pool = None
        self.database_url = os.getenv("DATABASE_URL")
        if not self.database_url:
            raise ValueError("DATABASE_URL environment variable is required")

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
        """Create database tables if they don't exist"""
        async with self.pool.acquire() as conn:
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
            
            # Create indexes for better performance
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_id ON scores(session_id);
                CREATE INDEX IF NOT EXISTS idx_final_bill ON scores(final_bill);
                CREATE INDEX IF NOT EXISTS idx_total_savings ON scores(total_savings DESC);
                CREATE INDEX IF NOT EXISTS idx_claimed ON scores(email) WHERE email IS NOT NULL;
            """)
            
        logger.info("Database tables created/verified")

    async def submit_score(self, session_id: str, final_bill: int, total_savings: int, timestamp: str):
        """Submit a new score to the database"""
        async with self.pool.acquire() as conn:
            # Parse timestamp
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            except ValueError:
                dt = datetime.now()
            
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

    async def claim_score(self, session_id: str, email: str):
        """Claim a score with an email"""
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE scores 
                SET email = $1, claimed_at = CURRENT_TIMESTAMP
                WHERE session_id = $2 AND email IS NULL
            """, email, session_id)
            
            # Check if update was successful (row was found and updated)
            return result == "UPDATE 1"

    async def check_high_score(self, session_id: str):
        """Check if a score is a high score and get ranking info"""
        async with self.pool.acquire() as conn:
            # Get the score details
            score_row = await conn.fetchrow("""
                SELECT final_bill, total_savings FROM scores WHERE session_id = $1
            """, session_id)
            
            if not score_row:
                return None
            
            # Calculate rank based on lowest final bill (better score = lower bill)
            rank_row = await conn.fetchrow("""
                SELECT COUNT(*) + 1 as rank
                FROM scores 
                WHERE final_bill < $1
            """, score_row['final_bill'])
            
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
        """Get top scores leaderboard"""
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT 
                    session_id,
                    final_bill,
                    total_savings,
                    email,
                    timestamp,
                    claimed_at,
                    ROW_NUMBER() OVER (ORDER BY final_bill ASC) as rank
                FROM scores
                ORDER BY final_bill ASC
                LIMIT $1
            """, limit)

# Global database instance
database = Database()