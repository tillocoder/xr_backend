from __future__ import annotations

from secrets import compare_digest

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request

from app.core.config import get_settings
from app.db.session import engine
from app.models.entities import AuthSession, User


class AdminAuthBackend(AuthenticationBackend):
    def __init__(self, secret_key: str) -> None:
        super().__init__(secret_key=secret_key)

    async def login(self, request: Request) -> bool:
        settings = get_settings()
        form = await request.form()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))
        if (
            compare_digest(username, settings.admin_panel_username)
            and compare_digest(password, settings.admin_panel_password)
        ):
            request.session.update({"token": "authenticated"})
            return True
        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return bool(request.session.get("token"))


class UserAdmin(ModelView, model=User):
    name = "User"
    name_plural = "Users"
    column_list = [User.id, User.email, User.full_name, User.role, User.is_active, User.created_at]
    column_searchable_list = [User.email, User.full_name]
    column_sortable_list = [User.created_at, User.email]


class AuthSessionAdmin(ModelView, model=AuthSession):
    name = "Auth session"
    name_plural = "Auth sessions"
    column_list = [
        AuthSession.id,
        AuthSession.user_id,
        AuthSession.refresh_jti,
        AuthSession.expires_at,
        AuthSession.revoked_at,
        AuthSession.created_at,
    ]
    can_create = False
    can_edit = False


def setup_admin_panel(app) -> None:
    settings = get_settings()
    authentication_backend = AdminAuthBackend(
        secret_key=settings.admin_panel_secret_key
    )
    admin = Admin(
        app,
        engine,
        authentication_backend=authentication_backend,
        base_url="/admin-panel",
        title=settings.project_name,
    )
    admin.add_view(UserAdmin)
    admin.add_view(AuthSessionAdmin)
