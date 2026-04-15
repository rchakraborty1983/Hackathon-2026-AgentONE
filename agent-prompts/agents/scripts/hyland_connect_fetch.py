#!/usr/bin/env python3
r"""
Silent Hyland Connect Community forum fetcher.

AUTHENTICATION
--------------
Uses Okta SSO (id.hyland.com) for a one-time authenticated session, then
caches the session cookies so subsequent calls require no credentials.

Credentials are read ONLY from environment variables — never stored in this file.

Required env vars (set once per machine):
  HYLAND_CONNECT_USERNAME   Hyland Community username / email
  HYLAND_CONNECT_PASSWORD   Hyland Community password

Set permanently (no admin required):
  PowerShell (user-level):
    [System.Environment]::SetEnvironmentVariable("HYLAND_CONNECT_USERNAME","you@example.com","User")
    [System.Environment]::SetEnvironmentVariable("HYLAND_CONNECT_PASSWORD","yourpassword","User")
  Then restart VS Code / your terminal.

SESSION CACHE
-------------
Session cookies (not credentials) are cached at:
  Windows:  %USERPROFILE%\.hyland_connect_session
  Unix:     ~/.hyland_connect_session
Cache is reused for 7 days before re-authentication is triggered.

USAGE
-----
  python hyland_connect_fetch.py search "<query>" [--limit N] [--best-match]
  python hyland_connect_fetch.py board <boardId|alias> [--limit N]
  python hyland_connect_fetch.py thread <messageId>
  python hyland_connect_fetch.py url "<full-hyland-connect-url>"

BOARD ALIASES
-------------
  workview     onbase23forum-board     (Low-code & WorkView)
  onbase       onbase1forum-board      (OnBase general)
  technical    onbase31forum-board     (OnBase Technical)
  government   onbase12forum-board     (Government Modules)
  records      onbase27forum-board     (Records Management)
"""

import sys
import os
import json
import time
import argparse
import textwrap
from pathlib import Path
from urllib.parse import urlparse, parse_qs

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print(
        "ERROR: Required packages not installed.\n"
        "Run: pip install requests beautifulsoup4",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://connect.hyland.com"
API_BASE = f"{BASE_URL}/api/2.0"
OKTA_BASE = "https://id.hyland.com"
# Okta app instance for Hyland Connect (Okta app path from SAML form action)
OKTA_APP_PATH = "/app/alfrescosoftware-customer_verticcommunitykhos_1/exklzv2jtq4a9eiIS4x7/sso/saml"
SAML_ACS_PATH = "/plugins/common/feature/samlss/doauth/post"
# Seed URL used to trigger the SAML flow when we need fresh cookies
SAML_SEED_URL = f"{BASE_URL}/t5/onbase-forum/bd-p/onbase1forum-board"

SESSION_FILE = Path.home() / ".hyland_connect_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600  # 7 days

BOARD_ALIASES = {
    "workview": "onbase23forum-board",
    "onbase": "onbase1forum-board",
    "technical": "onbase31forum-board",
    "government": "onbase12forum-board",
    "records": "onbase27forum-board",
}

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class AuthError(RuntimeError):
    pass


class HylandConnectClient:
    """
    Fetches Hyland Connect forum content silently.
    Authenticates via Okta SAML SSO on first use; reuses cached cookies thereafter.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": BROWSER_UA})
        self._authenticated = self._load_cached_session()

    # ------------------------------------------------------------------
    # Session persistence  (cookies only — credentials never written)
    # ------------------------------------------------------------------

    def _load_cached_session(self) -> bool:
        if not SESSION_FILE.exists():
            return False
        try:
            data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            if time.time() - data.get("created", 0) < SESSION_TTL_SECONDS:
                for name, value in data.get("cookies", {}).items():
                    self.session.cookies.set(name, value, domain="connect.hyland.com")
                return True
        except Exception:
            pass
        return False

    def _save_session(self):
        data = {
            "created": time.time(),
            "cookies": {c.name: c.value for c in self.session.cookies},
        }
        try:
            SESSION_FILE.write_text(json.dumps(data), encoding="utf-8")
            SESSION_FILE.chmod(0o600)   # owner-read-write only (no-op on Windows)
        except Exception:
            pass  # Non-fatal — next run will just re-authenticate

    def _clear_session(self):
        self.session.cookies.clear()
        self._authenticated = False
        try:
            SESSION_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Okta SAML SSO authentication flow
    # ------------------------------------------------------------------

    def _get_credentials(self):
        username = os.environ.get("HYLAND_CONNECT_USERNAME", "").strip()
        password = os.environ.get("HYLAND_CONNECT_PASSWORD", "").strip()
        if not username or not password:
            print(
                "\nERROR: Hyland Connect credentials not configured.\n"
                "\nSet these environment variables once on your machine (no admin required):\n"
                "\n  PowerShell:\n"
                '    [System.Environment]::SetEnvironmentVariable('
                '"HYLAND_CONNECT_USERNAME", "you@example.com", "User")\n'
                '    [System.Environment]::SetEnvironmentVariable('
                '"HYLAND_CONNECT_PASSWORD", "yourpassword", "User")\n'
                "\n  Then restart VS Code / your terminal for the vars to take effect.\n",
                file=sys.stderr,
            )
            sys.exit(1)
        return username, password

    def _okta_session_token(self, username: str, password: str) -> str:
        """POST credentials to Okta /api/v1/authn; return sessionToken on SUCCESS."""
        resp = requests.post(
            f"{OKTA_BASE}/api/v1/authn",
            json={"username": username, "password": password},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=15,
        )
        body = resp.json()
        status = body.get("status", "")

        if status == "SUCCESS":
            return body["sessionToken"]

        if status in ("MFA_ENROLL", "MFA_REQUIRED", "MFA_CHALLENGE"):
            print(
                "\nERROR: Your Hyland Connect account requires MFA.\n"
                "Automated passwordless login cannot complete MFA challenges.\n"
                "\nOptions:\n"
                "  1. Ask your Hyland admin to exempt connect.hyland.com from MFA policy.\n"
                "  2. Log in once via the IDE browser; the agent will reuse those cookies.\n",
                file=sys.stderr,
            )
            sys.exit(1)

        err = body.get("errorSummary") or body.get("errorCode") or resp.text[:300]
        raise AuthError(f"Okta authentication failed (status={resp.status_code}): {err}")

    def _complete_saml_flow(self, session_token: str, seed_url: str = SAML_SEED_URL):
        """
        Exchange an Okta sessionToken for Khoros session cookies via SAML.

        1. GET seed_url → Khoros redirects to a SAML form (SAMLRequest + RelayState)
        2. GET the Okta SSO URL with sessionToken → Okta returns a SAMLResponse form
        3. POST SAMLResponse to the Khoros ACS endpoint
        4. Khoros sets session cookies; we cache them
        """
        # Step 1: obtain SAML form from Khoros
        init_resp = self.session.get(seed_url, timeout=15, allow_redirects=True)
        soup1 = BeautifulSoup(init_resp.text, "html.parser")
        form1 = soup1.find("form")

        if not form1:
            # Already authenticated — just cache whatever cookies we have
            self._save_session()
            return

        okta_sso_url = form1.get("action") or f"{OKTA_BASE}{OKTA_APP_PATH}"
        relay_input = form1.find("input", {"name": "RelayState"})
        relay_state = relay_input["value"] if relay_input else ""

        # Step 2: GET Okta SSO URL with sessionToken (skips login UI)
        okta_resp = self.session.get(
            okta_sso_url,
            params={"sessionToken": session_token},
            timeout=15,
            allow_redirects=True,
        )
        soup2 = BeautifulSoup(okta_resp.text, "html.parser")
        form2 = soup2.find("form")

        if not form2:
            # Possibly already logged in via an existing Okta session
            self._save_session()
            return

        sp_url = form2.get("action") or f"{BASE_URL}{SAML_ACS_PATH}"
        saml_resp_el = form2.find("input", {"name": "SAMLResponse"})
        relay2_el = form2.find("input", {"name": "RelayState"})

        if not saml_resp_el:
            raise AuthError("Okta did not return a SAMLResponse in the POST form")

        post_data = {
            "SAMLResponse": saml_resp_el["value"],
            "RelayState": (relay2_el["value"] if relay2_el else relay_state),
        }

        # Step 3: POST SAMLResponse back to Khoros
        self.session.post(sp_url, data=post_data, timeout=15, allow_redirects=True)
        self._save_session()

    def _authenticate(self, seed_url: str = SAML_SEED_URL):
        username, password = self._get_credentials()
        try:
            session_token = self._okta_session_token(username, password)
            self._complete_saml_flow(session_token, seed_url)
            self._authenticated = True
        except AuthError as exc:
            print(f"\nERROR: {exc}", file=sys.stderr)
            sys.exit(1)

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _is_auth_gate(self, resp: requests.Response) -> bool:
        """Return True when the response is a Khoros/Okta auth wall."""
        if resp.status_code == 403:
            return True
        if "id.hyland.com" in resp.url:
            return True
        if "samlss/doauth" in resp.url:
            return True
        # Soft gate: page renders but user is not logged in
        # Khoros shows a "Sign In" / "Log in" link in the global nav when unauthenticated
        if resp.status_code == 200:
            snippet = resp.text[:5000]
            if ('href="/t5/user/viewprofilepage/tab/sign-in' in snippet
                    or 'lia-link-navigation lia-custom-event" href="/t5/' not in snippet
                    and ('Sign In' in snippet or 'Log in' in snippet)
                    and 'log-out' not in snippet.lower()):
                return True
        return False

    def _get_page(self, url: str, _retry: bool = True) -> requests.Response:
        """GET a URL; if auth-gated, authenticate once and retry."""
        resp = self.session.get(url, timeout=20, allow_redirects=True)
        if self._is_auth_gate(resp) and _retry:
            self._clear_session()
            self._authenticate(seed_url=url)
            resp = self.session.get(url, timeout=20, allow_redirects=True)
        return resp

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(self, query: str, limit: int = 10, sort_by_date: bool = True) -> str:
        """Search across all forum boards."""
        url = f"{BASE_URL}/t5/forums/searchpage/tab/message?q={requests.utils.quote(query)}"
        if sort_by_date:
            url += "&sort_by=-topicPostDate"
        resp = self._get_page(url)
        result = _parse_search_html(resp.text, query, limit)
        # If we got no results and we haven't authenticated yet, the search page
        # may have silently shown an empty page rather than a 403.  Try once more
        # after authenticating.
        if not self._authenticated and "No search results" in result:
            self._authenticate(seed_url=url)
            resp = self.session.get(url, timeout=20, allow_redirects=True)
            result = _parse_search_html(resp.text, query, limit)
        return result

    def get_board_messages(self, board_id: str, limit: int = 15) -> str:
        """List recent topics on a specific board."""
        url = f"{BASE_URL}/t5/-/bd-p/{board_id}?sort=date_desc"
        resp = self._get_page(url)
        return _parse_board_html(resp.text, board_id, limit)

    def get_thread(self, message_id: str) -> str:
        """Fetch the OP and all replies for a thread."""
        url = f"{BASE_URL}/t5/-/td-p/{message_id}"
        resp = self._get_page(url)
        return _parse_thread_html(resp.text, message_id)

    def fetch_url(self, url: str) -> str:
        """Auto-detect URL type and dispatch to the right method."""
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")

        if "/td-p/" in path:
            return self.get_thread(path.split("/td-p/")[-1])
        if "/bd-p/" in path:
            return self.get_board_messages(path.split("/bd-p/")[-1])
        if "searchpage" in path:
            qs = parse_qs(parsed.query)
            q = qs.get("q", [""])[0]
            sort_by_date = "sort_by" in parsed.query
            return self.search(q, sort_by_date=sort_by_date)

        resp = self._get_page(url)
        return _html_to_plain(resp.text)


# ---------------------------------------------------------------------------
# HTML parsers
# ---------------------------------------------------------------------------


def _html_to_plain(html: str, max_chars: int = 3000) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    text = "\n".join(lines)
    return (text[:max_chars] + "\n[…truncated]") if len(text) > max_chars else text


def _parse_search_html(html: str, query: str, limit: int) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # Khoros marks each search hit as a <li> with class containing "search-result"
    # and puts the thread link inside with class "lia-link-message-view-thread-subject"
    links = soup.select(".lia-link-message-view-thread-subject")
    if not links:
        links = [a for a in soup.find_all("a", href=True) if "/td-p/" in a["href"]]
    if not links:
        return f"No search results found for {query!r}.\n(Page title: {soup.title.string if soup.title else 'unknown'})"

    lines = [f"Search: {query!r}  —  {min(len(links), limit)} result(s)\n"]
    for link in links[:limit]:
        title = link.get_text(strip=True)
        href = link["href"]
        if not href.startswith("http"):
            href = BASE_URL + href

        # Look for an accepted-solution badge near this link
        parent = link.parent
        solved = ""
        for _ in range(6):
            if parent is None:
                break
            if parent.select(".lia-message-accepted-solution, .accepted-solution-label"):
                solved = "  ✅ accepted answer"
                break
            parent = parent.parent

        lines.append(f"• [{title}]({href}){solved}")

    return "\n".join(lines)


def _parse_board_html(html: str, board_id: str, limit: int) -> str:
    soup = BeautifulSoup(html, "html.parser")

    links = soup.select(".lia-link-message-view-thread-subject")
    if not links:
        links = [a for a in soup.find_all("a", href=True) if "/td-p/" in a["href"]]
    if not links:
        return f"No topics found in board {board_id!r}.\n(Page title: {soup.title.string if soup.title else 'unknown'})"

    lines = [f"Latest topics — {board_id}\n"]
    for link in links[:limit]:
        title = link.get_text(strip=True)
        href = link["href"]
        if not href.startswith("http"):
            href = BASE_URL + href

        # Try to grab post date near the link
        parent = link.parent
        date_str = ""
        for _ in range(6):
            if parent is None:
                break
            t = parent.select_one("time, .lia-message-posted-on, .post-date")
            if t:
                date_str = t.get_text(strip=True)[:16]
                break
            parent = parent.parent

        solved = ""
        if link.find_parent(class_=lambda c: c and "solved" in c):
            solved = "  ✅"

        lines.append(f"• [{title}]({href})" + (f"  {date_str}" if date_str else "") + solved)

    return "\n".join(lines)


def _parse_thread_html(html: str, thread_id: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    title_el = (soup.select_one("h1.lia-message-subject a")
                or soup.select_one("h1.page-header")
                or soup.title)
    title = title_el.get_text(strip=True) if title_el else f"Thread {thread_id}"

    lines = [f"# {title}", f"URL: {BASE_URL}/t5/-/td-p/{thread_id}", ""]

    # Each post body is wrapped in .lia-message-body-content
    posts = soup.select(".lia-message-body-content")
    if not posts:
        posts = soup.select(".message-body, .post-body, .MessageBody")
    if not posts:
        return _html_to_plain(html)

    for idx, post in enumerate(posts):
        # Author (typically in .lia-user-name just before the post)
        author_el = post.find_previous(class_=lambda c: c and (
            "lia-user-name" in c or "UserName" in c or "login-name" in c))
        author = author_el.get_text(strip=True) if author_el else f"User {idx + 1}"

        # Timestamp
        date_el = post.find_previous("time")
        date_str = date_el.get("datetime", date_el.get_text(strip=True))[:16] if date_el else ""

        # Accepted solution flag (check ancestor elements for the badge)
        is_solution = False
        parent = post.parent
        for _ in range(8):
            if parent is None:
                break
            cls = " ".join(parent.get("class", []))
            if "accepted-solution" in cls or "AcceptedSolution" in cls:
                is_solution = True
                break
            parent = parent.parent

        solution_tag = "  ✅ ACCEPTED SOLUTION" if is_solution else ""
        label = "Original Post" if idx == 0 else f"Reply {idx}"
        body = post.get_text(separator="\n", strip=True)
        body_lines = [ln for ln in body.splitlines() if ln.strip()]
        body = "\n".join(body_lines[:60])  # cap at 60 lines per reply

        lines.append(f"**{author}**  {date_str}  ({label}){solution_tag}")
        lines.append(body)
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Silent Hyland Connect forum fetcher — no browser required",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            r"""
            Examples:
              python hyland_connect_fetch.py search "WorkView filter slow" --limit 10
              python hyland_connect_fetch.py board workview --limit 15
              python hyland_connect_fetch.py thread 497182
              python hyland_connect_fetch.py url "https://connect.hyland.com/t5/.../td-p/497182"
            """
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("search", help="Full-text search across all forums")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=10, metavar="N")
    sp.add_argument("--best-match", action="store_true", help="Sort by relevance not date")

    bp = sub.add_parser("board", help="Latest topics on a board")
    bp.add_argument("board_id", metavar="BOARD")
    bp.add_argument("--limit", type=int, default=15, metavar="N")

    tp = sub.add_parser("thread", help="Fetch a thread and all replies")
    tp.add_argument("message_id", metavar="ID")

    up = sub.add_parser("url", help="Fetch any Hyland Connect URL")
    up.add_argument("url")

    return p


def main():
    args = build_parser().parse_args()
    client = HylandConnectClient()

    if args.command == "search":
        result = client.search(args.query, limit=args.limit, sort_by_date=not args.best_match)
    elif args.command == "board":
        board_id = BOARD_ALIASES.get(args.board_id.lower(), args.board_id)
        result = client.get_board_messages(board_id, limit=args.limit)
    elif args.command == "thread":
        result = client.get_thread(args.message_id)
    elif args.command == "url":
        result = client.fetch_url(args.url)
    else:
        build_parser().print_help()
        sys.exit(1)

    print(result)


if __name__ == "__main__":
    main()
