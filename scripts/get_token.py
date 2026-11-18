"""Sign in through the browser (Cognito managed login, code flow with PKCE) and save the token.

    python3 scripts/get_token.py

Opens the sign-in page, receives the redirect on http://localhost:5173/ (the callback URL
registered for the app client), exchanges the code and writes the access token to
~/.config/expenses-tracker/token.json (mode 600). Nothing secret is printed.
The first sign-in with the temporary password asks for a new password, then a TOTP app or a
passkey; register the passkey when offered.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = 5173
REDIRECT = f"http://localhost:{PORT}/"
TOKEN_FILE = Path.home() / ".config" / "expenses-tracker" / "token.json"
WAIT_SECONDS = 300


def _terraform_outputs() -> dict[str, str]:
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run(  # noqa: S603 - fixed argv, no user input
        ["terraform", f"-chdir={root / 'infra'}", "output", "-json"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {k: v["value"] for k, v in json.loads(out).items()}


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


def authorize_url(domain: str, client_id: str, state: str, challenge: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "scope": "openid email",
            "redirect_uri": REDIRECT,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"https://{domain}/oauth2/authorize?{query}"


def wait_for_code(expected_state: str) -> str:
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code = params.get("code", [""])[0]
            state = params.get("state", [""])[0]
            ok = bool(code) and secrets.compare_digest(state, expected_state)
            if ok:
                result["code"] = code
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab." if ok else b"Invalid request.")

        def log_message(self, *args: object) -> None:  # never log the URL: it carries the code
            pass

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    server.timeout = WAIT_SECONDS
    deadline = time.monotonic() + WAIT_SECONDS
    while "code" not in result and time.monotonic() < deadline:
        server.handle_request()
    server.server_close()
    if "code" not in result:
        sys.exit("no valid sign-in received (timed out or state mismatch)")
    return result["code"]


def exchange(domain: str, client_id: str, code: str, verifier: str) -> dict[str, object]:
    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": verifier,
        }
    ).encode()
    req = urllib.request.Request(  # noqa: S310 - fixed https URL
        f"https://{domain}/oauth2/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
        data = json.load(resp)
    if not isinstance(data, dict) or "access_token" not in data:
        sys.exit("token exchange failed")
    return data


def main() -> None:
    outputs = _terraform_outputs()
    domain, client_id = outputs["cognito_domain"], outputs["cognito_client_id"]
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    url = authorize_url(domain, client_id, state, challenge)
    print("Opening the sign-in page. If the browser does not open, open it from this link:")
    print(url)
    webbrowser.open(url)
    tokens = exchange(domain, client_id, wait_for_code(state), verifier)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(
            {
                "access_token": tokens["access_token"],
                "expires_at": int(time.time()) + int(tokens.get("expires_in", 1800)),  # type: ignore[call-overload]
            },
            fh,
        )
    print(f"Signed in. Token saved to {TOKEN_FILE} (valid about 30 minutes).")


if __name__ == "__main__":
    main()
