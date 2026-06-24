"""Auth routes: login, logout, manage users, change password."""
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import verify_password, hash_password, check_password_strength
from app.core.ratelimit import _get_ip, is_rate_limited, record_failure, clear_failures
from app.models.user import User
from app.web.deps import _tmpl, require_user

router = APIRouter(tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/upload", status_code=302)
    return _tmpl(request, "login.html", {"error": None})


@router.post("/login")
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    ip = _get_ip(request)
    blocked, wait = is_rate_limited(ip)
    if blocked:
        mins = wait // 60 + 1
        return _tmpl(
            request, "login.html",
            {"error": f"Too many failed attempts. Try again in {mins} minute(s)."},
            status_code=429,
        )

    user = db.query(User).filter(User.username == username, User.is_active.is_(True)).first()
    if not user or not verify_password(password, user.password_hash):
        record_failure(ip)
        return _tmpl(request, "login.html", {"error": "Invalid username or password."}, status_code=401)

    clear_failures(ip)
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["name"] = user.name
    request.session["is_admin"] = user.is_admin
    return RedirectResponse("/upload", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@router.get("/change-password", response_class=HTMLResponse)
def change_password_get(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=302)
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    required = request.query_params.get("required") == "1"
    return _tmpl(request, "change_password.html", {"user": user, "error": None, "success": None, "required": required})


@router.post("/change-password")
async def change_password_post(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=302)
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        request.session.clear()
        return RedirectResponse("/login", status_code=302)

    required = request.query_params.get("required") == "1"
    ctx = {"user": user, "error": None, "success": None, "required": required}

    if not verify_password(current_password, user.password_hash):
        ctx["error"] = "Current password is incorrect."
        return _tmpl(request, "change_password.html", ctx, status_code=400)
    if new_password != confirm_password:
        ctx["error"] = "New passwords do not match."
        return _tmpl(request, "change_password.html", ctx, status_code=400)
    strength_err = check_password_strength(new_password)
    if strength_err:
        ctx["error"] = strength_err
        return _tmpl(request, "change_password.html", ctx, status_code=400)

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    db.commit()

    if required:
        return RedirectResponse("/upload", status_code=303)
    ctx["success"] = "Password changed successfully."
    return _tmpl(request, "change_password.html", ctx)


@router.get("/users", response_class=HTMLResponse)
def users_list(request: Request, db: Session = Depends(get_db)):
    user, redir = require_user(request, db)
    if redir:
        return redir
    if not user.is_admin:
        return RedirectResponse("/upload", status_code=302)
    users = db.query(User).order_by(User.name).all()
    return _tmpl(request, "users.html", {"user": user, "users": users, "message": None})


@router.post("/users/new")
async def users_new(
    request: Request,
    name: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    is_admin: str = Form(default=""),
    db: Session = Depends(get_db),
):
    user, redir = require_user(request, db)
    if redir:
        return redir
    if not user.is_admin:
        return RedirectResponse("/upload", status_code=302)
    users = db.query(User).order_by(User.name).all()
    ctx = {"user": user, "users": users, "message": None}

    strength_err = check_password_strength(password)
    if strength_err:
        ctx["message"] = strength_err
        return _tmpl(request, "users.html", ctx, status_code=400)
    if db.query(User).filter(User.username == username).first():
        ctx["message"] = f"Username '{username}' already exists."
        return _tmpl(request, "users.html", ctx, status_code=400)

    new_user = User(
        name=name.strip(),
        username=username.strip().lower(),
        password_hash=hash_password(password),
        is_admin=bool(is_admin),
        is_active=True,
        must_change_password=True,  # new accounts must set their own password on first login
    )
    db.add(new_user)
    db.commit()
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{user_id}/reset-password")
async def reset_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    db: Session = Depends(get_db),
):
    admin, redir = require_user(request, db)
    if redir:
        return redir
    if not admin.is_admin:
        return RedirectResponse("/upload", status_code=302)

    strength_err = check_password_strength(new_password)
    if strength_err:
        # Re-render users page with error
        users = db.query(User).order_by(User.name).all()
        return _tmpl(
            request, "users.html",
            {"user": admin, "users": users, "message": strength_err},
            status_code=400,
        )

    target = db.query(User).filter(User.id == user_id).first()
    if target:
        target.password_hash = hash_password(new_password)
        target.must_change_password = True  # force them to change the admin-set password
        db.commit()
    return RedirectResponse("/users", status_code=303)
