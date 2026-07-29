#!/usr/bin/env python
from __future__ import annotations

import argparse
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from quran_video.config import Settings
from quran_video.youtube.client import YOUTUBE_SCOPE


def main() -> int:
    settings = Settings()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--client-id", default=os.getenv("YOUTUBE_CLIENT_ID") or settings.youtube_client_id
    )
    parser.add_argument(
        "--client-secret",
        default=os.getenv("YOUTUBE_CLIENT_SECRET") or settings.youtube_client_secret,
    )
    args = parser.parse_args()
    if not args.client_id or not args.client_secret:
        raise SystemExit("YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET are required")
    print(
        "Before continuing, set Google Auth Platform > Audience > Publishing status to "
        "In production. Testing-mode refresh tokens expire after seven days.",
        file=sys.stderr,
    )
    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": args.client_id,
                "client_secret": args.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=YOUTUBE_SCOPE,
    )
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not credentials.refresh_token:
        raise SystemExit(
            "Google did not return a refresh token. Revoke the app's existing account access "
            "and run this command again."
        )
    print("YOUTUBE_REFRESH_TOKEN=" + credentials.refresh_token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
