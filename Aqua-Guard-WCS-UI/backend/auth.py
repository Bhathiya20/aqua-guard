"""Server-side authentication, sessions, CSRF protection, roles, audit log.

Design
------
* Login verifies a salted password hash and creates a random 256-bit session token.
  Only sha256(token) is stored server-side; the browser gets the token in an
  HttpOnly, SameSite cookie.  Logout deletes the row (real revocation).
* Sessions expire after an idle period and an absolute lifetime.
* State-changing requests need the per-session CSRF token in X-CSRF-Token
  (double protection on top of SameSite).
* Roles are enforced on the server by @require_role - the React UI is only a
  convenience.
* Repeated failures lock the account temporarily; every security-relevant action
  is written to audit_log.
"""
import hashlib
import hmac
import secrets
import time
from functools import wraps

from flask import current_app, g, jsonify, request

from database import db_session, get_db

COOKIE_NAME = "aquaguard_session"
PUBLIC_ENDPOINTS = {"health", "login", "static"}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip() -> str:
    return request.remote_addr or ""


def audit(conn, action: str, detail: str = "", user=None, email: str | None = None):
    uid = user["id"] if user is not None else None
    em = email if email is not None else (user["email"] if user is not None else None)
    conn.execute("INSERT INTO audit_log (user_id,email,action,detail,ip) VALUES (?,?,?,?,?)",
                 (uid, em, action, detail[:500], client_ip()))


def create_session(conn, user_id: int) -> tuple[str, str, float]:
    cfg = current_app.config["AQ"]
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    now = time.time()
    exp = now + cfg.session_absolute_hours * 3600
    conn.execute("INSERT INTO sessions (token_hash,user_id,csrf_token,created_at,last_seen,expires_at,ip,user_agent) VALUES (?,?,?,?,?,?,?,?)",
                 (_hash(token), user_id, csrf, now, now, exp, client_ip(), (request.headers.get("User-Agent") or "")[:200]))
    # opportunistic cleanup
    conn.execute("DELETE FROM sessions WHERE expires_at < ? OR last_seen < ?", (now, now - cfg.session_idle_minutes * 60))
    return token, csrf, exp


def set_cookie(resp, token: str, expires: float):
    cfg = current_app.config["AQ"]
    resp.set_cookie(COOKIE_NAME, token, max_age=int(expires - time.time()), httponly=True,
                    secure=cfg.cookie_secure, samesite=cfg.cookie_samesite, path="/")


def clear_cookie(resp):
    cfg = current_app.config["AQ"]
    resp.set_cookie(COOKIE_NAME, "", max_age=0, httponly=True, secure=cfg.cookie_secure, samesite=cfg.cookie_samesite, path="/")


def load_session():
    """Populate g.user / g.csrf from the cookie, enforcing idle + absolute expiry."""
    g.user, g.csrf, g.token_hash = None, None, None
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return
    cfg = current_app.config["AQ"]
    th = _hash(token)
    now = time.time()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT s.*, u.id AS uid, u.email, u.role, u.first_name, u.last_name, u.phone, u.address, u.active, u.must_change_password"
            " FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash=?", (th,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return
    expired = row["expires_at"] < now or (now - row["last_seen"]) > cfg.session_idle_minutes * 60 or not row["active"]
    if expired or now - row["last_seen"] > 30:              # writes only when needed (not on every request)
        with db_session() as conn:
            if expired:
                conn.execute("DELETE FROM sessions WHERE token_hash=?", (th,))
            else:
                conn.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now, th))
    if expired:
        return
    g.user = {"id": row["uid"], "email": row["email"], "role": row["role"], "firstName": row["first_name"],
              "lastName": row["last_name"], "phone": row["phone"], "address": row["address"],
              "mustChangePassword": bool(row["must_change_password"])}
    g.csrf, g.token_hash = row["csrf_token"], th


def before_request():
    if request.method == "OPTIONS":
        return None
    if not request.path.startswith("/api/"):
        return None
    load_session()
    ep = request.endpoint or ""
    if ep in PUBLIC_ENDPOINTS:
        return None
    if g.user is None:
        return jsonify(error="Authentication required"), 401
    if g.user["mustChangePassword"] and ep not in ("me", "logout", "change_password"):
        return jsonify(error="Password change required", code="PASSWORD_CHANGE_REQUIRED"), 403
    if request.method not in ("GET", "HEAD"):
        sent = request.headers.get("X-CSRF-Token", "")
        if not g.csrf or not hmac.compare_digest(sent, g.csrf):
            return jsonify(error="Missing or invalid CSRF token"), 403
    return None


def require_role(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if g.get("user") is None:
                return jsonify(error="Authentication required"), 401
            if g.user["role"] not in roles:
                with db_session() as conn:
                    audit(conn, "forbidden", f"{request.method} {request.path}", user=g.user)
                return jsonify(error="Insufficient permissions"), 403
            return fn(*a, **kw)
        return wrapper
    return deco


def password_problem(pw: str, min_len: int) -> str | None:
    if len(pw) < min_len:
        return f"Password must be at least {min_len} characters"
    if pw.lower() == pw or pw.upper() == pw or not any(c.isdigit() for c in pw):
        return "Password must mix upper/lower case letters and include a digit"
    if pw.lower() in ("admin.aquaguard", "password123", "aquaguard123"):
        return "That password is too common"
    return None
