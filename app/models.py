from pydantic import BaseModel, EmailStr, validator
from datetime import datetime
from typing import List, Literal, Optional
import json
import re

class ScoreSubmission(BaseModel):
    session_id: str
    final_bill: int
    total_savings: int
    timestamp: str
    # Older clients do not send this; every score they produced came from the arcade
    source: Literal['arcade', 'web'] = 'arcade'
    # Which event this was played at (a slug from the events table), if any
    event: Optional[str] = None

    @validator('event')
    def clean_event(cls, v):
        if v is None:
            return None
        v = v.strip().lower()
        if not v:
            return None
        if not re.match(r'^[a-z0-9][a-z0-9-]{0,39}$', v):
            raise ValueError('invalid event')
        return v

class ClaimData(BaseModel):
    email: EmailStr
    nickname: str
    company: Optional[str] = None
    
    @validator('nickname')
    def validate_nickname(cls, v):
        # Clean and validate nickname
        v = v.strip()
        
        if len(v) < 2:
            raise ValueError('Nickname must be at least 2 characters long')
        if len(v) > 50:
            raise ValueError('Nickname must be 50 characters or less')
        
        # Allow Unicode letters, numbers, spaces, hyphens, apostrophes, and periods
        # This covers names like: José, O'Brien, Jean-Luc, Dr. Smith, Müller, 中村
        if not re.match(r"^[\w\s\-'.]+$", v, re.UNICODE):
            raise ValueError('Nickname can only contain letters, numbers, spaces, hyphens, apostrophes, and periods')
            
        # Prevent all spaces/symbols - must have at least one letter or number
        if not re.search(r'\w', v, re.UNICODE):
            raise ValueError('Nickname must contain at least one letter or number')
            
        return v
    
    @validator('company')
    def validate_company(cls, v):
        if v is None:
            return v
        
        v = v.strip()
        
        if len(v) == 0:
            return None
            
        if len(v) > 100:
            raise ValueError('Company name must be 100 characters or less')
        
        # Allow Unicode letters, numbers, spaces, and common company symbols
        if not re.match(r'^[\w\s\-_.&,()]+$', v, re.UNICODE):
            raise ValueError('Company name contains invalid characters')
            
        return v

class AdminScoreUpdate(BaseModel):
    """Fields an admin may edit on an existing score.

    All three are optional and nullable: clearing the email un-claims a score,
    which is how a junk entry gets taken off the leaderboard without deleting
    the play itself.
    """
    nickname: Optional[str] = None
    company: Optional[str] = None
    email: Optional[EmailStr] = None

    @validator('nickname')
    def validate_nickname(cls, v):
        if v is None:
            return v

        v = v.strip()

        if len(v) == 0:
            return None
        if len(v) > 50:
            raise ValueError('Nickname must be 50 characters or less')
        if not re.match(r"^[\w\s\-'.]+$", v, re.UNICODE):
            raise ValueError('Nickname can only contain letters, numbers, spaces, hyphens, apostrophes, and periods')
        if not re.search(r'\w', v, re.UNICODE):
            raise ValueError('Nickname must contain at least one letter or number')

        return v

    @validator('company')
    def validate_company(cls, v):
        if v is None:
            return v

        v = v.strip()

        if len(v) == 0:
            return None
        if len(v) > 100:
            raise ValueError('Company name must be 100 characters or less')
        if not re.match(r'^[\w\s\-_.&,()]+$', v, re.UNICODE):
            raise ValueError('Company name contains invalid characters')

        return v


class ScoreResponse(BaseModel):
    success: bool
    session_id: str
    message: str

class HighScoreCheck(BaseModel):
    game: Optional[str] = None
    event: Optional[str] = None
    is_high_score: bool
    rank: Optional[int] = None
    total_scores: Optional[int] = None

class ClaimResponse(BaseModel):
    success: bool
    message: str
    is_high_score: Optional[bool] = None
    rank: Optional[int] = None

class EventUpsert(BaseModel):
    """Create or edit an event. The window is optional: with no dates the event is always open."""
    name: str
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    # Slugs of the games played at this event; each gets its own board, links and QR codes.
    # Leave it out to keep an existing event's games unchanged.
    games: Optional[List[str]] = None

    @validator('name')
    def name_ok(cls, v):
        v = v.strip()
        if not 1 <= len(v) <= 100:
            raise ValueError('Name must be 1-100 characters')
        return v

    @validator('games')
    def games_ok(cls, v):
        if v is None:
            return v
        if len(v) > 50:
            raise ValueError('An event can have at most 50 games')
        for slug in v:
            if not SLUG_RE.match(slug):
                raise ValueError(f'invalid game slug: {slug!r}')
        return list(dict.fromkeys(v))


def _url_template_ok(v):
    """'' or None clears; otherwise an http(s) URL, optionally containing {event}"""
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    if len(v) > 500:
        raise ValueError('URLs must be 500 characters or fewer')
    if not re.match(r'^https?://[^\s/]+', v):
        raise ValueError('must be an http:// or https:// URL')
    if '{' in v.replace('{event}', '') or '}' in v.replace('{event}', ''):
        raise ValueError('the only placeholder allowed is {event}')
    return v


SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,39}$')


class GameUpsert(BaseModel):
    """A game's identity and how its scores rank and display."""
    name: str
    score_label: str = 'Score'
    score_format: Literal['int', 'money', 'time_ms'] = 'int'
    # 'desc': higher is better. 'asc': lower is better (times, move counts)
    sort_order: Literal['desc', 'asc'] = 'desc'
    secondary_label: Optional[str] = None
    secondary_format: Optional[Literal['int', 'money', 'time_ms']] = None
    # Shown on the claim page for a high score; {score} is replaced with the formatted score
    highscore_text: str = 'Your score: {score}'
    # Where players start the game. {event} is replaced with the event slug; without it,
    # ?event=<slug> is appended. Leave it out to keep the current value; send "" to clear it.
    play_url: Optional[str] = None
    # The arcade cabinet's start page for this game, templated the same way
    cabinet_url: Optional[str] = None

    @validator('play_url', 'cabinet_url')
    def urls_ok(cls, v):
        return _url_template_ok(v)

    @validator('name')
    def name_ok(cls, v):
        v = v.strip()
        if not 1 <= len(v) <= 100:
            raise ValueError('Name must be 1-100 characters')
        return v

    @validator('score_label', 'secondary_label')
    def label_ok(cls, v):
        if v is None:
            return v
        v = v.strip()
        if len(v) > 40:
            raise ValueError('Labels must be 40 characters or fewer')
        return v or None

    @validator('highscore_text')
    def text_ok(cls, v):
        v = v.strip()
        if not 1 <= len(v) <= 200:
            raise ValueError('High-score text must be 1-200 characters')
        return v


class ScoreV1(BaseModel):
    """A finished game, posted by a game's server (never a browser) with that game's token."""
    game: str
    # Also the player's claim id: whoever holds it can attach a nickname and email to this score
    session_id: str
    score: int
    secondary: Optional[int] = None
    # Anything else worth keeping (level reached, duration...); small JSON object
    details: Optional[dict] = None
    event: Optional[str] = None
    source: Literal['arcade', 'web', 'other'] = 'web'

    @validator('game')
    def game_ok(cls, v):
        v = v.strip().lower()
        if not SLUG_RE.match(v):
            raise ValueError('invalid game slug')
        return v

    @validator('session_id')
    def session_ok(cls, v):
        if not re.match(r'^[A-Za-z0-9_-]{8,64}$', v):
            raise ValueError('session_id must be 8-64 characters: letters, numbers, - and _')
        return v

    @validator('score', 'secondary')
    def score_range(cls, v):
        if v is None:
            return v
        if not 0 <= v <= 9_007_199_254_740_991:
            raise ValueError('scores must be whole numbers from 0 to 2^53')
        return v

    @validator('event')
    def event_ok(cls, v):
        if v is None:
            return None
        v = v.strip().lower()
        if not v:
            return None
        if not SLUG_RE.match(v):
            raise ValueError('invalid event')
        return v

    @validator('details')
    def details_small(cls, v):
        if v is not None and len(json.dumps(v)) > 2000:
            raise ValueError('details must be under 2000 characters of JSON')
        return v


class TokenCreate(BaseModel):
    name: str
    # 'game': submit scores for one game. 'admin': use the management API
    kind: Literal['game', 'admin']
    game: Optional[str] = None

    @validator('name')
    def name_ok(cls, v):
        v = v.strip()
        if not 1 <= len(v) <= 100:
            raise ValueError('Name must be 1-100 characters')
        return v

    @validator('game')
    def game_ok(cls, v, values):
        if values.get('kind') == 'game':
            if not v or not SLUG_RE.match(v):
                raise ValueError('a game token needs a game slug')
            return v
        return None
