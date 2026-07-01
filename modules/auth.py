import bcrypt
import streamlit as st
from .database import SessionLocal, User


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def register_user(username: str, email: str, password: str):
    db = SessionLocal()
    try:
        if db.query(User).filter(
            (User.username == username) | (User.email == email)
        ).first():
            return None, "Username or email already exists."
        user = User(
            username=username,
            email=email,
            password_hash=hash_password(password),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return {"id": user.id, "username": user.username, "email": user.email}, None
    except Exception as e:
        db.rollback()
        return None, str(e)
    finally:
        db.close()


def login_user(username: str, password: str):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user or not verify_password(password, user.password_hash):
            return None, "Invalid username or password."
        return {"id": user.id, "username": user.username, "email": user.email}, None
    finally:
        db.close()


def change_password(user_id: int, old_password: str, new_password: str):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user or not verify_password(old_password, user.password_hash):
            return False, "Current password is incorrect."
        user.password_hash = hash_password(new_password)
        db.commit()
        return True, None
    except Exception as e:
        db.rollback()
        return False, str(e)
    finally:
        db.close()


def get_user_profile(user_id: int):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return None
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "created_at": user.created_at,
        }
    finally:
        db.close()


def is_authenticated() -> bool:
    return bool(st.session_state.get("user_id"))


def logout():
    for key in ["user_id", "username"]:
        st.session_state.pop(key, None)


def require_auth():
    if not is_authenticated():
        st.warning("Please log in to access this page.")
        if st.button("Go to Login", type="primary"):
            st.switch_page("app.py")
        st.stop()


def show_sidebar():
    with st.sidebar:
        st.markdown(f"### 👤 {st.session_state.get('username', '')}")
        st.markdown("---")
        st.page_link("app.py", label="Home", icon="🏠")
        st.page_link("pages/1_Events.py", label="Events", icon="📅")
        st.page_link("pages/2_Chat.py", label="Chat", icon="💬")
        st.page_link("pages/3_AI_Assistant.py", label="AI Assistant", icon="🤖")
        st.page_link("pages/4_Data_Indexing.py", label="Data Indexing", icon="🗂️")
        st.page_link("pages/5_Profile.py", label="Profile", icon="⚙️")
        st.markdown("---")
        if st.button("🚪 Logout", use_container_width=True):
            logout()
            st.switch_page("app.py")
