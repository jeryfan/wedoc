"""Request schemas for /api/auth — field-level ports of packages/openapi/src/auth.

Field order follows the zod shape order (signinSchema then signup's extend
order) so multi-error messages list issues in the same sequence as upstream.
"""

from pydantic import field_validator

from ...core.validation import (
    ZodEmailStr,
    ZodModel,
    zod_email,
    zod_int,
    zod_password_checks,
)

SPACE_NAME_MAX_LENGTH = 100


def _check_email(v: str) -> str:
    return zod_email(v)


def _check_email_lower(v: str) -> str:
    return zod_email(v).lower()


class SignupVerification(ZodModel):
    code: str
    token: str


class RefMeta(ZodModel):
    query: str | None = None
    referer: str | None = None


class SignupBody(ZodModel):
    email: str
    password: str
    turnstileToken: str | None = None
    defaultSpaceName: str | None = None
    refMeta: RefMeta | None = None
    verification: SignupVerification | None = None
    inviteCode: str | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return _check_email_lower(v)

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        return zod_password_checks(v, strength=True)

    @field_validator("defaultSpaceName")
    @classmethod
    def _default_space_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) < 1:
            raise ValueError("Too small: expected string to have >=1 characters")
        if len(v) > SPACE_NAME_MAX_LENGTH:
            raise ValueError("Too big: expected string to have <=100 characters")
        return v


class SendSignupVerificationCodeBody(ZodModel):
    email: str
    turnstileToken: str | None = None

    _email = field_validator("email")(_check_email)


class JoinWaitlistBody(ZodModel):
    email: str

    _email = field_validator("email")(_check_email)


class InviteWaitlistBody(ZodModel):
    list: list[ZodEmailStr]


class WaitlistInviteCodeBody(ZodModel):
    count: int
    times: int

    @field_validator("count", "times", mode="before")
    @classmethod
    def _int(cls, v: object) -> int:
        return zod_int(v)


class ChangePasswordBody(ZodModel):
    password: str
    newPassword: str

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        return zod_password_checks(v, strength=False)

    @field_validator("newPassword")
    @classmethod
    def _new_password(cls, v: str) -> str:
        return zod_password_checks(v, strength=True)


class SendResetPasswordEmailBody(ZodModel):
    email: str

    _email = field_validator("email")(_check_email)


class ResetPasswordBody(ZodModel):
    password: str
    code: str

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        return zod_password_checks(v, strength=True)


class AddPasswordBody(ZodModel):
    password: str

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        return zod_password_checks(v, strength=True)


class ChangeEmailBody(ZodModel):
    email: str
    token: str
    code: str

    _email = field_validator("email")(_check_email)


class SendChangeEmailCodeBody(ZodModel):
    email: str
    password: str

    _email = field_validator("email")(_check_email)


class DeleteUserQuery(ZodModel):
    confirm: str

    @field_validator("confirm")
    @classmethod
    def _confirm(cls, v: str) -> str:
        if v != "DELETE":
            raise ValueError("Please enter DELETE to confirm")
        return v
