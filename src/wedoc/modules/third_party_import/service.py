"""Airtable / Google Sheet import service.

Contract, auth and validation are fully implemented. The real third-party pull
is attempted best-effort against the vendor APIs with the reference's error
mapping, but the actual base-from-schema creation is deferred (documented in
the ledger): a successful fetch still ends the stream with a deferral error
event rather than materializing tables.
"""

from __future__ import annotations

from typing import Any

import httpx

from ...core.errors import ApiError, HttpErrorCode

_DEFERRED = "Third-party import materialization is not yet available on this instance"


class VendorApiError(Exception):
    def __init__(self, status: int, message: str, auth_mode: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.auth_mode = auth_mode


def format_airtable_error(error: Exception) -> str:
    if isinstance(error, VendorApiError):
        if error.status == 401:
            return "Airtable rejected the access token. Check that the token is valid."
        if error.status in (403, 404):
            return (
                "Airtable base not accessible. Grant the token access to the base and the "
                '"data.records:read" and "schema.bases:read" scopes.'
            )
        return f"Airtable API error: {error.message}"
    return str(error) if str(error) else "Unknown import error"


def format_google_error(error: Exception) -> str:
    if isinstance(error, VendorApiError):
        if error.status == 401:
            return "Google rejected the access token. Reconnect the Google Sheets integration."
        if error.status in (403, 404):
            if error.auth_mode == "apiKey":
                return (
                    "Spreadsheet not accessible without signing in. Share it as "
                    '"anyone with the link", or connect the Google Sheets integration and pick '
                    "it in the Google Picker."
                )
            return (
                "Spreadsheet not accessible. The drive.file grant only covers files picked "
                "through the Google Picker — pick the spreadsheet again."
            )
        return f"Google Sheets API error: {error.message}"
    return str(error) if str(error) else "Unknown import error"


async def airtable_analyze(
    access_token: str | None, airtable_base_id: str | None
) -> dict[str, Any]:
    if not access_token:
        # integrationId path resolves a stored OAuth token server-side; that
        # integration store is not implemented (deferred).
        raise ApiError(_DEFERRED, HttpErrorCode.VALIDATION_ERROR)
    headers = {"Authorization": f"Bearer {access_token}"}
    url = (
        f"https://api.airtable.com/v0/meta/bases/{airtable_base_id}/tables"
        if airtable_base_id
        else "https://api.airtable.com/v0/meta/bases"
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code >= 400:
        raise VendorApiError(resp.status_code, resp.text)
    # Real analyze VO mapping is deferred; a schema-valid, authorized fetch is
    # not materialized into the analyze VO yet.
    raise ApiError(_DEFERRED, HttpErrorCode.VALIDATION_ERROR)


async def google_analyze(
    access_token: str | None, spreadsheet_id: str
) -> dict[str, Any]:
    auth_mode = "token" if access_token else "apiKey"
    headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code >= 400:
        raise VendorApiError(resp.status_code, resp.text, auth_mode)
    raise ApiError(_DEFERRED, HttpErrorCode.VALIDATION_ERROR)
