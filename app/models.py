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
    
    @validator('nickname')
    def validate_nickname(cls, v):
        # Clean and validate nickname
        v = v.strip()
        
        if len(v) < 2:
            raise ValueError('Nickname must be at least 2 characters long')
        if len(v) > 20:
            raise ValueError('Nickname must be 20 characters or less')
        
        # Allow letters, numbers, spaces, and basic symbols
        if not re.match(r'^[a-zA-Z0-9\s\-_.!]+$', v):
            raise ValueError('Nickname contains invalid characters')
            
        # Prevent all spaces/symbols
        if not re.search(r'[a-zA-Z0-9]', v):
            raise ValueError('Nickname must contain at least one letter or number')
            
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