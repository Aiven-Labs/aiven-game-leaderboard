from pydantic import BaseModel, EmailStr, validator
from datetime import datetime
from typing import Literal, Optional
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

    @validator('name')
    def name_ok(cls, v):
        v = v.strip()
        if not 1 <= len(v) <= 100:
            raise ValueError('Name must be 1-100 characters')
        return v
