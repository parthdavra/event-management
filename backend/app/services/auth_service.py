from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User


def register_user(
    db: Session, username: str, email: str, password: str
) -> Tuple[Optional[User], Optional[str]]:
    if db.query(User).filter((User.username == username) | (User.email == email)).first():
        return None, "Username or email already exists."
    try:
        user = User(
            username=username,
            email=email,
            password_hash=hash_password(password),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user, None
    except Exception as exc:
        db.rollback()
        return None, str(exc)


def login_user(
    db: Session, username: str, password: str
) -> Tuple[Optional[str], Optional[str]]:
    """Authenticate and return a JWT on success."""
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        return None, "Invalid username or password."
    token = create_access_token(data={"sub": str(user.id)})
    return token, None


def change_password(
    db: Session, user_id: int, old_password: str, new_password: str
) -> Tuple[bool, Optional[str]]:
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not verify_password(old_password, user.password_hash):
        return False, "Current password is incorrect."
    try:
        user.password_hash = hash_password(new_password)
        db.commit()
        return True, None
    except Exception as exc:
        db.rollback()
        return False, str(exc)


def get_user_profile(db: Session, user_id: int) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()
