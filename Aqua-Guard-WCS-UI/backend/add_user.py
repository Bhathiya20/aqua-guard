"""Create or reset a user from the command line.

  python add_user.py                     # interactive (password prompt is hidden)
  python add_user.py someone@example.com user|admin     # password is prompted, never taken from argv/shell history

New / reset accounts must change their password at first login.
"""
import getpass
import sys

from werkzeug.security import generate_password_hash

import auth
import database
from config import load_settings


def add_or_update_user(email: str, password: str, role: str = "user", first_name: str = "User", last_name: str = "Member",
                       phone: str = "", address: str = "", min_len: int = 10):
    problem = auth.password_problem(password, min_len)
    if problem:
        raise SystemExit(f"[ERROR] {problem}")
    if role not in ("user", "admin"):
        raise SystemExit("[ERROR] role must be 'user' or 'admin'")
    conn = database.get_db()
    try:
        hashed = generate_password_hash(password)
        existing = conn.execute("SELECT id FROM users WHERE lower(email)=lower(?)", (email.strip(),)).fetchone()
        if existing:
            conn.execute("UPDATE users SET password_hash=?, role=?, active=1, failed_logins=0, locked_until=NULL, must_change_password=1,"
                         " updated_at=CURRENT_TIMESTAMP WHERE id=?", (hashed, role, existing["id"]))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (existing["id"],))
            print(f"[OK] Updated user {email} (role: {role}); password change required at next login")
        else:
            conn.execute("INSERT INTO users (first_name,last_name,email,password_hash,phone,address,role,must_change_password) VALUES (?,?,?,?,?,?,?,1)",
                         (first_name, last_name, email.strip(), hashed, phone, address, role))
            print(f"[OK] Created user {email} (role: {role}); password change required at first login")
    finally:
        conn.close()


if __name__ == "__main__":
    cfg = load_settings()
    database.set_db_path(cfg.db_path)
    database.init_db(cfg.default_admin_email, cfg.default_admin_password)
    email = sys.argv[1] if len(sys.argv) > 1 else input("Email: ").strip()
    role = sys.argv[2] if len(sys.argv) > 2 else (input("Role (admin/user) [user]: ").strip().lower() or "user")
    pw = getpass.getpass("Password: ")
    if pw != getpass.getpass("Repeat password: "):
        raise SystemExit("[ERROR] passwords differ")
    add_or_update_user(email, pw, role)
