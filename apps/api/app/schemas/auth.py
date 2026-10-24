import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

NormalizedEmail = Annotated[EmailStr, AfterValidator(lambda v: v.strip().lower())]


class RegisterIn(BaseModel):
    email: NormalizedEmail
    # Upper bound keeps hashing cost bounded; lower bound per NIST 800-63B guidance.
    password: str = Field(min_length=10, max_length=128)
    display_name: str | None = Field(default=None, max_length=100)
    invite_code: str | None = Field(default=None, max_length=200)


class AuthConfigOut(BaseModel):
    """Lets the sign-up page show an invite-code field, or say sign-up is closed."""

    registration: Literal["open", "invite", "closed"]


class LoginIn(BaseModel):
    email: NormalizedEmail
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str | None
    created_at: datetime
