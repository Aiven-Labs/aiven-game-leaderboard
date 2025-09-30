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
        if len(v) > 50:  # Changed from 20 to 50
            raise ValueError('Nickname must be 50 characters or less')
        
        # Allow letters, numbers, spaces, and basic symbols
        if not re.match(r'^[a-zA-Z0-9\s\-_.!]+$', v):
            raise ValueError('Nickname contains invalid characters')
            
        # Prevent all spaces/symbols
        if not re.search(r'[a-zA-Z0-9]', v):
            raise ValueError('Nickname must contain at least one letter or number')
            
        return v
    
    @validator('company')
    def validate_company(cls, v):
        if v is None:
            return v
        
        # Clean and validate company
        v = v.strip()
        
        if len(v) == 0:
            return None  # Empty string becomes None
            
        if len(v) > 100:  # Increased from 50 to 100
            raise ValueError('Company name must be 100 characters or less')
        
        # Allow letters, numbers, spaces, and common company symbols
        if not re.match(r'^[a-zA-Z0-9\s\-_.&,()]+$', v):
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