"""Airtable / Google Sheet import — ports features/airtable-import +
features/google-sheet-import controllers.

Contract, auth, and cross-field validation are implemented; the real
third-party pull (Airtable meta/records API, Google Sheets API) is attempted
best-effort with the reference's error mapping but is not parity-verified
offline (deferred — see docs/api-parity-ledger.md).
"""
