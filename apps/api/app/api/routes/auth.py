import hashlib
import hmac
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.deps import CurrentUser, DbSession, RedisDep, SettingsDep, client_ip
from app.core import ratelimit
from app.core.config import Settings
from app.schemas.auth import AuthConfigOut, LoginIn, RegisterIn, UserOut
from app.services import auth as auth_service

router = APIRouter(tags=["auth"])


def _set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def _too_many(retry_after: int) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "Too many attempts. Try again later.",
        headers={"Retry-After": str(retry_after)},
    )


@router.get("/config")
async def auth_config(settings: SettingsDep) -> AuthConfigOut:
    if not settings.allow_registration:
        return AuthConfigOut(registration="closed")
    return AuthConfigOut(registration="invite" if settings.registration_code else "open")


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterIn,
    request: Request,
    response: Response,
    db: DbSession,
    redis: RedisDep,
    settings: SettingsDep,
) -> UserOut:
    if not settings.allow_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is disabled")
    retry_after = await ratelimit.hit(
        redis,
        f"rl:register:ip:{client_ip(request)}",
        limit=settings.register_rate_limit,
        window_seconds=settings.register_rate_window_seconds,
    )
    if retry_after:
        raise _too_many(retry_after)
    # Checked after the rate limit, so the code can't be guessed quickly.
    if settings.registration_code and not hmac.compare_digest(
        (body.invite_code or "").strip().encode(), settings.registration_code.encode()
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "A valid invite code is needed to create an account."
        )

    try:
        user = await auth_service.create_user(
            db, email=body.email, password=body.password, display_name=body.display_name
        )
    except auth_service.EmailTakenError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An account with this email already exists"
        ) from None
    token = auth_service.start_session(
        db,
        user,
        ttl=timedelta(hours=settings.session_ttl_hours),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    _set_session_cookie(response, settings, token)
    return UserOut.model_validate(user)


@router.post("/login")
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: DbSession,
    redis: RedisDep,
    settings: SettingsDep,
) -> UserOut:
    # Limit per IP and per account, so neither one machine nor a botnet can grind one password.
    email_key = f"rl:login:email:{hashlib.sha256(body.email.encode()).hexdigest()}"
    limits = (
        (f"rl:login:ip:{client_ip(request)}", settings.login_ip_rate_limit),
        (email_key, settings.login_rate_limit),
    )
    for key, limit in limits:
        retry_after = await ratelimit.hit(
            redis, key, limit=limit, window_seconds=settings.login_rate_window_seconds
        )
        if retry_after:
            raise _too_many(retry_after)

    user = await auth_service.authenticate(db, email=body.email, password=body.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    # The account owner got in; don't let their earlier typos count against them.
    await redis.delete(email_key)

    # Never reuse a session ID across a login (session fixation).
    if old_token := request.cookies.get(settings.session_cookie_name):
        await auth_service.end_session(db, old_token)
    token = auth_service.start_session(
        db,
        user,
        ttl=timedelta(hours=settings.session_ttl_hours),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    _set_session_cookie(response, settings, token)
    return UserOut.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request, response: Response, db: DbSession, settings: SettingsDep
) -> None:
    if token := request.cookies.get(settings.session_cookie_name):
        await auth_service.end_session(db, token)
        await db.commit()
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
