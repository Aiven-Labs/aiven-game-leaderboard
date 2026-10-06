from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from datetime import datetime
import asyncio
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import time
from contextlib import asynccontextmanager

from .database import database
from .models import (
    ScoreSubmission,
    ClaimData,
    ScoreResponse,
    HighScoreCheck,
    ClaimResponse,
    AdminScoreUpdate,
)

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

# Static game build (the arcade frontend).
#
# `docs/` is the canonical build directory: `npm run build` writes docs/game.js
# into it and rollup's dev server uses it as its contentBase. Mounting it here
# means the game and the score API share an origin, so the arcade build needs
# neither CORS nor a hardcoded API host.
GAME_STATIC_DIR = os.getenv(
    "GAME_STATIC_DIR",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "docs",
    ),
)

class GameStaticFiles(StaticFiles):
    """StaticFiles that stops browsers serving a stale game after a deploy.

    Starlette sends only last-modified and etag, with no Cache-Control. Browsers
    then apply heuristic freshness and can keep a cached game.js for a long time
    without revalidating - so a deployed change silently fails to reach the
    arcade cabinet and anyone who has played before.

    index.html, game.js and game.config.js change on every deploy, so they are
    marked no-cache: the browser must revalidate, and the etag makes that a cheap
    304 when nothing changed. Assets keep a normal cache lifetime; they are large
    and effectively immutable.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)

        content_type = response.headers.get("content-type", "")
        revalidate = path.endswith((".html", ".js")) or content_type.startswith("text/html")

        response.headers["Cache-Control"] = (
            "no-cache" if revalidate else "public, max-age=86400"
        )

        return response


def _write_stdb_config() -> None:
    """Point the game client at the SpacetimeDB server named by the environment.

    Same job as the nginx image's entrypoint: the client loads stdb-config.js
    before its own config, so the address is a deploy setting, not baked into
    the bundle.
    """
    uri = os.getenv("STDB_URI", "").strip()
    if not uri or not os.path.isdir(GAME_STATIC_DIR):
        return
    db_name = os.getenv("STDB_DATABASE", "inkless-game").strip() or "inkless-game"
    try:
        with open(os.path.join(GAME_STATIC_DIR, "stdb-config.js"), "w") as f:
            f.write(f"window.INKLESS_STDB_URI = {json.dumps(uri)};\n")
            f.write(f"window.INKLESS_STDB_DATABASE = {json.dumps(db_name)};\n")
        logger.info(f"Game client will connect to {uri} ({db_name})")
    except OSError as e:
        logger.warning(f"Could not write stdb-config.js: {e}")


_write_stdb_config()

if os.path.isdir(GAME_STATIC_DIR):
    # html=True serves index.html for /game/, and Starlette redirects /game to
    # /game/ so the game's relative asset paths resolve inside /game/ rather
    # than against the site root.
    app.mount("/game", GameStaticFiles(directory=GAME_STATIC_DIR, html=True), name="game")
    # The same build is also served at /qrcode/ for the arcade cabinet. The bundle picks its
    # end-of-game flow from the URL it is loaded at: /game/ shows an on-screen sign-up form,
    # /qrcode/ shows a QR code that stays up until the score is scanned.
    app.mount("/qrcode", GameStaticFiles(directory=GAME_STATIC_DIR, html=True), name="qrcode")
    logger.info(f"Serving game build from {GAME_STATIC_DIR} at /game and /qrcode")
else:
    logger.warning(f"No game build at {GAME_STATIC_DIR}; /game will return 404")

def format_money(amount: int) -> str:
    """Format money display"""
    if amount >= 1000:
        return f"${amount/1000:.1f}k"
    return f"${amount}"

@app.get("/")
async def root():
    """API health check"""
    return {
        "message": "Inkless Game Score API",
        "status": "healthy",
        "game": "/game/",
        "arcade": "/qrcode/",
        "leaderboard": "/leaderboard",
    }


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve the game's favicon at the site root"""
    icon_path = os.path.join(GAME_STATIC_DIR, "favicon.ico")
    if os.path.isfile(icon_path):
        return FileResponse(icon_path)
    raise HTTPException(status_code=404, detail="Not found")

def _check_ingest_secret(provided: str | None) -> None:
    """Only the game server may create scores once INGEST_SECRET is configured.

    With the SpacetimeDB build the score is computed server-side and posted here by
    the module, so a client must not be able to POST its own. Unset keeps the
    legacy behaviour (anyone can submit) for the old client-side build.
    """
    expected = os.getenv("INGEST_SECRET")
    if not expected:
        return
    if not provided or not hmac.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Forbidden")


@app.post("/api/scores", response_model=ScoreResponse)
async def submit_score(score: ScoreSubmission, x_ingest_secret: str | None = Header(default=None)):
    """Submit a game score"""
    _check_ingest_secret(x_ingest_secret)
    try:
        logger.info(f"Attempting to submit score: {score}")
        
        await database.submit_score(
            score.session_id, 
            score.final_bill, 
            score.total_savings, 
            score.timestamp,
            score.source
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

@app.get("/api/scores/{session_id}/status")
async def score_status(session_id: str):
    """Lightweight poll target for the arcade cabinet: has this score been claimed yet?

    The cabinet leaves the QR code and claim button up until the score is claimed (or it
    times out), polling this rather than a fixed on-screen timer, so it doesn't take the QR
    code away before someone's had a chance to scan it.
    """
    try:
        row = await database.get_score(session_id)
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")

        return {"claimed": row["claimed_at"] is not None}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking score status: {e}")
        raise HTTPException(status_code=500, detail="Failed to check score status")

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
    * {
        box-sizing: border-box;
    }
    
    body { 
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; 
        max-width: 600px; 
        margin: 50px auto; 
        padding: 20px; 
        background: #000000; 
        color: #ffffff;
        line-height: 1.6;
    }
    
    .container { 
        background: #111111; 
        padding: 40px; 
        border-radius: 12px; 
        text-align: center; 
        border: 1px solid #333333;
    }
    
    h1 {
        color: #ffffff;
        font-weight: 700;
        margin-bottom: 20px;
        font-size: 2em;
    }
    
    .score { 
        font-size: 1.3em; 
        margin: 30px 0; 
        background: #222222; 
        padding: 25px; 
        border-radius: 8px; 
        border: 1px solid #333333;
        color: #ffffff;
        font-weight: 600;
    }
    
    .form { 
        margin: 30px 0; 
    }
    
    input[type="email"], input[type="text"] { 
        padding: 15px; 
        font-size: 16px; 
        width: 300px; 
        max-width: 100%; 
        border: 2px solid #333333; 
        border-radius: 6px; 
        margin: 8px 0; 
        background: #222222; 
        color: #ffffff;
        transition: border-color 0.2s ease;
    }
    
    input[type="email"]:focus, input[type="text"]:focus {
        outline: none;
        border-color: #FF6B35;
    }
    
    input::placeholder {
        color: #888888;
    }
    
    button { 
        padding: 15px 30px; 
        font-size: 16px; 
        background: #FF6B35; 
        color: #000000; 
        border: none; 
        border-radius: 6px; 
        cursor: pointer; 
        margin-top: 20px; 
        transition: all 0.2s ease; 
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    
    button:hover { 
        background: #e55a2b; 
        transform: translateY(-1px);
    }
    
    button:active {
        transform: translateY(0);
    }
    
    button:disabled { 
        background: #666666; 
        cursor: not-allowed; 
        transform: none;
    }
    
    .message { 
        margin: 20px 0; 
        padding: 20px; 
        border-radius: 8px; 
    }
    
    .success { 
        background: #0f2e0f; 
        color: #4CAF50; 
        border: 1px solid #4CAF50; 
    }
    
    .error { 
        background: #2e0f0f; 
        color: #FF6B6B; 
        border: 1px solid #FF6B6B; 
    }
    
    .high-score { 
        background: #2e2200; 
        color: #FFD700; 
        border: 1px solid #FFD700; 
        padding: 20px; 
        margin: 25px 0; 
        border-radius: 8px; 
        animation: pulse 2s infinite; 
        font-weight: 600;
    }
    
    .claimed { 
        color: #4CAF50; 
        font-weight: 600; 
        margin: 25px 0; 
    }
    
    .meta { 
        color: #888888; 
        font-size: 14px; 
        margin: 15px 0; 
    }
    
    .optional { 
        color: #888888; 
        font-size: 12px; 
    }
    
    .loading { 
        display: none; 
    }
    
    a {
        color: #FF6B35;
        text-decoration: none;
        font-weight: 500;
    }
    
    a:hover {
        text-decoration: underline;
    }
    
    @keyframes pulse { 
        0% { opacity: 1; } 
        50% { opacity: 0.7; } 
        100% { opacity: 1; } 
    }
    
    @media (max-width: 768px) {
        body {
            margin: 20px auto;
            padding: 15px;
        }
        
        .container {
            padding: 25px;
        }
        
        input[type="email"], input[type="text"] {
            width: 100%;
        }
        
        h1 {
            font-size: 1.6em;
        }
    }
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
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <title>Claim Your Score - Inkless Game</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    * {{
                        box-sizing: border-box;
                    }}
                    
                    body {{
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                        margin: 0;
                        padding: 20px;
                        background: #000000;
                        color: #ffffff;
                        min-height: 100vh;
                        line-height: 1.6;
                    }}
                    
                    .container {{
                        max-width: 600px;
                        margin: 0 auto;
                    }}
                    
                    h1 {{
                        text-align: center;
                        font-size: 2.5em;
                        font-weight: 700;
                        color: #ffffff;
                        margin: 0 0 30px 0;
                    }}
                    
                    .score {{
                        background: #111111;
                        border: 1px solid #333333;
                        border-radius: 12px;
                        padding: 30px;
                        margin-bottom: 20px;
                        text-align: center;
                        font-size: 1.3em;
                    }}
                    
                    .score strong {{
                        color: #FF6B35;
                        display: inline-block;
                        min-width: 140px;
                    }}
                    
                    .savings-value {{
                        color: #4CAF50;
                        font-weight: 700;
                        font-size: 1.2em;
                    }}
                    
                    .bill-value {{
                        color: #FF6B6B;
                        font-weight: 700;
                    }}
                    
                    .meta {{
                        text-align: center;
                        color: #888888;
                        font-size: 0.9em;
                        margin-bottom: 20px;
                    }}
                    
                    .high-score {{
                        background: linear-gradient(135deg, #FFD700 0%, #FFA500 100%);
                        color: #000000;
                        padding: 20px;
                        border-radius: 12px;
                        text-align: center;
                        margin: 20px 0;
                        font-weight: 700;
                        font-size: 1.2em;
                        border: 2px solid #FFD700;
                        animation: pulse 2s infinite;
                    }}
                    
                    @keyframes pulse {{
                        0%, 100% {{ transform: scale(1); }}
                        50% {{ transform: scale(1.02); }}
                    }}
                    
                    .form {{
                        background: #111111;
                        border: 1px solid #333333;
                        border-radius: 12px;
                        padding: 30px;
                        margin: 20px 0;
                    }}
                    
                    .form p {{
                        text-align: center;
                        font-size: 1.1em;
                        margin: 0 0 25px 0;
                        color: #FF6B35;
                    }}
                    
                    .form input {{
                        width: 100%;
                        padding: 14px;
                        margin: 10px 0;
                        background: #222222;
                        border: 1px solid #444444;
                        border-radius: 6px;
                        color: #ffffff;
                        font-size: 1em;
                        transition: border-color 0.2s ease;
                    }}
                    
                    .form input:focus {{
                        outline: none;
                        border-color: #FF6B35;
                    }}
                    
                    .form input::placeholder {{
                        color: #666666;
                    }}
                    
                    .form small {{
                        display: block;
                        color: #888888;
                        font-size: 0.85em;
                        margin: -5px 0 10px 5px;
                    }}
                    
                    .form small.optional {{
                        color: #666666;
                    }}
                    
                    .consent-label {{
                        display: flex;
                        align-items: flex-start;
                        gap: 12px;
                        cursor: pointer;
                    }}

                    .consent-label input[type="checkbox"] {{
                        width: 22px;
                        height: 22px;
                        min-width: 22px;
                        margin: 2px 0 0 0;
                        padding: 0;
                        accent-color: #FF6B35;
                        cursor: pointer;
                    }}

                    .consent-label a {{
                        color: #FF6B35;
                        word-break: break-all;
                    }}

                    .consent-text {{
                        background: #1a1a1a;
                        border-left: 3px solid #FF6B35;
                        padding: 15px;
                        margin: 20px 0;
                        font-size: 0.95em;
                        color: #cccccc;
                        line-height: 1.6;
                        border-radius: 4px;
                    }}
                    
                    button {{
                        width: 100%;
                        padding: 16px;
                        background: #FF6B35;
                        border: none;
                        border-radius: 6px;
                        color: #000000;
                        font-size: 1.1em;
                        font-weight: 600;
                        cursor: pointer;
                        transition: all 0.2s ease;
                        text-transform: uppercase;
                        letter-spacing: 0.5px;
                        margin-top: 10px;
                    }}
                    
                    button:hover {{
                        background: #e55a2b;
                        transform: translateY(-1px);
                    }}
                    
                    button:active {{
                        transform: translateY(0);
                    }}
                    
                    button:disabled {{
                        background: #444444;
                        cursor: not-allowed;
                        transform: none;
                    }}
                    
                    .loading {{
                        display: none;
                    }}
                    
                    .success {{
                        background: #1a4d2e;
                        border: 1px solid #4CAF50;
                        color: #4CAF50;
                        padding: 20px;
                        border-radius: 12px;
                        text-align: center;
                        margin: 20px 0;
                        font-size: 1.2em;
                        font-weight: 600;
                    }}
                    
                    .error {{
                        background: #1a0000;
                        border: 1px solid #FF6B6B;
                        color: #FF6B6B;
                        padding: 20px;
                        border-radius: 12px;
                        text-align: center;
                        margin: 20px 0;
                        font-weight: 600;
                    }}
                    
                    .thank-you {{
                        background: #111111;
                        border: 1px solid #333333;
                        border-radius: 12px;
                        padding: 30px;
                        margin-top: 20px;
                        border-left: 4px solid #FF6B35;
                    }}
                    
                    .thank-you h2 {{
                        margin: 0 0 15px 0;
                        font-size: 1.4em;
                        color: #FF6B35;
                    }}
                    
                    .thank-you p {{
                        color: #cccccc;
                        margin: 10px 0;
                        line-height: 1.6;
                    }}
                    
                    .thank-you a {{
                        display: inline-block;
                        background: #FF6B35;
                        color: #000000;
                        padding: 14px 28px;
                        text-decoration: none;
                        border-radius: 6px;
                        font-weight: 600;
                        margin-top: 15px;
                        transition: all 0.2s ease;
                        text-transform: uppercase;
                        letter-spacing: 0.5px;
                        font-size: 0.95em;
                    }}
                    
                    .thank-you a:hover {{
                        background: #e55a2b;
                        transform: translateY(-1px);
                    }}
                    
                    .privacy-note {{
                        text-align: center;
                        color: #666666;
                        font-size: 0.85em;
                        margin-top: 15px;
                    }}
                    
                    @media (max-width: 768px) {{
                        body {{
                            padding: 15px;
                        }}
                        
                        h1 {{
                            font-size: 2em;
                        }}
                        
                        .score {{
                            font-size: 1.1em;
                            padding: 20px;
                        }}
                        
                        .form {{
                            padding: 20px;
                        }}
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>🎮 Claim Your Score!</h1>
                    
                    <div class="score">
                        <div><strong>Total Saved:</strong> <span class="savings-value">{total_savings_formatted}</span></div>
                        <div style="margin-top: 10px;"><strong>Final Bill:</strong> <span class="bill-value">{final_bill_formatted}</span></div>
                    </div>
                    
                    <div class="meta">Game played on: {row['timestamp'].strftime('%B %d, %Y at %I:%M %p')}</div>
                    
                    <div id="highScoreCheck"></div>
                    
                    <div class="form" id="claimSection">
                        <p><strong>Join the leaderboard!</strong></p>
                        <form id="claimForm">
                            <input type="email" id="email" placeholder="your@email.com" required>
                            
                            <input type="text" id="nickname" placeholder="Your name" maxlength="50" required>
                            <small>Your name will appear on the leaderboard</small>
                            
                            <input type="text" id="company" placeholder="Company name (optional)" maxlength="100">
                            
                            <input type="text" id="title" placeholder="Job title (optional)" maxlength="100">
                            <small class="optional">Company and title fields are optional</small>
                            
                            <div class="consent-text">
                                <label class="consent-label">
                                    <input type="checkbox" id="privacyConsent" name="privacyConsent" required>
                                    <span>By submitting your information, you agree to Aiven's privacy policy: <a href="https://aiven.io/privacy" target="_blank" rel="noopener noreferrer">https://aiven.io/privacy</a></span>
                                </label>
                            </div>

                            <button type="submit" id="submitBtn">
                                <span class="loading">⏳ Claiming...</span>
                                <span class="normal">Claim Score</span>
                            </button>
                        </form>
                        
                        <div class="privacy-note">
                            📧 Your email is used for score verification and product updates.
                        </div>
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
                    if (!document.getElementById('privacyConsent').checked) {{
                        document.getElementById('message').innerHTML = '<div class="error">❌ Please agree to the privacy policy to continue.</div>';
                        return;
                    }}
                    const email = document.getElementById('email').value;
                    const nickname = document.getElementById('nickname').value;
                    const company = document.getElementById('company').value || null;
                    const title = document.getElementById('title').value || null;
                    const messageDiv = document.getElementById('message');
                    const submitBtn = document.getElementById('submitBtn');
                    
                    // Show loading state
                    submitBtn.disabled = true;
                    submitBtn.querySelector('.loading').style.display = 'inline';
                    submitBtn.querySelector('.normal').style.display = 'none';
                    
                    try {{
                        // First, submit to your backend for leaderboard
                        const response = await fetch('/api/claim/{session_id}', {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json' }},
                            body: JSON.stringify({{ 
                                email: email, 
                                nickname: nickname, 
                                company: company 
                            }})
                        }});
                        
                        const data = await response.json();
                        
                        if (response.ok) {{
                            const nameParts = nickname.trim().split(' ');
                            const firstName = nameParts[0] || nickname;
                            const lastName = nameParts.slice(1).join(' ') || nameParts[0];
                            
                            // Submit to Salesforce via hidden form (bypasses CORS)
                            const iframe = document.createElement('iframe');
                            iframe.name = 'salesforce-target';
                            iframe.style.display = 'none';
                            document.body.appendChild(iframe);
                            
                            const form = document.createElement('form');
                            form.target = 'salesforce-target';
                            form.method = 'POST';
                            form.action = 'https://go.aiven.io/l/890043/2025-09-25/wqyqc';
                            
                            const fields = {{
                                email: email,
                                first_name: firstName,
                                last_name: lastName,
                                company: company || 'Not Provided',
                                title: title || '',
                                source: 'Inkless Game - Disk Invaders'
                            }};
                            
                            Object.entries(fields).forEach(([key, value]) => {{
                                const input = document.createElement('input');
                                input.type = 'hidden';
                                input.name = key;
                                input.value = value;
                                form.appendChild(input);
                            }});
                            
                            document.body.appendChild(form);
                            form.submit();
                            
                            // Cleanup after 2 seconds
                            setTimeout(() => {{
                                if (document.body.contains(form)) document.body.removeChild(form);
                                if (document.body.contains(iframe)) document.body.removeChild(iframe);
                            }}, 2000);
                            
                            // Show success message
                            let message = '<div class="success">✅ Score claimed successfully!</div>';
                            if (data.is_high_score) {{
                                message += `<div class="high-score">🎉 High Score Confirmed! You ranked #${{data.rank}}!</div>`;
                            }}
                            
                            message += `
                            <div class="thank-you">
                                <h2>✨ Thank you for playing!</h2>
                                <p>
                                    You've been added to our Inkless updates list. We'll keep you informed 
                                    about new features and product releases.
                                </p>
                                <a href="https://aiven.io/inkless" target="_blank">
                                    Learn More About Inkless →
                                </a>
                            </div>`;
                            
                            messageDiv.innerHTML = message;
                            document.getElementById('claimSection').style.display = 'none';
                        }} else {{
                            let errorMessage = 'An error occurred';
                            
                            if (data.detail) {{
                                if (typeof data.detail === 'string') {{
                                    errorMessage = data.detail;
                                }} else if (Array.isArray(data.detail)) {{
                                    const errors = data.detail.map(err => {{
                                        const field = err.loc ? err.loc.join('.') : 'unknown';
                                        return `${{field}}: ${{err.msg}}`;
                                    }});
                                    errorMessage = errors.join('<br>');
                                }} else if (typeof data.detail === 'object') {{
                                    errorMessage = JSON.stringify(data.detail);
                                }}
                            }}
                            
                            messageDiv.innerHTML = '<div class="error">❌ ' + errorMessage + '</div>';
                            submitBtn.disabled = false;
                            submitBtn.querySelector('.loading').style.display = 'none';
                            submitBtn.querySelector('.normal').style.display = 'inline';
                        }}
                    }} catch (err) {{
                        console.error('Error:', err);
                        messageDiv.innerHTML = '<div class="error">❌ Error claiming score. Please try again.</div>';
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
async def get_leaderboard(limit: int = 10, source: str = "all"):
    """Get the leaderboard showing only claimed scores with nicknames.

    `source` filters by where the score was played: "arcade", "web" or "all".
    """
    if source not in ("all", "arcade", "web"):
        raise HTTPException(status_code=400, detail="source must be all, arcade or web")
    try:
        logger.info(f"Getting leaderboard (source={source})")
        all_scores = await database.get_leaderboard(source=None if source == "all" else source)
        
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
                "source": score.get('source', 'arcade'),
                "claimed": True  # All these are claimed by definition
            }
            
            # Add company if it exists and is not empty
            company = score.get('company')
            if company:
                entry["company"] = company
            
            leaderboard.append(entry)
        
        logger.info(f"Returning {len(leaderboard)} claimed scores for leaderboard")
        return {"leaderboard": leaderboard, "ranked_by": "total_savings", "source": source}
        
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

# ---------------------------------------------------------------------------
# Admin console authentication
#
# A single shared password (ADMIN_KEY) exchanged for an HMAC-signed, HttpOnly
# session cookie, so the secret stops travelling in URLs and referrers the way
# ?admin_key= does. The cookie is signed with ADMIN_KEY itself, which means
# rotating the password invalidates every live session.
#
# If ADMIN_KEY is unset the console fails closed rather than opening up.
# ---------------------------------------------------------------------------

ADMIN_SESSION_COOKIE = "inkless_admin"
ADMIN_SESSION_TTL_SECONDS = 12 * 60 * 60


def _admin_key() -> str:
    return os.getenv("ADMIN_KEY", "")


def _sign_admin_session(expires_at: int) -> str:
    mac = hmac.new(
        _admin_key().encode(), str(expires_at).encode(), hashlib.sha256
    ).hexdigest()
    return f"{expires_at}.{mac}"


def _admin_session_valid(token: str) -> bool:
    key = _admin_key()
    if not key or not token:
        return False

    expires_raw, _, mac = token.partition(".")
    if not mac:
        return False

    try:
        expires_at = int(expires_raw)
    except ValueError:
        return False

    if expires_at < time.time():
        return False

    expected = hmac.new(key.encode(), expires_raw.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, mac)


def require_admin(request: Request):
    """Dependency guarding every admin data route"""
    if not _admin_session_valid(request.cookies.get(ADMIN_SESSION_COOKIE, "")):
        raise HTTPException(status_code=401, detail="Not authenticated")


@app.post("/admin/login")
async def admin_login(request: Request, response: Response, password: str = Form(...)):
    """Exchange the shared password for a session cookie"""
    key = _admin_key()

    if not key:
        logger.error("Admin login attempted but ADMIN_KEY is not configured")
        raise HTTPException(status_code=503, detail="Admin console is not configured")

    if not hmac.compare_digest(password, key):
        # Small fixed delay to take the edge off password guessing
        await asyncio.sleep(0.5)
        logger.warning("Failed admin login attempt")
        raise HTTPException(status_code=401, detail="Incorrect password")

    expires_at = int(time.time()) + ADMIN_SESSION_TTL_SECONDS
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        _sign_admin_session(expires_at),
        max_age=ADMIN_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    logger.info("Admin logged in")
    return {"success": True}


@app.post("/admin/logout")
async def admin_logout(response: Response):
    """Clear the admin session cookie"""
    response.delete_cookie(ADMIN_SESSION_COOKIE)
    return {"success": True}


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    """Serve the admin console.

    Always served; the page itself shows a password prompt until
    /admin/api/scores stops answering 401.
    """
    admin_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin.html")

    try:
        with open(admin_path, "r", encoding="utf-8") as handle:
            return HTMLResponse(content=handle.read())
    except FileNotFoundError:
        logger.error(f"Admin template missing at {admin_path}")
        raise HTTPException(status_code=500, detail="Admin console template missing")


@app.get("/admin/api/scores", dependencies=[Depends(require_admin)])
async def admin_list_scores():
    """Every score, all columns, ranked by savings"""
    try:
        rows = await database.get_all_scores_admin()

        scores = []
        for index, row in enumerate(rows):
            scores.append(
                {
                    "rank": index + 1,
                    "session_id": row["session_id"],
                    "nickname": row["nickname"],
                    "company": row["company"],
                    "email": row["email"],
                    "total_savings": row["total_savings"],
                    "final_bill": row["final_bill"],
                    "claimed": bool(row["email"]),
                    "source": row["source"],
                    "played_at": row["timestamp"].isoformat() if row["timestamp"] else None,
                    "claimed_at": row["claimed_at"].isoformat() if row["claimed_at"] else None,
                }
            )

        return {"total": len(scores), "scores": scores}

    except Exception as e:
        logger.error(f"Error listing scores for admin: {e}")
        raise HTTPException(status_code=500, detail="Failed to list scores")


@app.patch("/admin/api/scores/{session_id}", dependencies=[Depends(require_admin)])
async def admin_update_score(session_id: str, update: AdminScoreUpdate):
    """Edit a score's nickname, company or email"""
    try:
        updated = await database.admin_update_score(
            session_id,
            update.nickname,
            update.company,
            str(update.email) if update.email else None,
        )

        if not updated:
            raise HTTPException(status_code=404, detail="Score not found")

        logger.info(f"Admin updated score {session_id}")
        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating score {session_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update score")


@app.delete("/admin/api/scores/{session_id}", dependencies=[Depends(require_admin)])
async def admin_delete_score(session_id: str):
    """Delete a score - for clearing out test runs and junk entries"""
    try:
        deleted = await database.admin_delete_score(session_id)

        if not deleted:
            raise HTTPException(status_code=404, detail="Score not found")

        logger.info(f"Admin deleted score {session_id}")
        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting score {session_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete score")


@app.get("/admin/export.csv", dependencies=[Depends(require_admin)])
async def admin_export_csv(claimed_only: bool = False):
    """Download every score as CSV, for spreadsheets and follow-up"""
    try:
        rows = await database.get_all_scores_admin()

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "rank",
                "nickname",
                "company",
                "email",
                "total_savings",
                "final_bill",
                "claimed",
                "source",
                "played_at",
                "claimed_at",
                "session_id",
            ]
        )

        exported = 0
        for index, row in enumerate(rows):
            if claimed_only and not row["email"]:
                continue

            writer.writerow(
                [
                    index + 1,
                    row["nickname"] or "",
                    row["company"] or "",
                    row["email"] or "",
                    row["total_savings"],
                    row["final_bill"],
                    "yes" if row["email"] else "no",
                    row["source"],
                    row["timestamp"].isoformat() if row["timestamp"] else "",
                    row["claimed_at"].isoformat() if row["claimed_at"] else "",
                    row["session_id"],
                ]
            )
            exported += 1

        filename = f"inkless-scores-{datetime.utcnow().strftime('%Y-%m-%d')}.csv"
        logger.info(f"Admin exported {exported} scores as CSV")

        return Response(
            content=buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as e:
        logger.error(f"Error exporting scores: {e}")
        raise HTTPException(status_code=500, detail="Failed to export scores")


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