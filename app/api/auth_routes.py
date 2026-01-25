from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.api.schemas import (
    LoginRequest, SignupRequest, AuthResponse,
    RefreshRequest, RefreshResponse
)
from app.services.auth_service import (
    authenticate_user, create_user, create_access_token,
    create_refresh_token, verify_refresh_token, revoke_refresh_token
)
from app.db.models import User

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=AuthResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    """Login with username and password"""
    user = authenticate_user(db, request.username, request.password)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )
    
    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(db, user.id)
    
    return AuthResponse(
        accessToken=access_token,
        refreshToken=refresh_token,
        message="Login successful"
    )


@router.post("/signup", response_model=AuthResponse)
def signup(request: SignupRequest, db: Session = Depends(get_db)):
    """Create a new user account"""
    # Check if username exists
    existing_user = db.query(User).filter(User.username == request.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists"
        )
    
    # Create user
    user = create_user(db, request.username, request.password)
    
    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(db, user.id)
    
    return AuthResponse(
        accessToken=access_token,
        refreshToken=refresh_token,
        message="Account created successfully"
    )


@router.post("/refresh", response_model=RefreshResponse)
def refresh_token(request: RefreshRequest, db: Session = Depends(get_db)):
    """Get a new access token using refresh token"""
    user_id = verify_refresh_token(db, request.refreshToken)
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token"
        )
    
    new_access_token = create_access_token(user_id)
    
    return RefreshResponse(accessToken=new_access_token)


@router.post("/logout")
def logout(request: RefreshRequest, db: Session = Depends(get_db)):
    """Logout and revoke refresh token"""
    revoke_refresh_token(db, request.refreshToken)
    return {"success": True, "message": "Logged out successfully"}
