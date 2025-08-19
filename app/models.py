from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Optional

class ScoreSubmission(BaseModel):
    session_id: str
    final_bill: int
    total_savings: int
    timestamp: str

class EmailClaim(BaseModel):
    email: EmailStr

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