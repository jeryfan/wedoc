"""Request schemas + cross-field refinements for airtable / google-sheet import.

The base object shape is validated via ZodModel (types, min-length, required);
the zod `.superRefine` cross-field rules are reproduced as explicit checks that
raise the same zod-validation-error message ("<Message> at \"<path>\"").
"""

from typing import Any

from ...core.errors import ApiError, HttpErrorCode
from ...core.validation import ZodModel


class AirtableAnalyzeRo(ZodModel):
    integrationId: str | None = None
    accessToken: str | None = None
    airtableBaseId: str | None = None


class AirtableImportRo(ZodModel):
    spaceId: str | None = None
    baseId: str | None = None
    folderId: str | None = None
    integrationId: str | None = None
    accessToken: str | None = None
    airtableBaseId: str
    baseName: str | None = None
    importRecords: bool | None = None
    importAttachments: bool | None = None
    importViewConfig: bool | None = None
    shareLink: str | None = None


class GoogleSheetAnalyzeRo(ZodModel):
    integrationId: str | None = None
    accessToken: str | None = None
    spreadsheetId: str


class GoogleSheetImportRo(ZodModel):
    spaceId: str | None = None
    baseId: str | None = None
    integrationId: str | None = None
    accessToken: str | None = None
    spreadsheetId: str
    baseName: str | None = None
    sheetIds: list[int] | None = None
    importRecords: bool | None = None


def _raise_issues(issues: list[tuple[str, str]]) -> None:
    """zod collects every superRefine issue, then the zod-validation-error
    formatter title-cases each and joins with '; '."""
    if not issues:
        return
    parts = [
        (f"{m[0].upper() + m[1:] if m else m}" + f' at "{p}"') for m, p in issues
    ]
    raise ApiError(
        "Validation error: " + "; ".join(parts),
        HttpErrorCode.VALIDATION_ERROR,
    )


def refine_airtable_credentials(data: Any) -> None:
    issues: list[tuple[str, str]] = []
    if not data.integrationId and not data.accessToken:
        issues.append(("Either integrationId or accessToken is required", "integrationId"))
    _raise_issues(issues)


def refine_airtable_import(data: AirtableImportRo) -> None:
    issues: list[tuple[str, str]] = []
    if not data.integrationId and not data.accessToken:
        issues.append(("Either integrationId or accessToken is required", "integrationId"))
    if data.importViewConfig and not (data.shareLink and data.shareLink.strip()):
        issues.append(("A shareLink is required to import view configuration", "shareLink"))
    if not data.baseId and not data.baseName:
        issues.append(("baseName is required when baseId is not provided.", "baseName"))
    if not data.baseId and not data.spaceId:
        issues.append(("spaceId is required when baseId is not provided.", "spaceId"))
    if data.folderId and not data.baseId:
        issues.append(
            (
                "folderId is only supported when importing into an existing base (baseId).",
                "folderId",
            )
        )
    _raise_issues(issues)


def refine_google_import(data: GoogleSheetImportRo) -> None:
    issues: list[tuple[str, str]] = []
    if not data.baseId and not data.baseName:
        issues.append(("baseName is required when baseId is not provided.", "baseName"))
    if not data.baseId and not data.spaceId:
        issues.append(("spaceId is required when baseId is not provided.", "spaceId"))
    _raise_issues(issues)
