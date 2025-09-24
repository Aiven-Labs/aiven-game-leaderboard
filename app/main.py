from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from datetime import datetime
import logging
import os
from contextlib import asynccontextmanager

from .database import database
from .models import ScoreSubmission, ClaimData, ScoreResponse, HighScoreCheck, ClaimResponse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await database.connect()
    logger.info("Application started")
    yield
    # Shutdown
    await database.disconnect()
    logger.info("Application stopped")

app = FastAPI(
    title="Inkless Game Score API",
    description="High score system for the Inkless space invaders game",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),  # In production, specify your game's domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def format_money(amount: int) -> str:
    """Format money display"""
    if amount >= 1000:
        return f"${amount/1000:.1f}k"
    return f"${amount}"

@app.get("/")
async def root():
    """API health check"""
    return {"message": "Inkless Game Score API", "status": "healthy"}

@app.post("/api/scores", response_model=ScoreResponse)
async def submit_score(score: ScoreSubmission):
    """Submit a game score"""
    try:
        logger.info(f"Attempting to submit score: {score}")
        
        await database.submit_score(
            score.session_id, 
            score.final_bill, 
            score.total_savings, 
            score.timestamp
        )
        
        logger.info(f"Score submitted for session {score.session_id}: bill=${score.final_bill}, savings=${score.total_savings}")
        
        return ScoreResponse(
            success=True,
            session_id=score.session_id,
            message="Score submitted successfully"
        )
        
    except Exception as e:
        logger.error(f"Error submitting score: {e}")
        logger.error(f"Error type: {type(e)}")
        logger.error(f"Error details: {str(e)}")
        
        if "duplicate key" in str(e).lower():
            raise HTTPException(status_code=400, detail=f"Session ID already exists: {score.session_id}")
        
        # For debugging, return more detailed error info
        raise HTTPException(status_code=500, detail=f"Failed to submit score: {str(e)}")

@app.get("/api/check-high-score/{session_id}", response_model=HighScoreCheck)
async def check_high_score(session_id: str):
    """Check if a score is a high score"""
    try:
        result = await database.check_high_score(session_id)
        if not result:
            raise HTTPException(status_code=404, detail="Score not found")
        
        return HighScoreCheck(**result)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking high score: {e}")
        raise HTTPException(status_code=500, detail="Failed to check high score")

@app.get("/claim/{session_id}", response_class=HTMLResponse)
async def claim_page(session_id: str, request: Request):
    """Display the claim page for a session"""
    try:
        row = await database.get_score(session_id)
        
        if not row:
            return HTMLResponse("""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Score Not Found</title>
                    <meta name="viewport" content="width=device-width, initial-scale=1">
                    <style>
                        body { font-family: Arial, sans-serif; text-align: center; margin: 50px; background: #f8f9fa; }
                        .container { background: white; padding: 40px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); max-width: 500px; margin: 0 auto; }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>🚫 Score Not Found</h1>
                        <p>The score you're looking for doesn't exist or has been removed.</p>
                        <p><a href="/">← Back to API</a></p>
                    </div>
                </body>
                </html>
            """, status_code=404)
        
        final_bill_formatted = format_money(row['final_bill'])
        total_savings_formatted = format_money(row['total_savings'])
        
        # If already claimed
        if row['email']:
            company_display = f"<br><strong>Company:</strong> {row.get('company', 'Not specified')}" if row.get('company') else ""
            return HTMLResponse(f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Score Already Claimed</title>
                    <meta name="viewport" content="width=device-width, initial-scale=1">
                    <style>
                        body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; background: #f8f9fa; }}
                        .container {{ background: white; padding: 30px; border-radius: 10px; text-align: center; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                        .score {{ font-size: 24px; margin: 20px 0; background: #e9ecef; padding: 15px; border-radius: 5px; }}
                        .claimed {{ color: #28a745; font-weight: bold; margin: 20px 0; }}
                        .meta {{ color: #6c757d; font-size: 14px; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>🎉 Score Already Claimed!</h1>
                        <div class="score">
                            <strong>Final Bill:</strong> {final_bill_formatted}<br>
                            <strong>Total Saved:</strong> {total_savings_formatted}
                        </div>
                        <div class="claimed">This score was claimed by:<br><strong>"{row['nickname'] or 'Anonymous'}"</strong>{company_display}</div>
                        <div class="meta">
                            <p>Claimed on: {row['claimed_at'].strftime('%B %d, %Y at %I:%M %p')}</p>
                            <p>Game played on: {row['timestamp'].strftime('%B %d, %Y at %I:%M %p')}</p>
                        </div>
                    </div>
                </body>
                </html>
            """)
        
        # Show claim form
        return HTMLResponse(f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Claim Your Score - Inkless Game</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; background: #f8f9fa; }}
                    .container {{ background: white; padding: 30px; border-radius: 10px; text-align: center; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                    .score {{ font-size: 24px; margin: 20px 0; background: #e9ecef; padding: 15px; border-radius: 5px; }}
                    .form {{ margin: 30px 0; }}
                    input[type="email"], input[type="text"] {{ padding: 12px; font-size: 16px; width: 300px; max-width: 100%; border: 2px solid #ddd; border-radius: 5px; margin-top: 10px; }}
                    button {{ padding: 12px 30px; font-size: 16px; background: #007bff; color: white; border: none; border-radius: 5px; cursor: pointer; margin-top: 15px; transition: background 0.3s; }}
                    button:hover {{ background: #0056b3; }}
                    button:disabled {{ background: #6c757d; cursor: not-allowed; }}
                    .message {{ margin: 20px 0; padding: 15px; border-radius: 5px; }}
                    .success {{ background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }}
                    .error {{ background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }}
                    .high-score {{ background: #fff3cd; color: #856404; border: 1px solid #ffeaa7; padding: 15px; margin: 20px 0; border-radius: 5px; animation: pulse 2s infinite; }}
                    .meta {{ color: #6c757d; font-size: 14px; margin: 10px 0; }}
                    .optional {{ color: #6c757d; font-size: 12px; }}
                    @keyframes pulse {{ 0% {{ opacity: 1; }} 50% {{ opacity: 0.7; }} 100% {{ opacity: 1; }} }}
                    .loading {{ display: none; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>🎮 Claim Your Score!</h1>
                    <div class="score">
                        <strong>Final Bill:</strong> {final_bill_formatted}<br>
                        <strong>Total Saved:</strong> {total_savings_formatted}
                    </div>
                    
                    <div class="meta">Game played on: {row['timestamp'].strftime('%B %d, %Y at %I:%M %p')}</div>
                    
                    <div id="highScoreCheck"></div>
                    
                    <div class="form" id="claimSection">
                        <p><strong>Claim your score to join the leaderboard!</strong></p>
                        <form id="claimForm">
                            <input type="email" id="email" placeholder="your@email.com" required>
                            <br>
                            <input type="text" id="nickname" placeholder="Your leaderboard nickname" maxlength="20" required>
                            <br>
                            <small style="color: #6c757d;">Nickname: 2-20 characters, letters, numbers, and basic symbols only</small>
                            <br>
                            <input type="text" id="company" placeholder="Company name (optional)" maxlength="50">
                            <br>
                            <small class="optional">Company field is optional</small>
                            <br>
                            <button type="submit" id="submitBtn">
                                <span class="loading">⏳ Claiming...</span>
                                <span class="normal">Claim Score</span>
                            </button>
                        </form>
                        <p style="font-size: 12px; color: #6c757d; margin-top: 15px;">
                            📧 Your email is used for score verification and updates. Only your nickname and company (if provided) appear on the public leaderboard.
                        </p>
                    </div>
                    
                    <div id="message"></div>
                </div>

                <script>
                    // Check if this is a high score
                    fetch('/api/check-high-score/{session_id}')
                        .then(response => response.json())
                        .then(data => {{
                            if (data.is_high_score) {{
                                document.getElementById('highScoreCheck').innerHTML = 
                                    `<div class="high-score">🏆 HIGH SCORE! You saved {total_savings_formatted}!<br>
                                    Rank #${{data.rank}} out of ${{data.total_scores}} players!</div>`;
                            }}
                        }})
                        .catch(err => console.log('Could not check high score status'));

                    document.getElementById('claimForm').addEventListener('submit', async (e) => {{
                        e.preventDefault();
                        const email = document.getElementById('email').value;
                        const nickname = document.getElementById('nickname').value;
                        const company = document.getElementById('company').value || null;
                        const messageDiv = document.getElementById('message');
                        const submitBtn = document.getElementById('submitBtn');
                        
                        // Show loading state
                        submitBtn.disabled = true;
                        submitBtn.querySelector('.loading').style.display = 'inline';
                        submitBtn.querySelector('.normal').style.display = 'none';
                        
                        try {{
                            const response = await fetch('/api/claim/{session_id}', {{
                                method: 'POST',
                                headers: {{ 'Content-Type': 'application/json' }},
                                body: JSON.stringify({{ email: email, nickname: nickname, company: company }})
                            }});
                            
                            const data = await response.json();
                            
                            if (response.ok) {{
                                let message = '<div class="success">✅ Score claimed successfully!</div>';
                                if (data.is_high_score) {{
                                    message += `<div class="high-score">🎉 High Score Confirmed! You ranked #${{data.rank}}!</div>`;
                                }}
                                messageDiv.innerHTML = message;
                                document.getElementById('claimSection').style.display = 'none';
                            }} else {{
                                messageDiv.innerHTML = '<div class="error">❌ ' + data.detail + '</div>';
                                // Reset button
                                submitBtn.disabled = false;
                                submitBtn.querySelector('.loading').style.display = 'none';
                                submitBtn.querySelector('.normal').style.display = 'inline';
                            }}
                        }} catch (err) {{
                            messageDiv.innerHTML = '<div class="error">❌ Error claiming score. Please try again.</div>';
                            // Reset button
                            submitBtn.disabled = false;
                            submitBtn.querySelector('.loading').style.display = 'none';
                            submitBtn.querySelector('.normal').style.display = 'inline';
                        }}
                    }});
                </script>
            </body>
            </html>
        """)
        
    except Exception as e:
        logger.error(f"Error displaying claim page: {e}")
        return HTMLResponse("Internal server error", status_code=500)

@app.post("/api/claim/{session_id}", response_model=ClaimResponse)
async def claim_score(session_id: str, claim_data: ClaimData):
    """Claim a score with email, nickname, and optional company"""
    try:
        # First check if the score exists
        score = await database.get_score(session_id)
        if not score:
            raise HTTPException(status_code=404, detail="Score not found")
        
        # Check if already claimed
        if score['email']:
            raise HTTPException(status_code=400, detail=f"Score has already been claimed")
        
        # Check if nickname is already taken by a different email
        existing_user = await database.get_user_by_nickname(claim_data.nickname)
        if existing_user:
            if existing_user['email'].lower() != claim_data.email.lower():
                raise HTTPException(status_code=400, detail=f"Nickname '{claim_data.nickname}' is already taken. Please choose another.")
            # Same email can reuse the nickname - this allows users to claim multiple scores with the same nickname
        
        # Claim the score
        success = await database.claim_score(session_id, claim_data.email, claim_data.nickname, claim_data.company)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to claim score")
        
        # Check if it's a high score
        high_score_info = await database.check_high_score(session_id)
        
        company_log = f" from {claim_data.company}" if claim_data.company else ""
        logger.info(f"Score claimed for session {session_id} by '{claim_data.nickname}' ({claim_data.email}){company_log}")
        
        return ClaimResponse(
            success=True,
            message="Score claimed successfully!",
            is_high_score=high_score_info['is_high_score'] if high_score_info else False,
            rank=high_score_info['rank'] if high_score_info else None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error claiming score: {e}")
        raise HTTPException(status_code=500, detail="Failed to claim score")

@app.get("/api/leaderboard")
async def get_leaderboard(limit: int = 10):
    """Get the leaderboard showing only claimed scores with nicknames"""
    try:
        logger.info("Getting leaderboard")
        all_scores = await database.get_leaderboard()
        
        # Filter to only include claimed scores (those with nicknames and emails)
        claimed_scores = []
        for score in all_scores:
            # Only include entries that have been claimed (have nickname and email)
            # Use .get() to safely access potentially missing fields
            nickname = score.get('nickname')
            email = score.get('email')
            
            if nickname and email:
                claimed_scores.append(score)
        
        # Sort by total_savings descending (highest savings first)
        claimed_scores.sort(key=lambda x: x['total_savings'], reverse=True)
        
        # Take only the requested limit
        top_scores = claimed_scores[:limit]
        
        # Format the leaderboard response
        leaderboard = []
        for i, score in enumerate(top_scores):
            entry = {
                "rank": i + 1,
                "total_savings": format_money(score['total_savings']),
                "final_bill": format_money(score['final_bill']),
                "nickname": score.get('nickname', 'Anonymous'),
                "timestamp": score['timestamp'].isoformat(),
                "claimed": True  # All these are claimed by definition
            }
            
            # Add company if it exists and is not empty
            company = score.get('company')
            if company:
                entry["company"] = company
            
            leaderboard.append(entry)
        
        logger.info(f"Returning {len(leaderboard)} claimed scores for leaderboard")
        return {"leaderboard": leaderboard, "ranked_by": "total_savings"}
        
    except Exception as e:
        logger.error(f"Error getting leaderboard: {e}")
        logger.error(f"Error type: {type(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get leaderboard: {str(e)}")

@app.get("/api/all-scores")
async def get_all_scores():
    """Get all scores (claimed and unclaimed) for high score detection"""
    try:
        logger.info("Getting all scores for high score detection")
        all_scores = await database.get_leaderboard()
        
        # Sort by total_savings descending
        all_scores.sort(key=lambda x: x['total_savings'], reverse=True)
        
        # Add rank to each entry and return minimal data
        formatted_scores = []
        for i, score in enumerate(all_scores):
            formatted_scores.append({
                'total_savings': score['total_savings'],
                'final_bill': score['final_bill'],
                'rank': i + 1,
                'claimed': bool(score.get('email'))  # True if claimed, False if not
            })
        
        logger.info(f"Returning {len(formatted_scores)} total scores")
        return formatted_scores
        
    except Exception as e:
        logger.error(f"Error getting all scores: {e}")
        logger.error(f"Error type: {type(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get all scores: {str(e)}")

@app.get("/api/admin/emails")
async def get_emails(admin_key: str = None):
    """Get all collected emails - protected endpoint for business use"""
    # Simple admin protection - in production use proper authentication
    if admin_key != os.getenv("ADMIN_KEY", "your_secret_admin_key"):
        raise HTTPException(status_code=403, detail="Access denied")
    
    try:
        async with database.pool.acquire() as conn:
            # Check if company column exists first
            try:
                company_exists = await conn.fetchval("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name='scores' AND column_name='company';
                """)
                
                if company_exists:
                    emails = await conn.fetch("""
                        SELECT email, nickname, company, total_savings, final_bill, claimed_at, timestamp
                        FROM scores 
                        WHERE email IS NOT NULL 
                        ORDER BY claimed_at DESC
                    """)
                else:
                    emails = await conn.fetch("""
                        SELECT email, nickname, total_savings, final_bill, claimed_at, timestamp
                        FROM scores 
                        WHERE email IS NOT NULL 
                        ORDER BY claimed_at DESC
                    """)
            except Exception:
                # Fallback query without company column
                emails = await conn.fetch("""
                    SELECT email, nickname, total_savings, final_bill, claimed_at, timestamp
                    FROM scores 
                    WHERE email IS NOT NULL 
                    ORDER BY claimed_at DESC
                """)
        
        email_list = []
        for record in emails:
            entry = {
                "email": record['email'],
                "nickname": record['nickname'],
                "total_savings": record['total_savings'],
                "final_bill": record['final_bill'],
                "claimed_at": record['claimed_at'].isoformat(),
                "game_played": record['timestamp'].isoformat()
            }
            
            # Add company if it exists in the record
            if 'company' in record._mapping and record['company']:
                entry["company"] = record['company']
            
            email_list.append(entry)
        
        return {
            "total_emails": len(email_list),
            "emails": email_list
        }
        
    except Exception as e:
        logger.error(f"Error getting emails: {e}")
        raise HTTPException(status_code=500, detail="Failed to get emails")

@app.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard_page():
    """Serve the leaderboard page"""
    try:
        # Use absolute path relative to the current file location
        current_dir = os.path.dirname(os.path.abspath(__file__))
        leaderboard_path = os.path.join(current_dir, "leaderboard.html")
        
        logger.info(f"Looking for leaderboard.html at: {leaderboard_path}")
        
        with open(leaderboard_path, "r", encoding="utf-8") as f:
            content = f.read()
            logger.info("Successfully loaded leaderboard.html")
            return HTMLResponse(content)
            
    except FileNotFoundError:
        logger.error(f"leaderboard.html not found at {leaderboard_path}")
        # List files in current directory for debugging
        try:
            files = os.listdir(os.path.dirname(os.path.abspath(__file__)))
            logger.error(f"Files in directory: {files}")
        except Exception as e:
            logger.error(f"Could not list directory: {e}")
        raise HTTPException(status_code=404, detail="Leaderboard page not found")
    except Exception as e:
        logger.error(f"Error serving leaderboard page: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/debug/info")
async def debug_info():
    """Debug endpoint to check deployment status"""
    from .models import ClaimData
    
    # Fix for Pydantic v2 compatibility
    try:
        # Try Pydantic v2 syntax first
        claim_fields = list(ClaimData.model_fields.keys())
    except AttributeError:
        # Fallback to Pydantic v1 syntax
        try:
            claim_fields = list(ClaimData.__fields__.keys())
        except AttributeError:
            claim_fields = ["error: could not get fields"]
    
    # Check database schema safely
    company_column = None
    try:
        if hasattr(database, 'pool') and database.pool:
            async with database.pool.acquire() as conn:
                company_column = await conn.fetchval("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name='scores' AND column_name='company';
                """)
        else:
            company_column = "database pool not available"
    except Exception as e:
        company_column = f"error: {str(e)}"
    
    # Check file system
    current_dir = os.path.dirname(os.path.abspath(__file__))
    leaderboard_exists = os.path.exists(os.path.join(current_dir, "leaderboard.html"))
    
    try:
        files_in_dir = os.listdir(current_dir)
    except Exception as e:
        files_in_dir = [f"error listing files: {str(e)}"]
    
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "current_working_directory": os.getcwd(),
        "file_location": __file__,
        "claim_data_fields": claim_fields,
        "has_company_field": "company" in claim_fields,
        "company_column_exists": bool(company_column),
        "company_column_value": company_column,
        "files_in_directory": files_in_dir,
        "leaderboard_file_exists": leaderboard_exists,
        "leaderboard_path": os.path.join(current_dir, "leaderboard.html"),
        "database_pool_status": "available" if hasattr(database, 'pool') and database.pool else "not available"
    }

# Test endpoint for claim form
@app.get("/test-claim/{session_id}")
async def test_claim_form(session_id: str):
    """Test endpoint to see current claim form"""
    return await claim_page(session_id, None)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)