"""Credential handling.

Uses Application Default Credentials (ADC), the same approach as
googleads/google-ads-mcp. Credentials are resolved, in order, from:

1. GOOGLE_APPLICATION_CREDENTIALS env var pointing at a service-account key.
2. `gcloud auth application-default login` user credentials.
3. The attached service account when running on GCP.

The service account (or user) must be invited as a user of the Play Console
developer account (Users and permissions) with at least "View app information"
permission for the Reporting API, and needs read access to the developer's
report bucket (pubsite_prod_*) for the CSV stats reports.
"""

from __future__ import annotations

import google.auth
from google.auth.transport.requests import AuthorizedSession

SCOPES = [
    "https://www.googleapis.com/auth/playdeveloperreporting",
    "https://www.googleapis.com/auth/devstorage.read_only",
]

_session: AuthorizedSession | None = None


def get_session() -> AuthorizedSession:
    """Return a cached requests session that attaches OAuth credentials."""
    global _session
    if _session is None:
        credentials, _ = google.auth.default(scopes=SCOPES)
        _session = AuthorizedSession(credentials)
    return _session
