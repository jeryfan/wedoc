"""Shared identity constants (anonymous / robot users) and id prefixes."""

from ...compat import system_email
from ..ids import IdPrefix  # noqa: F401  (re-exported for the security package)

ANONYMOUS_USER_ID = "anonymous"
ANONYMOUS_USER = {
    "id": ANONYMOUS_USER_ID,
    "name": "Anonymous",
    "email": system_email("anonymous"),
}

APP_ROBOT_ID = "appRobot"
APP_ROBOT_USER = {
    "id": APP_ROBOT_ID,
    "name": "App Robot",
    "email": system_email("appRobot"),
}

AUTOMATION_ROBOT_ID = "automationRobot"
AUTOMATION_ROBOT_USER = {
    "id": AUTOMATION_ROBOT_ID,
    "name": "Automation Robot",
    "email": system_email("automationRobot"),
}

SYSTEM_USER_ID = "system"


def is_anonymous(user_id: str | None) -> bool:
    return user_id == ANONYMOUS_USER_ID
