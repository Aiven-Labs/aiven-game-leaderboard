from pydantic import BaseModel, EmailStr, validator
from datetime import datetime
from typing import Optional
import re

class ScoreSubmission(BaseModel):
    session_id: str
    final_bill: int
    total_savings: int
    timestamp: str

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
    is_high_score: bool
    rank: Optional[int] = None
    total_scores: Optional[int] = None

class ClaimResponse(BaseModel):
    success: bool
    message: str
    is_high_score: Optional[bool] = None
    rank: Optional[int] = None