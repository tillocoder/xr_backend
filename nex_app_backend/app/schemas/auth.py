from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_serializer, model_validator


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    full_name: str
    role: str
    is_active: bool
    created_at: datetime


class RegisterRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    email: EmailStr
    full_name: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=8, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: object) -> object:
        if isinstance(data, dict) and "fullName" in data and "full_name" not in data:
            return {**data, "full_name": data["fullName"]}
        return data


class LoginRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class TokenRefreshRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    refresh_token: str = Field(min_length=20)

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: object) -> object:
        if isinstance(data, dict) and "refreshToken" in data and "refresh_token" not in data:
            return {**data, "refresh_token": data["refreshToken"]}
        return data


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if "currentPassword" in normalized and "current_password" not in normalized:
            normalized["current_password"] = normalized["currentPassword"]
        if "newPassword" in normalized and "new_password" not in normalized:
            normalized["new_password"] = normalized["newPassword"]
        return normalized


class TokenPair(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    access_token: str
    refresh_token: str
    token_type: str = "bearer"

    @model_serializer(mode="plain")
    def serialize(self) -> dict[str, str]:
        return {
            "accessToken": self.access_token,
            "refreshToken": self.refresh_token,
            "tokenType": self.token_type,
        }


class AuthResponse(BaseModel):
    tokens: TokenPair
    user: UserRead


class LogoutResponse(BaseModel):
    detail: str
