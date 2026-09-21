"""Error contract: {message, status, code, data?} — mirrors the upstream reference exactly."""

from enum import StrEnum


class HttpErrorCode(StrEnum):
    VALIDATION_ERROR = "validation_error"
    INVALID_CAPTCHA = "invalid_captcha"
    INVALID_CREDENTIALS = "invalid_credentials"
    UNAUTHORIZED = "unauthorized"
    UNAUTHORIZED_SHARE = "unauthorized_share"
    PAYMENT_REQUIRED = "payment_required"
    CREDIT_LIMIT_EXCEEDED = "credit_limit_exceeded"
    RESTRICTED_RESOURCE = "restricted_resource"
    NOT_FOUND = "not_found"
    REQUEST_TIMEOUT = "request_timeout"
    CONFLICT = "conflict"
    UNPROCESSABLE_ENTITY = "unprocessable_entity"
    FAILED_DEPENDENCY = "failed_dependency"
    USER_LIMIT_EXCEEDED = "user_limit_exceeded"
    TOO_MANY_REQUESTS = "too_many_requests"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    INTERNAL_SERVER_ERROR = "internal_server_error"
    DATABASE_CONNECTION_UNAVAILABLE = "database_connection_unavailable"
    GATEWAY_TIMEOUT = "gateway_timeout"
    UNKNOWN_ERROR_CODE = "unknown_error_code"
    NETWORK_ERROR = "network_error"
    VIEW_NOT_FOUND = "view_not_found"
    AUTOMATION_NODE_PARSE_ERROR = "automation_node_parse_error"
    AUTOMATION_NODE_NEED_TEST = "automation_node_need_test"
    AUTOMATION_NODE_TEST_OUTDATED = "automation_node_test_outdated"


ERROR_CODE_TO_STATUS: dict[HttpErrorCode, int] = {
    HttpErrorCode.VALIDATION_ERROR: 400,
    HttpErrorCode.INVALID_CAPTCHA: 400,
    HttpErrorCode.INVALID_CREDENTIALS: 400,
    HttpErrorCode.UNAUTHORIZED: 401,
    HttpErrorCode.UNAUTHORIZED_SHARE: 401,
    HttpErrorCode.PAYMENT_REQUIRED: 402,
    HttpErrorCode.CREDIT_LIMIT_EXCEEDED: 402,
    HttpErrorCode.RESTRICTED_RESOURCE: 403,
    HttpErrorCode.NOT_FOUND: 404,
    HttpErrorCode.REQUEST_TIMEOUT: 408,
    HttpErrorCode.CONFLICT: 409,
    HttpErrorCode.UNPROCESSABLE_ENTITY: 422,
    HttpErrorCode.FAILED_DEPENDENCY: 424,
    HttpErrorCode.USER_LIMIT_EXCEEDED: 460,
    HttpErrorCode.TOO_MANY_REQUESTS: 429,
    HttpErrorCode.PAYLOAD_TOO_LARGE: 413,
    HttpErrorCode.INTERNAL_SERVER_ERROR: 500,
    HttpErrorCode.DATABASE_CONNECTION_UNAVAILABLE: 503,
    HttpErrorCode.GATEWAY_TIMEOUT: 504,
    HttpErrorCode.UNKNOWN_ERROR_CODE: 500,
    HttpErrorCode.VIEW_NOT_FOUND: 404,
    HttpErrorCode.AUTOMATION_NODE_PARSE_ERROR: 400,
    HttpErrorCode.AUTOMATION_NODE_NEED_TEST: 400,
    HttpErrorCode.AUTOMATION_NODE_TEST_OUTDATED: 400,
    HttpErrorCode.NETWORK_ERROR: 0,
}

_DEFAULT_CODE_BY_STATUS: dict[int, HttpErrorCode] = {
    400: HttpErrorCode.VALIDATION_ERROR,
    401: HttpErrorCode.UNAUTHORIZED,
    402: HttpErrorCode.PAYMENT_REQUIRED,
    403: HttpErrorCode.RESTRICTED_RESOURCE,
    404: HttpErrorCode.NOT_FOUND,
    409: HttpErrorCode.CONFLICT,
    500: HttpErrorCode.INTERNAL_SERVER_ERROR,
    503: HttpErrorCode.DATABASE_CONNECTION_UNAVAILABLE,
    408: HttpErrorCode.REQUEST_TIMEOUT,
    429: HttpErrorCode.TOO_MANY_REQUESTS,
    413: HttpErrorCode.PAYLOAD_TOO_LARGE,
    504: HttpErrorCode.GATEWAY_TIMEOUT,
}


def default_code_by_status(status: int) -> HttpErrorCode:
    return _DEFAULT_CODE_BY_STATUS.get(status, HttpErrorCode.UNKNOWN_ERROR_CODE)


class ApiError(Exception):
    """Domain error carrying the exact wire contract fields."""

    def __init__(
        self,
        message: str,
        code: HttpErrorCode,
        data: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = ERROR_CODE_TO_STATUS[code]
        self.data = data

    @classmethod
    def not_found(cls, message: str = "Not Found") -> ApiError:
        return cls(message, HttpErrorCode.NOT_FOUND)

    @classmethod
    def unauthorized(cls, message: str = "Unauthorized") -> ApiError:
        return cls(message, HttpErrorCode.UNAUTHORIZED)

    @classmethod
    def forbidden(cls, message: str = "Forbidden resource") -> ApiError:
        return cls(message, HttpErrorCode.RESTRICTED_RESOURCE)

    @classmethod
    def validation(cls, message: str, data: dict | None = None) -> ApiError:
        return cls(message, HttpErrorCode.VALIDATION_ERROR, data)

    @classmethod
    def conflict(cls, message: str) -> ApiError:
        return cls(message, HttpErrorCode.CONFLICT)
