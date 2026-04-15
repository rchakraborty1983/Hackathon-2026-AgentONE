from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security import APIKeyHeader
import base64
import requests
import json as _json
import subprocess as _subprocess
import os
import re
import difflib
import time as _time
from urllib.parse import quote
from typing import Optional, List
import threading
import logging
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger("tfsmcp")


def _warm_bd_cache(*, repeat_interval: int = 0):
    """Pre-warm BuildDirector cache in a background thread.

    Fetches the versions list + top builds for the most common versions
    so that the first user query hits cache instead of waiting 30-40s.

    If repeat_interval > 0, re-warms every repeat_interval seconds (daemon loop).
    """
    while True:
        _logger.info("BuildDirector cache warming started")
        try:
            # 1. Fetch versions list
            _bd_get_cached("OnBase/versions")
            _logger.info("  Cached: versions list")
            # 2. Pre-fetch builds for common active versions
            # Use top=50 — fast enough per call (~15s vs ~40s for top=200),
            # and covers all BOTW queries (BOTWs are weekly, 50 builds ≈ 12+ weeks).
            # Superset cache logic ensures bd_list_builds (top=10-20) also hits this.
            for ver in ["DEV", "25.2", "25.1", "24.1"]:
                try:
                    bd_ver = _resolve_bd_version(ver)
                    _bd_get_cached(f"onbase/builds/allSlim/{bd_ver}", top=50)
                    _logger.info(f"  Cached: builds for {ver} ({bd_ver})")
                except Exception as e:
                    _logger.warning(f"  Cache warm failed for {ver}: {e}")
            # 3. Pre-fetch calendar events for common versions (avoids cold PowerShell call on first BOTW query)
            for ver in ["DEV", "25.2", "25.1", "24.1"]:
                try:
                    bd_ver = _resolve_bd_version(ver)
                    vid = _bd_get_version_id(bd_ver)
                    _bd_get_cached(f"onbase/news/events/{vid}")
                    _logger.info(f"  Cached: calendar events for {ver}")
                except Exception as e:
                    _logger.warning(f"  Cache warm failed for {ver} calendar: {e}")
            _logger.info("BuildDirector cache warming complete")
        except Exception as e:
            _logger.warning(f"BuildDirector cache warming failed: {e}")
        if repeat_interval <= 0:
            break
        _time.sleep(repeat_interval)


app = FastAPI(
    title="TFS-Jira Analysis API",
    description=(
        "Fetches Jira card context and linked TFS changeset code diffs. "
        "Designed as a custom connector for Copilot Studio agents to "
        "enable AI-driven test scenario generation."
    ),
    version="2.0.0",
)

# ------------------------------------------------
# CONFIGURATION
# ------------------------------------------------

TFS_URL = "http://dev-tfs:8080/tfs/HylandCollection/_apis"
BUILD_DIRECTOR = "http://qa-websrvr/BuildDirector"
TFS_PAT = os.getenv("TFS_PAT")
TFS_AUTH = ("user", TFS_PAT)

JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "https://hyland.atlassian.net")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")

GITHUB_BASE_URL = os.getenv("GITHUB_BASE_URL", "https://api.github.com")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_ORG = os.getenv("GITHUB_ORG", "HylandFoundation")

# Well-known repo aliases within the org
GITHUB_REPO_ALIASES = {
    "workview-client": "SBP-WV-onbase-web-server-ng-apps",
    "angular-client": "SBP-WV-onbase-web-server-ng-apps",
    "ng-apps": "SBP-WV-onbase-web-server-ng-apps",
    "onbase-web": "SBP-WV-onbase-web-server-ng-apps",
    "wv-automation": "SBP-WV-wv-legacy-selenium-automation",
    "selenium": "SBP-WV-wv-legacy-selenium-automation",
    "test-automation": "SBP-WV-wv-legacy-selenium-automation",
    "api-server": "PCS-APISERVER-api-server",
    "apiserver": "PCS-APISERVER-api-server",
}

MAX_DIFF_LINES = 500
API_KEY = os.getenv("API_KEY")  # Set to enable API key auth; leave unset to disable

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(key: str = Security(api_key_header)):
    if API_KEY and key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key.")
    return key

BINARY_EXTENSIONS = {
    ".dll", ".exe", ".png", ".jpg", ".jpeg", ".gif", ".bmp",
    ".ico", ".zip", ".rar", ".7z", ".pdf", ".bin", ".obj", ".pdb",
}

# ------------------------------------------------
# HELPERS Ã¢â‚¬â€ TFS
# ------------------------------------------------

def tfs_get(url: str) -> dict:
    r = requests.get(url, auth=TFS_AUTH, timeout=30)
    r.raise_for_status()
    return r.json()


def tfs_get_text(url: str) -> str:
    r = requests.get(
        url, auth=TFS_AUTH,
        headers={"Accept": "text/plain"},
        timeout=30,
    )
    r.raise_for_status()
    return r.text

# ------------------------------------------------
# HELPERS Ã¢â‚¬â€ JIRA
# ------------------------------------------------

def jira_get(path: str) -> dict:
    if not JIRA_EMAIL or not JIRA_API_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="JIRA_EMAIL and JIRA_API_TOKEN environment variables must be set.",
        )
    url = f"{JIRA_BASE_URL}/rest/api/3/{path}"
    r = requests.get(
        url,
        auth=(JIRA_EMAIL, JIRA_API_TOKEN),
        headers={"Accept": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def jira_attach_file(issue_key: str, filepath: str) -> dict:
    """Attach a file to a Jira issue. Returns attachment metadata (id, filename, content URL)."""
    if not JIRA_EMAIL or not JIRA_API_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="JIRA_EMAIL and JIRA_API_TOKEN environment variables must be set.",
        )
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/attachments"
    basename = os.path.basename(filepath)
    with open(filepath, "rb") as fh:
        r = requests.post(
            url,
            auth=(JIRA_EMAIL, JIRA_API_TOKEN),
            headers={"X-Atlassian-Token": "no-check"},
            files={"file": (basename, fh, "text/html")},
            timeout=120,
        )
    r.raise_for_status()
    attachments = r.json()
    if attachments:
        att = attachments[0]
        return {
            "id": att.get("id"),
            "filename": att.get("filename"),
            "content_url": att.get("content"),
            "self_url": att.get("self"),
        }
    return {}


def adf_to_text(node) -> str:
    """Convert Atlassian Document Format (ADF) to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    if isinstance(node, dict):
        ntype = node.get("type", "")
        children = node.get("content", [])

        if ntype == "text":
            return node.get("text", "")
        if ntype == "hardBreak":
            return "\n"
        if ntype == "emoji":
            return node.get("attrs", {}).get("text", "")
        if ntype == "paragraph":
            return adf_to_text(children) + "\n"
        if ntype == "heading":
            return adf_to_text(children) + "\n"
        if ntype == "orderedList":
            items = []
            for i, child in enumerate(children, 1):
                inner = adf_to_text(child.get("content", [])).strip()
                items.append(f"{i}. {inner}")
            return "\n".join(items) + "\n"
        if ntype == "bulletList":
            items = []
            for child in children:
                inner = adf_to_text(child.get("content", [])).strip()
                items.append(f"- {inner}")
            return "\n".join(items) + "\n"
        if ntype == "listItem":
            return adf_to_text(children)
        # Fallback: recurse into children
        return adf_to_text(children)
    return ""

# ------------------------------------------------
# HELPERS — GITHUB
# ------------------------------------------------

def github_get(path: str, accept: str = "application/json") -> requests.Response:
    """Authenticated GET against GitHub REST API."""
    if not GITHUB_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="GITHUB_TOKEN environment variable must be set.",
        )
    url = path if path.startswith("http") else f"{GITHUB_BASE_URL}/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": f"application/vnd.github+json" if accept == "application/json" else accept,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r


def github_get_json(path: str) -> dict | list:
    return github_get(path).json()


# ------------------------------------------------
# GITHUB — REPO HELPERS
# ------------------------------------------------

def fetch_github_repo(owner: str, repo: str) -> dict:
    """Get repository metadata."""
    data = github_get_json(f"repos/{owner}/{repo}")
    return {
        "full_name": data.get("full_name", ""),
        "description": data.get("description", ""),
        "default_branch": data.get("default_branch", ""),
        "language": data.get("language", ""),
        "private": data.get("private", False),
        "html_url": data.get("html_url", ""),
        "created_at": data.get("created_at", ""),
        "updated_at": data.get("updated_at", ""),
        "open_issues_count": data.get("open_issues_count", 0),
        "forks_count": data.get("forks_count", 0),
        "stargazers_count": data.get("stargazers_count", 0),
        "topics": data.get("topics", []),
    }


def list_github_pulls(
    owner: str,
    repo: str,
    state: str = "open",
    sort: str = "updated",
    direction: str = "desc",
    per_page: int = 30,
) -> list[dict]:
    """List pull requests for a repository."""
    data = github_get_json(
        f"repos/{owner}/{repo}/pulls"
        f"?state={state}&sort={sort}&direction={direction}&per_page={per_page}"
    )
    pulls = []
    for pr in data:
        pulls.append(_format_pr_summary(pr))
    return pulls


def _format_pr_summary(pr: dict) -> dict:
    """Format a PR object into a clean summary dict."""
    return {
        "number": pr.get("number"),
        "title": pr.get("title", ""),
        "state": pr.get("state", ""),
        "user": pr.get("user", {}).get("login", ""),
        "created_at": pr.get("created_at", ""),
        "updated_at": pr.get("updated_at", ""),
        "merged_at": pr.get("merged_at"),
        "draft": pr.get("draft", False),
        "head_branch": pr.get("head", {}).get("ref", ""),
        "base_branch": pr.get("base", {}).get("ref", ""),
        "html_url": pr.get("html_url", ""),
        "labels": [l.get("name", "") for l in pr.get("labels", [])],
    }


def fetch_github_pr(owner: str, repo: str, pr_number: int) -> dict:
    """Get full PR details including body and merge status."""
    pr = github_get_json(f"repos/{owner}/{repo}/pulls/{pr_number}")
    result = _format_pr_summary(pr)
    result.update({
        "body": pr.get("body", "") or "",
        "mergeable": pr.get("mergeable"),
        "mergeable_state": pr.get("mergeable_state", ""),
        "merged": pr.get("merged", False),
        "merged_by": (pr.get("merged_by") or {}).get("login", ""),
        "commits": pr.get("commits", 0),
        "additions": pr.get("additions", 0),
        "deletions": pr.get("deletions", 0),
        "changed_files": pr.get("changed_files", 0),
        "review_comments": pr.get("review_comments", 0),
        "comments": pr.get("comments", 0),
        "head_sha": pr.get("head", {}).get("sha", ""),
        "base_sha": pr.get("base", {}).get("sha", ""),
    })
    return result


def fetch_github_pr_files(owner: str, repo: str, pr_number: int) -> list[dict]:
    """Get the list of files changed in a PR with patch diffs."""
    data = github_get_json(f"repos/{owner}/{repo}/pulls/{pr_number}/files?per_page=100")
    files = []
    for f in data:
        patch = f.get("patch", "")
        if len(patch.splitlines()) > MAX_DIFF_LINES:
            patch_lines = patch.splitlines()[:MAX_DIFF_LINES]
            patch = "\n".join(patch_lines) + f"\n... [truncated - first {MAX_DIFF_LINES} lines shown]"
        files.append({
            "filename": f.get("filename", ""),
            "status": f.get("status", ""),
            "additions": f.get("additions", 0),
            "deletions": f.get("deletions", 0),
            "changes": f.get("changes", 0),
            "patch": patch,
        })
    return files


def fetch_github_pr_with_diffs(owner: str, repo: str, pr_number: int) -> dict:
    """Get PR details plus all file diffs — analogous to changeset_with_diffs."""
    pr_detail = fetch_github_pr(owner, repo, pr_number)
    file_diffs = fetch_github_pr_files(owner, repo, pr_number)
    return {
        **pr_detail,
        "file_diffs": file_diffs,
        "total_files_changed": len(file_diffs),
        "total_lines_added": sum(f["additions"] for f in file_diffs),
        "total_lines_removed": sum(f["deletions"] for f in file_diffs),
    }


def fetch_github_pr_reviews(owner: str, repo: str, pr_number: int) -> list[dict]:
    """Get reviews on a PR."""
    data = github_get_json(f"repos/{owner}/{repo}/pulls/{pr_number}/reviews")
    reviews = []
    for r in data:
        reviews.append({
            "user": r.get("user", {}).get("login", ""),
            "state": r.get("state", ""),
            "body": r.get("body", ""),
            "submitted_at": r.get("submitted_at", ""),
        })
    return reviews


def fetch_github_file_content(
    owner: str, repo: str, path: str, ref: str = None,
) -> dict:
    """Get file content from a GitHub repository."""
    url = f"repos/{owner}/{repo}/contents/{path}"
    if ref:
        url += f"?ref={ref}"
    data = github_get_json(url)
    content = ""
    if data.get("encoding") == "base64" and data.get("content"):
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    return {
        "path": data.get("path", ""),
        "name": data.get("name", ""),
        "size": data.get("size", 0),
        "sha": data.get("sha", ""),
        "content": content,
        "html_url": data.get("html_url", ""),
    }


def search_github_prs(
    owner: str,
    repo: str,
    query: str,
    per_page: int = 20,
) -> list[dict]:
    """Search PRs in a repo using GitHub search API."""
    search_q = f"{query} repo:{owner}/{repo} is:pr"
    data = github_get_json(f"search/issues?q={quote(search_q)}&per_page={per_page}")
    results = []
    for item in data.get("items", []):
        results.append({
            "number": item.get("number"),
            "title": item.get("title", ""),
            "state": item.get("state", ""),
            "user": item.get("user", {}).get("login", ""),
            "created_at": item.get("created_at", ""),
            "updated_at": item.get("updated_at", ""),
            "html_url": item.get("html_url", ""),
            "labels": [l.get("name", "") for l in item.get("labels", [])],
        })
    return results


def resolve_github_repo(name_or_alias: str) -> tuple[str, str]:
    """Resolve a repo alias or short name to (owner, repo). Defaults to GITHUB_ORG."""
    name_or_alias = name_or_alias.strip().rstrip("/")
    if "/" in name_or_alias:
        parts = name_or_alias.split("/", 1)
        return parts[0], parts[1]
    resolved = GITHUB_REPO_ALIASES.get(name_or_alias.lower(), name_or_alias)
    return GITHUB_ORG, resolved


# ------------------------------------------------
# GITHUB — ORG HELPERS
# ------------------------------------------------

def list_github_org_repos(
    org: str = None,
    per_page: int = 30,
    sort: str = "updated",
    repo_type: str = "all",
) -> list[dict]:
    """List repositories in a GitHub organization."""
    org = org or GITHUB_ORG
    data = github_get_json(
        f"orgs/{org}/repos?sort={sort}&type={repo_type}&per_page={per_page}"
    )
    repos = []
    for r in data:
        repos.append({
            "name": r.get("name", ""),
            "full_name": r.get("full_name", ""),
            "description": r.get("description", ""),
            "language": r.get("language", ""),
            "private": r.get("private", False),
            "html_url": r.get("html_url", ""),
            "default_branch": r.get("default_branch", ""),
            "updated_at": r.get("updated_at", ""),
            "open_issues_count": r.get("open_issues_count", 0),
        })
    return repos


# ------------------------------------------------
# GITHUB — CODE SCANNING ALERTS
# ------------------------------------------------

def list_code_scanning_alerts(
    owner: str,
    repo: str,
    state: str = "open",
    severity: str = None,
    per_page: int = 30,
) -> list[dict]:
    """List code scanning alerts for a repository."""
    url = f"repos/{owner}/{repo}/code-scanning/alerts?state={state}&per_page={per_page}"
    if severity:
        url += f"&severity={severity}"
    try:
        data = github_get_json(url)
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return []
        raise
    alerts = []
    for a in data:
        rule = a.get("rule", {})
        tool = a.get("tool", {})
        most_recent = a.get("most_recent_instance", {})
        alerts.append({
            "number": a.get("number"),
            "state": a.get("state", ""),
            "rule_id": rule.get("id", ""),
            "rule_description": rule.get("description", ""),
            "rule_severity": rule.get("severity", ""),
            "rule_security_severity_level": rule.get("security_severity_level", ""),
            "tool_name": tool.get("name", ""),
            "tool_version": tool.get("version", ""),
            "html_url": a.get("html_url", ""),
            "created_at": a.get("created_at", ""),
            "updated_at": a.get("updated_at", ""),
            "dismissed_by": (a.get("dismissed_by") or {}).get("login", ""),
            "dismissed_reason": a.get("dismissed_reason", ""),
            "location_path": most_recent.get("location", {}).get("path", ""),
            "location_start_line": most_recent.get("location", {}).get("start_line"),
            "location_end_line": most_recent.get("location", {}).get("end_line"),
            "message": most_recent.get("message", {}).get("text", ""),
        })
    return alerts


def get_code_scanning_alert(
    owner: str, repo: str, alert_number: int,
) -> dict:
    """Get a single code scanning alert with full details."""
    a = github_get_json(f"repos/{owner}/{repo}/code-scanning/alerts/{alert_number}")
    rule = a.get("rule", {})
    tool = a.get("tool", {})
    most_recent = a.get("most_recent_instance", {})
    return {
        "number": a.get("number"),
        "state": a.get("state", ""),
        "rule_id": rule.get("id", ""),
        "rule_description": rule.get("description", ""),
        "rule_full_description": rule.get("full_description", ""),
        "rule_severity": rule.get("severity", ""),
        "rule_security_severity_level": rule.get("security_severity_level", ""),
        "rule_tags": rule.get("tags", []),
        "tool_name": tool.get("name", ""),
        "tool_version": tool.get("version", ""),
        "html_url": a.get("html_url", ""),
        "created_at": a.get("created_at", ""),
        "updated_at": a.get("updated_at", ""),
        "fixed_at": a.get("fixed_at"),
        "dismissed_by": (a.get("dismissed_by") or {}).get("login", ""),
        "dismissed_reason": a.get("dismissed_reason", ""),
        "dismissed_comment": a.get("dismissed_comment", ""),
        "location_path": most_recent.get("location", {}).get("path", ""),
        "location_start_line": most_recent.get("location", {}).get("start_line"),
        "location_end_line": most_recent.get("location", {}).get("end_line"),
        "message": most_recent.get("message", {}).get("text", ""),
        "ref": most_recent.get("ref", ""),
        "environment": most_recent.get("environment", ""),
        "category": most_recent.get("category", ""),
    }


# ------------------------------------------------
# GITHUB — DEPENDABOT ALERTS
# ------------------------------------------------

def list_dependabot_alerts(
    owner: str,
    repo: str,
    state: str = "open",
    severity: str = None,
    per_page: int = 30,
) -> list[dict]:
    """List Dependabot alerts for a repository."""
    url = f"repos/{owner}/{repo}/dependabot/alerts?state={state}&per_page={per_page}"
    if severity:
        url += f"&severity={severity}"
    try:
        data = github_get_json(url)
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return []
        raise
    alerts = []
    for a in data:
        vuln = a.get("security_vulnerability", {}) or {}
        advisory = a.get("security_advisory", {}) or {}
        pkg = vuln.get("package", {})
        alerts.append({
            "number": a.get("number"),
            "state": a.get("state", ""),
            "dependency_package": pkg.get("name", ""),
            "dependency_ecosystem": pkg.get("ecosystem", ""),
            "dependency_manifest": a.get("dependency", {}).get("manifest_path", ""),
            "severity": vuln.get("severity", ""),
            "vulnerable_version_range": vuln.get("vulnerable_version_range", ""),
            "first_patched_version": (vuln.get("first_patched_version") or {}).get("identifier", ""),
            "advisory_ghsa_id": advisory.get("ghsa_id", ""),
            "advisory_cve_id": advisory.get("cve_id", ""),
            "advisory_summary": advisory.get("summary", ""),
            "advisory_description": advisory.get("description", "")[:500],
            "advisory_severity": advisory.get("severity", ""),
            "html_url": a.get("html_url", ""),
            "created_at": a.get("created_at", ""),
            "updated_at": a.get("updated_at", ""),
            "dismissed_by": (a.get("dismissed_by") or {}).get("login", ""),
            "dismissed_reason": a.get("dismissed_reason", ""),
            "auto_dismissed_at": a.get("auto_dismissed_at"),
            "fixed_at": a.get("fixed_at"),
        })
    return alerts


def get_dependabot_alert(
    owner: str, repo: str, alert_number: int,
) -> dict:
    """Get a single Dependabot alert with full details."""
    a = github_get_json(f"repos/{owner}/{repo}/dependabot/alerts/{alert_number}")
    vuln = a.get("security_vulnerability", {}) or {}
    advisory = a.get("security_advisory", {}) or {}
    pkg = vuln.get("package", {})
    cwes = advisory.get("cwes", [])
    return {
        "number": a.get("number"),
        "state": a.get("state", ""),
        "dependency_package": pkg.get("name", ""),
        "dependency_ecosystem": pkg.get("ecosystem", ""),
        "dependency_manifest": a.get("dependency", {}).get("manifest_path", ""),
        "dependency_scope": a.get("dependency", {}).get("scope", ""),
        "severity": vuln.get("severity", ""),
        "vulnerable_version_range": vuln.get("vulnerable_version_range", ""),
        "first_patched_version": (vuln.get("first_patched_version") or {}).get("identifier", ""),
        "advisory_ghsa_id": advisory.get("ghsa_id", ""),
        "advisory_cve_id": advisory.get("cve_id", ""),
        "advisory_summary": advisory.get("summary", ""),
        "advisory_description": advisory.get("description", ""),
        "advisory_severity": advisory.get("severity", ""),
        "advisory_cvss_score": advisory.get("cvss", {}).get("score"),
        "advisory_cwes": [{"cwe_id": c.get("cwe_id", ""), "name": c.get("name", "")} for c in cwes],
        "advisory_references": [ref.get("url", "") for ref in advisory.get("references", [])],
        "html_url": a.get("html_url", ""),
        "created_at": a.get("created_at", ""),
        "updated_at": a.get("updated_at", ""),
        "dismissed_by": (a.get("dismissed_by") or {}).get("login", ""),
        "dismissed_reason": a.get("dismissed_reason", ""),
        "dismissed_comment": a.get("dismissed_comment", ""),
        "fixed_at": a.get("fixed_at"),
    }


# ------------------------------------------------
# GITHUB — PR REVIEW COMMENTS
# ------------------------------------------------

def fetch_github_pr_review_comments(
    owner: str, repo: str, pr_number: int,
) -> list[dict]:
    """Get all inline review comments on a PR (code-level comments)."""
    data = github_get_json(
        f"repos/{owner}/{repo}/pulls/{pr_number}/comments?per_page=100"
    )
    comments = []
    for c in data:
        comments.append({
            "id": c.get("id"),
            "user": c.get("user", {}).get("login", ""),
            "body": c.get("body", ""),
            "path": c.get("path", ""),
            "line": c.get("line"),
            "original_line": c.get("original_line"),
            "side": c.get("side", ""),
            "diff_hunk": c.get("diff_hunk", ""),
            "created_at": c.get("created_at", ""),
            "updated_at": c.get("updated_at", ""),
            "html_url": c.get("html_url", ""),
            "in_reply_to_id": c.get("in_reply_to_id"),
        })
    return comments


# ------------------------------------------------
# GITHUB — REPO SEARCH IN ORG
# ------------------------------------------------

def search_github_repos(
    query: str,
    org: str = None,
    per_page: int = 20,
) -> list[dict]:
    """Search repositories in an organization."""
    org = org or GITHUB_ORG
    search_q = f"{query} org:{org}"
    data = github_get_json(f"search/repositories?q={quote(search_q)}&per_page={per_page}")
    results = []
    for r in data.get("items", []):
        results.append({
            "name": r.get("name", ""),
            "full_name": r.get("full_name", ""),
            "description": r.get("description", ""),
            "language": r.get("language", ""),
            "private": r.get("private", False),
            "html_url": r.get("html_url", ""),
            "default_branch": r.get("default_branch", ""),
            "updated_at": r.get("updated_at", ""),
            "stargazers_count": r.get("stargazers_count", 0),
            "open_issues_count": r.get("open_issues_count", 0),
            "topics": r.get("topics", []),
        })
    return results


# ------------------------------------------------
# HELPERS — GITHUB WRITE
# ------------------------------------------------

def _github_headers() -> dict:
    """Common headers for GitHub REST API calls."""
    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="GITHUB_TOKEN environment variable must be set.")
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def github_post(path: str, payload: dict) -> dict:
    """Authenticated POST against GitHub REST API. Returns JSON response."""
    url = path if path.startswith("http") else f"{GITHUB_BASE_URL}/{path.lstrip('/')}"
    r = requests.post(url, headers=_github_headers(), json=payload, timeout=30)
    if not r.ok:
        # Capture GitHub's detailed error message
        try:
            detail = r.json()
        except Exception:
            detail = r.text[:500]
        raise requests.exceptions.HTTPError(
            f"{r.status_code} {r.reason}: {detail}",
            response=r,
        )
    return r.json()


def github_put(path: str, payload: dict) -> dict:
    """Authenticated PUT against GitHub REST API. Returns JSON response."""
    url = path if path.startswith("http") else f"{GITHUB_BASE_URL}/{path.lstrip('/')}"
    r = requests.put(url, headers=_github_headers(), json=payload, timeout=30)
    if not r.ok:
        try:
            detail = r.json()
        except Exception:
            detail = r.text[:500]
        raise requests.exceptions.HTTPError(
            f"{r.status_code} {r.reason}: {detail}",
            response=r,
        )
    return r.json()


# ------------------------------------------------
# GITHUB — WRITE OPERATIONS
# ------------------------------------------------

def create_github_branch(
    owner: str,
    repo: str,
    branch: str,
    from_branch: str = "",
) -> dict:
    """Create a new branch in a GitHub repository.

    Args:
        owner: Repository owner (org or user).
        repo: Repository name.
        branch: Name of the new branch to create (e.g. 'feature/my-fix').
        from_branch: Source branch to branch from. If empty, uses the repo default branch.

    Returns:
        dict with ref, sha, and url of the created branch.
    """
    # Resolve source branch
    if not from_branch:
        repo_info = github_get_json(f"repos/{owner}/{repo}")
        from_branch = repo_info.get("default_branch", "main")

    # Get the SHA of the source branch
    ref_data = github_get_json(f"repos/{owner}/{repo}/git/ref/heads/{quote(from_branch, safe='')}")
    source_sha = ref_data["object"]["sha"]

    # Create the new branch ref
    try:
        result = github_post(f"repos/{owner}/{repo}/git/refs", {
            "ref": f"refs/heads/{branch}",
            "sha": source_sha,
        })
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 422:
            # Branch already exists — return success-like response
            return {
                "ref": f"refs/heads/{branch}",
                "sha": source_sha,
                "url": f"https://github.com/{owner}/{repo}/tree/{branch}",
                "from_branch": from_branch,
                "from_sha": source_sha,
                "already_exists": True,
                "message": f"Branch '{branch}' already exists. You can proceed to push files to it.",
            }
        raise
    return {
        "ref": result.get("ref", ""),
        "sha": result["object"]["sha"],
        "url": f"https://github.com/{owner}/{repo}/tree/{branch}",
        "from_branch": from_branch,
        "from_sha": source_sha,
    }


def create_or_update_github_file(
    owner: str,
    repo: str,
    path: str,
    content: str,
    message: str,
    branch: str,
    sha: str = "",
) -> dict:
    """Create or update a single file in a GitHub repository.

    Args:
        owner: Repository owner.
        repo: Repository name.
        path: File path within the repo (e.g. 'src/app/config.ts').
        content: File content (plain text — will be base64-encoded automatically).
        message: Commit message.
        branch: Branch to commit to.
        sha: SHA of the file being replaced (required for updates, omit for new files).

    Returns:
        dict with commit sha, file path, and html_url.
    """
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    try:
        result = github_put(f"repos/{owner}/{repo}/contents/{path}", payload)
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 422 and not sha:
            # File probably already exists — try to auto-fetch current SHA and retry
            try:
                existing = github_get_json(f"repos/{owner}/{repo}/contents/{path}?ref={quote(branch, safe='')}")
                existing_sha = existing.get("sha", "")
                if existing_sha:
                    payload["sha"] = existing_sha
                    result = github_put(f"repos/{owner}/{repo}/contents/{path}", payload)
                else:
                    raise
            except requests.exceptions.HTTPError:
                raise e  # Re-raise original error if retry also fails
        else:
            raise
    commit_info = result.get("commit", {})
    return {
        "path": result.get("content", {}).get("path", path),
        "sha": result.get("content", {}).get("sha", ""),
        "commit_sha": commit_info.get("sha", ""),
        "commit_message": commit_info.get("message", ""),
        "html_url": result.get("content", {}).get("html_url", ""),
    }


def push_github_files(
    owner: str,
    repo: str,
    branch: str,
    files: list[dict],
    message: str,
) -> dict:
    """Push multiple files in a single commit using the Git trees API.

    Args:
        owner: Repository owner.
        repo: Repository name.
        branch: Target branch.
        files: List of dicts, each with 'path' (str) and 'content' (str).
        message: Commit message.

    Returns:
        dict with commit sha, tree sha, and html_url.
    """
    # 1. Get the latest commit SHA on the branch
    ref_data = github_get_json(f"repos/{owner}/{repo}/git/ref/heads/{quote(branch, safe='')}")
    latest_sha = ref_data["object"]["sha"]

    # 2. Get the tree SHA of the latest commit
    commit_data = github_get_json(f"repos/{owner}/{repo}/git/commits/{latest_sha}")
    base_tree_sha = commit_data["tree"]["sha"]

    # 3. Create blobs for each file
    tree_items = []
    for f in files:
        blob = github_post(f"repos/{owner}/{repo}/git/blobs", {
            "content": f["content"],
            "encoding": "utf-8",
        })
        tree_items.append({
            "path": f["path"],
            "mode": "100644",
            "type": "blob",
            "sha": blob["sha"],
        })

    # 4. Create a new tree
    new_tree = github_post(f"repos/{owner}/{repo}/git/trees", {
        "base_tree": base_tree_sha,
        "tree": tree_items,
    })

    # 5. Create a new commit
    new_commit = github_post(f"repos/{owner}/{repo}/git/commits", {
        "message": message,
        "tree": new_tree["sha"],
        "parents": [latest_sha],
    })

    # 6. Update the branch reference
    url = f"repos/{owner}/{repo}/git/refs/heads/{quote(branch, safe='')}"
    patch_url = url if url.startswith("http") else f"{GITHUB_BASE_URL}/{url}"
    r = requests.patch(patch_url, headers=_github_headers(), json={"sha": new_commit["sha"]}, timeout=30)
    r.raise_for_status()

    return {
        "commit_sha": new_commit["sha"],
        "tree_sha": new_tree["sha"],
        "files_pushed": len(files),
        "html_url": f"https://github.com/{owner}/{repo}/commit/{new_commit['sha']}",
    }


def create_github_pull_request(
    owner: str,
    repo: str,
    title: str,
    head: str,
    base: str = "",
    body: str = "",
    draft: bool = False,
) -> dict:
    """Create a pull request in a GitHub repository.

    Args:
        owner: Repository owner.
        repo: Repository name.
        title: PR title.
        head: Branch with changes (e.g. 'feature/my-fix').
        base: Target branch to merge into. If empty, uses the repo default branch.
        body: PR description (markdown).
        draft: Whether to create as draft PR.

    Returns:
        dict with PR number, url, title, state, and head/base refs.
    """
    if not base:
        repo_info = github_get_json(f"repos/{owner}/{repo}")
        base = repo_info.get("default_branch", "main")

    try:
        result = github_post(f"repos/{owner}/{repo}/pulls", {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "draft": draft,
        })
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 422:
            # Check if PR already exists
            try:
                err_body = e.response.json() if hasattr(e.response, 'json') else {}
            except Exception:
                err_body = {}
            errors = err_body.get("errors", [])
            for err in errors:
                if "pull request already exists" in str(err.get("message", "")).lower():
                    # Find existing PR
                    existing = github_get_json(
                        f"repos/{owner}/{repo}/pulls?head={owner}:{head}&base={base}&state=open"
                    )
                    if existing:
                        pr = existing[0]
                        return {
                            "number": pr.get("number"),
                            "html_url": pr.get("html_url", ""),
                            "title": pr.get("title", ""),
                            "state": pr.get("state", ""),
                            "head": pr.get("head", {}).get("ref", ""),
                            "base": pr.get("base", {}).get("ref", ""),
                            "draft": pr.get("draft", False),
                            "created_at": pr.get("created_at", ""),
                            "already_exists": True,
                            "message": "A pull request already exists for this branch.",
                        }
            # Re-raise with better message if it's a different 422 error
            raise
        raise
    return {
        "number": result.get("number"),
        "html_url": result.get("html_url", ""),
        "title": result.get("title", ""),
        "state": result.get("state", ""),
        "head": result.get("head", {}).get("ref", ""),
        "base": result.get("base", {}).get("ref", ""),
        "draft": result.get("draft", False),
        "created_at": result.get("created_at", ""),
    }


# ------------------------------------------------
# JIRA CONTEXT FETCHING
# ------------------------------------------------

def fetch_jira_context(jira_key: str) -> dict:
    issue = jira_get(f"issue/{jira_key}")
    fields = issue.get("fields", {})

    summary = fields.get("summary", "")
    description = adf_to_text(fields.get("description"))
    status = fields.get("status", {}).get("name", "")
    issue_type = fields.get("issuetype", {}).get("name", "")
    priority = fields.get("priority", {}).get("name", "")
    assignee_obj = fields.get("assignee")
    assignee = assignee_obj.get("displayName", "") if assignee_obj else ""

    repro_steps = adf_to_text(fields.get("customfield_10830"))
    testing_recommendations = adf_to_text(fields.get("customfield_11816"))

    parent = fields.get("parent")
    parent_summary = (
        parent.get("fields", {}).get("summary", "") if parent else ""
    )

    linked_issues = []
    for link in fields.get("issuelinks", []):
        direction = "outward" if "outwardIssue" in link else "inward"
        linked = link.get(f"{direction}Issue", {})
        linked_issues.append({
            "key": linked.get("key", ""),
            "summary": linked.get("fields", {}).get("summary", ""),
            "relationship": link.get("type", {}).get(direction, ""),
            "status": linked.get("fields", {}).get("status", {}).get("name", ""),
        })

    return {
        "jira_key": jira_key,
        "summary": summary,
        "description": description.strip(),
        "status": status,
        "issue_type": issue_type,
        "priority": priority,
        "assignee": assignee,
        "parent_epic": parent_summary,
        "repro_steps": repro_steps.strip(),
        "testing_recommendations": testing_recommendations.strip(),
        "linked_issues": linked_issues,
    }

# ------------------------------------------------
# EXTRACT TFS CHANGESET IDS FROM JIRA REMOTE LINKS
# ------------------------------------------------

def extract_changeset_ids_from_jira(jira_key: str) -> list[dict]:
    links = jira_get(f"issue/{jira_key}/remotelink")
    changesets = []
    for link in links:
        if link.get("relationship") != "Tfvc Checkin":
            continue
        obj = link.get("object", {})
        url = obj.get("url", "")
        match = re.search(r"/changeset/(\d+)", url)
        if match:
            changesets.append({
                "id": int(match.group(1)),
                "summary": obj.get("summary", ""),
            })
    return changesets

# ------------------------------------------------
# TFS FILE CONTENT & DIFF
# ------------------------------------------------

def get_file_content(path: str, changeset_id: int) -> Optional[str]:
    encoded = quote(path, safe="/$")
    url = (
        f"{TFS_URL}/tfvc/items"
        f"?path={encoded}"
        f"&versionDescriptor.version={changeset_id}"
        f"&versionDescriptor.versionType=Changeset"
        f"&api-version=5.0"
    )
    try:
        return tfs_get_text(url)
    except requests.exceptions.HTTPError:
        return None


def compute_file_diff(path: str, changeset_id: int, change_type: str) -> dict:
    ext = os.path.splitext(path)[1].lower()
    if ext in BINARY_EXTENSIONS:
        return {
            "path": path,
            "change_type": change_type,
            "diff": "[Binary file - diff not available]",
            "lines_added": 0,
            "lines_removed": 0,
        }

    if "add" in change_type.lower():
        current = get_file_content(path, changeset_id) or ""
        previous = ""
    elif "delete" in change_type.lower():
        current = ""
        previous = get_file_content(path, changeset_id - 1) or ""
    else:
        current = get_file_content(path, changeset_id) or ""
        previous = get_file_content(path, changeset_id - 1) or ""

    diff_lines = list(difflib.unified_diff(
        previous.splitlines(keepends=True),
        current.splitlines(keepends=True),
        fromfile=f"a{path}",
        tofile=f"b{path}",
        n=3,
    ))

    added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))

    truncated = len(diff_lines) > MAX_DIFF_LINES
    if truncated:
        diff_lines = diff_lines[:MAX_DIFF_LINES]

    diff_text = "".join(diff_lines)
    if truncated:
        diff_text += f"\n... [truncated - first {MAX_DIFF_LINES} lines shown]"

    return {
        "path": path,
        "change_type": change_type,
        "diff": diff_text,
        "lines_added": added,
        "lines_removed": removed,
    }

# ------------------------------------------------
# FULL CHANGESET WITH DIFFS
# ------------------------------------------------

def fetch_changeset_with_diffs(changeset_id: int) -> dict:
    cs_data = tfs_get(
        f"{TFS_URL}/tfvc/changesets/{changeset_id}?api-version=5.0"
    )
    changes_data = tfs_get(
        f"{TFS_URL}/tfvc/changesets/{changeset_id}/changes?api-version=5.0"
    )

    file_diffs = []
    for change in changes_data.get("value", []):
        item = change.get("item", {})
        if item.get("isFolder", False):
            continue
        diff = compute_file_diff(
            item.get("path", ""),
            changeset_id,
            change.get("changeType", ""),
        )
        file_diffs.append(diff)

    return {
        "changeset_id": changeset_id,
        "comment": cs_data.get("comment", ""),
        "author": cs_data.get("author", {}).get("displayName", ""),
        "date": cs_data.get("createdDate", ""),
        "file_diffs": file_diffs,
        "total_files_changed": len(file_diffs),
        "total_lines_added": sum(f["lines_added"] for f in file_diffs),
        "total_lines_removed": sum(f["lines_removed"] for f in file_diffs),
    }


def fetch_changeset_summary(changeset_id: int) -> dict:
    """Get changeset metadata and file list WITHOUT diffs (fast — no diff computation)."""
    cs_data = tfs_get(
        f"{TFS_URL}/tfvc/changesets/{changeset_id}?api-version=5.0"
    )
    changes_data = tfs_get(
        f"{TFS_URL}/tfvc/changesets/{changeset_id}/changes?api-version=5.0"
    )

    files_changed = []
    for change in changes_data.get("value", []):
        item = change.get("item", {})
        if item.get("isFolder", False):
            continue
        files_changed.append({
            "path": item.get("path", ""),
            "change_type": change.get("changeType", ""),
        })

    return {
        "changeset_id": changeset_id,
        "comment": cs_data.get("comment", ""),
        "author": cs_data.get("author", {}).get("displayName", ""),
        "date": cs_data.get("createdDate", ""),
        "files_changed": files_changed,
        "total_files_changed": len(files_changed),
    }

# ------------------------------------------------
# HELPERS Ã¢â‚¬â€ TFS SHELVESETS
# ------------------------------------------------

def search_shelvesets(
    name: str = None, owner: str = None, top: int = 50,
) -> list[dict]:
    """Search TFS shelvesets by exact name and/or owner."""
    params = {"api-version": "5.0", "$top": str(top)}
    if owner:
        params["requestData.owner"] = owner
    if name:
        params["requestData.name"] = name
    params["requestData.maxCommentLength"] = "256"

    url = f"{TFS_URL}/tfvc/shelvesets"
    r = requests.get(url, params=params, auth=TFS_AUTH, timeout=10)
    r.raise_for_status()
    data = r.json()

    shelvesets = []
    for ss in data.get("value", []):
        shelvesets.append({
            "name": ss.get("name", ""),
            "owner": ss.get("owner", {}).get("displayName", ""),
            "owner_unique_name": ss.get("owner", {}).get("uniqueName", ""),
            "date": ss.get("createdDate", ""),
            "comment": ss.get("comment", ""),
        })
    return shelvesets


def find_shelvesets_by_jira_key(jira_key: str) -> list[dict]:
    """Find shelvesets whose name matches the given Jira key.

    TFS only supports exact name matching. Bulk listing (without name filter)
    is too slow (>15s) for the tool timeout budget. If no exact match is found,
    return a helpful message instead of falling back to a slow listing.
    """
    exact = search_shelvesets(name=jira_key)
    if exact:
        return exact
    return [{"message": f"No shelveset named '{jira_key}' found. "
             "TFS only supports exact name matching. "
             "Try providing the exact shelveset name or owner."}]


def get_shelveset_changes(shelveset_name: str, owner: str) -> list[dict]:
    """Get the list of changed files in a shelveset."""
    shelveset_id = f"{shelveset_name};{owner}"
    url = (
        f"{TFS_URL}/tfvc/shelvesets/changes"
        f"?shelvesetId={quote(shelveset_id, safe='')}"
        f"&api-version=5.0"
    )
    data = tfs_get(url)
    return data.get("value", [])


def fetch_shelveset_summary(shelveset_name: str, owner: str) -> dict:
    """Get shelveset metadata and file list WITHOUT diffs (fast — no diff computation).

    Analogous to fetch_changeset_summary: returns owner, date, comment,
    linked work items, and the list of changed file paths + change types.
    """
    shelveset_id = f"{shelveset_name};{owner}"
    ss_resp = tfs_get(
        f"{TFS_URL}/tfvc/shelvesets"
        f"?requestData.name={quote(shelveset_name, safe='')}"
        f"&requestData.owner={quote(owner, safe='')}"
        f"&requestData.includeDetails=true"
        f"&requestData.includeWorkItems=true"
        f"&api-version=5.0"
    )
    ss_list = ss_resp.get("value", [])
    ss_data = ss_list[0] if ss_list else ss_resp

    changes = get_shelveset_changes(shelveset_name, owner)

    files_changed = []
    for change in changes:
        item = change.get("item", {})
        if item.get("isFolder", False):
            continue
        files_changed.append({
            "path": item.get("path", ""),
            "change_type": change.get("changeType", ""),
        })

    work_items = []
    for wi in ss_data.get("workItems", []):
        work_items.append({
            "id": wi.get("webUrl", "").rsplit("/", 1)[-1] if wi.get("webUrl") else "",
            "title": wi.get("title", ""),
            "url": wi.get("webUrl", ""),
        })

    return {
        "shelveset_name": shelveset_name,
        "owner": ss_data.get("owner", {}).get("displayName", ""),
        "owner_unique_name": ss_data.get("owner", {}).get("uniqueName", ""),
        "date": ss_data.get("createdDate", ""),
        "comment": ss_data.get("comment", ""),
        "work_items": work_items,
        "files_changed": files_changed,
        "total_files_changed": len(files_changed),
    }


def get_shelveset_file_content(
    path: str, shelveset_name: str, owner: str,
) -> Optional[str]:
    """Get file content from a shelveset version."""
    encoded_path = quote(path, safe="/$")
    shelveset_id = f"{shelveset_name};{owner}"
    url = (
        f"{TFS_URL}/tfvc/items"
        f"?path={encoded_path}"
        f"&versionDescriptor.versionType=Shelveset"
        f"&versionDescriptor.version={quote(shelveset_id, safe='')}"
        f"&api-version=5.0"
    )
    try:
        return tfs_get_text(url)
    except requests.exceptions.HTTPError:
        return None


def get_latest_file_content(path: str) -> Optional[str]:
    """Get the latest committed version of a file from TFVC."""
    encoded_path = quote(path, safe="/$")
    url = (
        f"{TFS_URL}/tfvc/items"
        f"?path={encoded_path}"
        f"&versionDescriptor.versionType=Latest"
        f"&api-version=5.0"
    )
    try:
        return tfs_get_text(url)
    except requests.exceptions.HTTPError:
        return None


def compute_shelveset_file_diff(
    path: str, change_type: str, shelveset_name: str, owner: str,
) -> dict:
    """Compute diff for a file in a shelveset against its latest TFVC version."""
    ext = os.path.splitext(path)[1].lower()
    if ext in BINARY_EXTENSIONS:
        return {
            "path": path,
            "change_type": change_type,
            "diff": "[Binary file - diff not available]",
            "lines_added": 0,
            "lines_removed": 0,
        }

    if "add" in change_type.lower():
        current = get_shelveset_file_content(path, shelveset_name, owner) or ""
        previous = ""
    elif "delete" in change_type.lower():
        current = ""
        previous = get_latest_file_content(path) or ""
    else:
        current = get_shelveset_file_content(path, shelveset_name, owner) or ""
        previous = get_latest_file_content(path) or ""

    diff_lines = list(difflib.unified_diff(
        previous.splitlines(keepends=True),
        current.splitlines(keepends=True),
        fromfile=f"a{path}",
        tofile=f"b{path}",
        n=3,
    ))

    added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))

    truncated = len(diff_lines) > MAX_DIFF_LINES
    if truncated:
        diff_lines = diff_lines[:MAX_DIFF_LINES]

    diff_text = "".join(diff_lines)
    if truncated:
        diff_text += f"\n... [truncated - first {MAX_DIFF_LINES} lines shown]"

    return {
        "path": path,
        "change_type": change_type,
        "diff": diff_text,
        "lines_added": added,
        "lines_removed": removed,
    }


def fetch_shelveset_with_diffs(shelveset_name: str, owner: str) -> dict:
    """Fetch full shelveset details including file diffs."""
    shelveset_id = f"{shelveset_name};{owner}"
    ss_resp = tfs_get(
        f"{TFS_URL}/tfvc/shelvesets"
        f"?requestData.name={quote(shelveset_name, safe='')}"
        f"&requestData.owner={quote(owner, safe='')}"
        f"&requestData.includeDetails=true"
        f"&requestData.includeWorkItems=true"
        f"&api-version=5.0"
    )
    # List endpoint returns {value: [...]}, pick the first match
    ss_list = ss_resp.get("value", [])
    ss_data = ss_list[0] if ss_list else ss_resp

    changes = get_shelveset_changes(shelveset_name, owner)

    file_diffs = []
    for change in changes:
        item = change.get("item", {})
        if item.get("isFolder", False):
            continue
        diff = compute_shelveset_file_diff(
            item.get("path", ""),
            change.get("changeType", ""),
            shelveset_name,
            owner,
        )
        file_diffs.append(diff)

    work_items = []
    for wi in ss_data.get("workItems", []):
        work_items.append({
            "id": wi.get("webUrl", "").rsplit("/", 1)[-1] if wi.get("webUrl") else "",
            "title": wi.get("title", ""),
            "url": wi.get("webUrl", ""),
        })

    return {
        "shelveset_name": shelveset_name,
        "owner": ss_data.get("owner", {}).get("displayName", ""),
        "owner_unique_name": ss_data.get("owner", {}).get("uniqueName", ""),
        "date": ss_data.get("createdDate", ""),
        "comment": ss_data.get("comment", ""),
        "work_items": work_items,
        "file_diffs": file_diffs,
        "total_files_changed": len(file_diffs),
        "total_lines_added": sum(f["lines_added"] for f in file_diffs),
        "total_lines_removed": sum(f["lines_removed"] for f in file_diffs),
    }


# ------------------------------------------------
# HELPERS — TFS SHELVESET DISCUSSION COMMENTS
# ------------------------------------------------

TFS_WEB_URL = "http://build-tfs:8080/tfs/HylandCollection/OnBase"


def get_shelveset_web_url(shelveset_name: str, owner_unique_name: str) -> str:
    """Build the TFS web portal URL for a shelveset."""
    shelveset_id = f"{shelveset_name};{owner_unique_name}"
    return f"{TFS_WEB_URL}/_versionControl/shelveset?ss={quote(shelveset_id, safe='')}"


def get_changeset_web_url(changeset_id: int) -> str:
    """Build the TFS web portal URL for a changeset."""
    return f"{TFS_WEB_URL}/_versionControl/changeset/{changeset_id}"


def get_shelveset_discussion_threads(shelveset_name: str, owner: str) -> list:
    """Fetch existing discussion threads on a shelveset."""
    shelveset_id = f"{shelveset_name};{owner}"
    artifact_uri = f"vstfs:///VersionControl/Shelveset/{quote(shelveset_id, safe='')}"
    url = (
        f"http://dev-tfs:8080/tfs/HylandCollection/_apis/discussion/threads"
        f"?artifactUri={quote(artifact_uri, safe='')}"
        f"&api-version=3.1"
    )
    resp = tfs_get(url)
    return resp.get("value", []) if "value" in resp else []


def post_shelveset_comment(
    shelveset_name: str,
    owner: str,
    comment_text: str,
    parent_comment_id: int = 0,
) -> dict:
    """Post a discussion comment on a TFS shelveset.

    Uses the TFS Discussion Threads API with the shelveset artifact URI.
    Supports Markdown if the TFS instance allows it.
    """
    shelveset_id = f"{shelveset_name};{owner}"
    artifact_uri = f"vstfs:///VersionControl/Shelveset/{quote(shelveset_id, safe='')}"
    url = (
        f"http://dev-tfs:8080/tfs/HylandCollection/_apis/discussion/threads"
        f"?api-version=3.1"
    )
    payload = {
        "comments": [
            {
                "parentCommentId": parent_comment_id,
                "content": comment_text,
                "commentType": 1,
            }
        ],
        "properties": {
            "Microsoft.TeamFoundation.Discussion.SupportsMarkdown": {
                "type": "System.Int32",
                "value": 1,
            }
        },
        "status": 1,
    }
    headers = {
        "Content-Type": "application/json",
        "X-TFS-ArtifactURI": artifact_uri,
    }
    r = requests.post(url, json=payload, headers=headers, auth=TFS_AUTH, timeout=30)
    r.raise_for_status()
    return r.json()


# ------------------------------------------------
# ANALYSIS BUNDLE (main Copilot Studio entry point)
# ------------------------------------------------

def build_analysis_bundle(jira_key: str) -> dict:
    jira_context = fetch_jira_context(jira_key)
    changeset_refs = extract_changeset_ids_from_jira(jira_key)

    changesets = []
    for ref in changeset_refs:
        changesets.append(fetch_changeset_with_diffs(ref["id"]))

    return {
        "jira": jira_context,
        "changesets": changesets,
        "summary_stats": {
            "total_changesets": len(changesets),
            "total_files_changed": sum(
                cs["total_files_changed"] for cs in changesets
            ),
            "total_lines_added": sum(
                cs["total_lines_added"] for cs in changesets
            ),
            "total_lines_removed": sum(
                cs["total_lines_removed"] for cs in changesets
            ),
        },
    }

# ------------------------------------------------
# CODE REVIEW — SINGLE-TOOL COMPOSITE PIPELINE
# ------------------------------------------------

_REVIEW_LLM_MODEL = "gpt-4.1-mini"

# ── Centralized output directories ──
_MARKDOWN_ANALYSIS_ROOT = r"C:\Users\rchakraborty\AppData\Roaming\Code\User\prompts\agents\Markdown_Analysis"
_REVIEW_REPORTS_DIR = os.path.join(_MARKDOWN_ANALYSIS_ROOT, "code-reviews")
_FAILURE_REPORTS_DIR = os.path.join(_MARKDOWN_ANALYSIS_ROOT, "build-failure-reports")
_REPO_REPORTS_DIR = os.path.join(_MARKDOWN_ANALYSIS_ROOT, "repo-reports")
_ANALYSIS_REPORTS_DIR = _MARKDOWN_ANALYSIS_ROOT  # general analysis catch-all
_DIFFS_DIR = r"C:\TFS_MCP\diffs"
_STATUS_DIR = os.path.join(_MARKDOWN_ANALYSIS_ROOT, ".status")  # job status files

# Ensure all output directories exist at startup
for _d in (_REVIEW_REPORTS_DIR, _FAILURE_REPORTS_DIR, _REPO_REPORTS_DIR,
           _ANALYSIS_REPORTS_DIR, _DIFFS_DIR, _STATUS_DIR):
    os.makedirs(_d, exist_ok=True)


def _llm_analyze_diffs(jira_context: dict, changesets: list[dict]) -> dict:
    """Make a focused LLM call to analyze code diffs for a code review."""
    from openai import OpenAI as _OpenAI

    # Build a condensed diff payload (cap total size to stay within token limits)
    MAX_DIFF_CHARS = 12000
    diff_text_parts = []
    total_chars = 0
    for cs in changesets:
        for fd in cs.get("file_diffs", []):
            header = f"--- {fd['path']} ({fd['change_type']}) [CS {cs['changeset_id']}] ---\n"
            diff_body = fd.get("diff", "")
            chunk = header + diff_body + "\n"
            if total_chars + len(chunk) > MAX_DIFF_CHARS:
                diff_text_parts.append(f"[... remaining diffs truncated to fit review budget ...]")
                break
            diff_text_parts.append(chunk)
            total_chars += len(chunk)
        else:
            continue
        break
    all_diffs = "\n".join(diff_text_parts)

    linked_desc = ""
    for li in jira_context.get("linked_issues", []):
        linked_desc += f"- {li['key']} ({li['relationship']}): {li['summary']} [{li['status']}]\n"

    prompt = f"""You are a senior code reviewer for OnBase (Hyland Software). Analyze the code changes below.

## Jira Context
- **Key**: {jira_context['jira_key']}
- **Summary**: {jira_context['summary']}
- **Type**: {jira_context['issue_type']} | **Priority**: {jira_context['priority']} | **Status**: {jira_context['status']}
- **Description**: {jira_context.get('description', '')[:800]}
- **Parent Epic**: {jira_context.get('parent_epic', 'N/A')}
- **Linked Issues**:
{linked_desc or 'None'}

## Code Changes
{all_diffs}

## Review Instructions
1. Identify bugs, security issues, performance problems, coding standard violations
2. Check if changes align with the Jira card requirements
3. Identify any missing error handling, null checks, or edge cases
4. Note any test gaps (unit tests, integration tests that should exist)
5. Check for OWASP Top 10 vulnerabilities

Respond in this EXACT JSON format (no markdown fences):
{{"summary": "1-2 sentence overall assessment",
"findings": [{{"severity": "Critical|High|Medium|Low|Info", "file": "short_filename", "line": 0, "title": "brief title", "description": "detail", "suggestion": "fix recommendation"}}],
"verdict": "Approve|Request Changes|Needs Discussion",
"test_scenarios": ["scenario description 1", "scenario description 2"],
"residual_risks": ["risk 1", "risk 2"],
"changed_files_summary": [{{"file": "short_filename", "change_type": "edit|add|delete", "purpose": "what this change does"}}]}}
"""
    client = _OpenAI(
        base_url="https://models.inference.ai.azure.com",
        api_key=os.environ.get("GITHUB_TOKEN", ""),
        max_retries=0,  # Prevent 60s built-in retry that contends with executor LLM calls
    )
    try:
        resp = client.chat.completions.create(
            model=_REVIEW_LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.15,
            max_tokens=3000,
        )
        raw = resp.choices[0].message.content or "{}"
        # Strip markdown fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        return _json.loads(raw)
    except Exception as e:
        _logger.error(f"LLM code review analysis failed: {e}")
        return {
            "summary": f"LLM analysis failed: {e}",
            "findings": [],
            "verdict": "Needs Discussion",
            "test_scenarios": [],
            "residual_risks": [f"Automated analysis failed: {e}"],
            "changed_files_summary": [],
        }


def _generate_review_html(jira_key: str, jira_ctx: dict, changesets: list[dict],
                           stats: dict, analysis: dict) -> str:
    """Generate a clean HTML code review report."""
    from datetime import date as _d
    today = _d.today().isoformat()

    severity_colors = {
        "Critical": "critical", "High": "high", "Medium": "medium",
        "Low": "low", "Info": "muted",
    }

    # Build findings rows
    findings_rows = ""
    for f in analysis.get("findings", []):
        sev = f.get("severity", "Info")
        sev_cls = severity_colors.get(sev, "muted")
        findings_rows += f"""<tr>
          <td><span class="sev {sev_cls}">{sev}</span></td>
          <td>{_html_esc(f.get('file', ''))}</td>
          <td>{_html_esc(f.get('title', ''))}</td>
          <td>{_html_esc(f.get('description', ''))}</td>
          <td>{_html_esc(f.get('suggestion', ''))}</td>
        </tr>\n"""
    if not findings_rows:
        findings_rows = '<tr><td colspan="5" style="text-align:center;color:var(--muted);">No issues found</td></tr>'

    # Build changeset list
    cs_items = ""
    for cs in changesets:
        cs_url = f"http://dev-tfs:8080/tfs/HylandCollection/OnBase/_versionControl/changeset/{cs['changeset_id']}"
        cs_items += f'<li><a href="{cs_url}" target="_blank">CS {cs["changeset_id"]}</a>: {_html_esc(cs.get("comment", "")[:120])}</li>\n'

    # Build changed files summary
    files_rows = ""
    for f in analysis.get("changed_files_summary", []):
        files_rows += f"""<tr>
          <td>{_html_esc(f.get('file', ''))}</td>
          <td>{_html_esc(f.get('change_type', ''))}</td>
          <td>{_html_esc(f.get('purpose', ''))}</td>
        </tr>\n"""

    # Build test scenarios
    test_items = ""
    for t in analysis.get("test_scenarios", []):
        test_items += f"<li>{_html_esc(t)}</li>\n"

    # Build residual risks
    risk_items = ""
    for r in analysis.get("residual_risks", []):
        risk_items += f"<li>{_html_esc(r)}</li>\n"

    # Verdict styling
    verdict = analysis.get("verdict", "Needs Discussion")
    verdict_class = "ok" if verdict == "Approve" else "critical" if verdict == "Request Changes" else "high"

    # Count findings by severity
    sev_counts = {}
    for f in analysis.get("findings", []):
        s = f.get("severity", "Info")
        sev_counts[s] = sev_counts.get(s, 0) + 1
    sev_summary = ", ".join(f"{v} {k}" for k, v in sorted(sev_counts.items(),
                   key=lambda x: ["Critical","High","Medium","Low","Info"].index(x[0])
                   if x[0] in ["Critical","High","Medium","Low","Info"] else 99))

    jira_url = f"https://hyland.atlassian.net/browse/{jira_key}"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Code Review - {jira_key}</title>
  <style>
    :root {{
      --bg: #f4f6f8; --card: #ffffff; --text: #1f2933; --muted: #52606d;
      --border: #d9e2ec; --critical: #b42318; --high: #d97706; --medium: #0f766e;
      --low: #475569; --ok: #166534; --link: #0b69ff;
      --shadow: 0 6px 24px rgba(15,23,42,0.08); --radius: 12px;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background: linear-gradient(180deg,#f8fafc 0%,#eef2f7 100%);
           color: var(--text); font: 15px/1.55 "Segoe UI","Calibri",sans-serif; }}
    .wrap {{ max-width:1080px; margin:28px auto; padding:0 16px 28px; }}
    .header {{ background:var(--card); border:1px solid var(--border);
               border-radius:var(--radius); box-shadow:var(--shadow); padding:20px 22px; margin-bottom:16px; }}
    h1 {{ margin:0 0 4px; font-size:24px; }}
    .sub {{ color:var(--muted); margin:0; }}
    .grid {{ display:grid; gap:16px; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); margin-bottom:16px; }}
    .card {{ background:var(--card); border:1px solid var(--border);
             border-radius:var(--radius); box-shadow:var(--shadow); padding:16px; }}
    .kpi {{ font-size:28px; font-weight:700; margin:4px 0; }}
    .muted {{ color:var(--muted); }}
    h2 {{ margin:0 0 10px; font-size:18px; }}
    table {{ width:100%; border-collapse:collapse; border:1px solid var(--border);
             border-radius:8px; overflow:hidden; background:#fff; }}
    th,td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--border); vertical-align:top; }}
    th {{ font-size:13px; color:#334155; background:#f8fafc; }}
    tr:last-child td {{ border-bottom:0; }}
    .sev {{ display:inline-block; font-size:12px; padding:3px 8px; border-radius:999px;
            border:1px solid currentColor; font-weight:600; }}
    .critical {{ color:var(--critical); }} .high {{ color:var(--high); }}
    .medium {{ color:var(--medium); }} .low {{ color:var(--low); }}
    .verdict {{ padding:10px 12px; border-radius:10px; font-weight:600; margin-top:6px; }}
    .verdict.ok {{ color:var(--ok); border:1px solid #bbf7d0; background:#f0fdf4; }}
    .verdict.critical {{ color:var(--critical); border:1px solid #f0d7d4; background:#fff7f6; }}
    .verdict.high {{ color:var(--high); border:1px solid #fef3c7; background:#fffbeb; }}
    ul {{ margin:8px 0 0 18px; padding:0; }} li {{ margin:4px 0; }}
    a {{ color:var(--link); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
    .small {{ font-size:13px; }}
    .footer {{ margin-top:16px; color:var(--muted); font-size:12px; text-align:right; }}
    @media (max-width:640px) {{ .wrap {{ margin:12px auto; }} h1 {{ font-size:20px; }} }}
  </style>
</head>
<body>
<main class="wrap">
  <section class="header">
    <h1>Code Review: {jira_key}</h1>
    <p class="sub">{_html_esc(jira_ctx.get('summary', ''))}</p>
    <p class="small muted">Review date: {today} &bull; Type: {_html_esc(jira_ctx.get('issue_type', ''))} &bull; Priority: {_html_esc(jira_ctx.get('priority', ''))} &bull; Agent ONE automated review</p>
  </section>

  <section class="grid">
    <article class="card">
      <h2>Jira Context</h2>
      <p><strong>Key:</strong> <a href="{jira_url}" target="_blank">{jira_key}</a></p>
      <p><strong>Status:</strong> {_html_esc(jira_ctx.get('status', ''))}</p>
      <p><strong>Assignee:</strong> {_html_esc(jira_ctx.get('assignee', 'Unassigned'))}</p>
      <p><strong>Epic:</strong> {_html_esc(jira_ctx.get('parent_epic', 'N/A'))}</p>
    </article>
    <article class="card">
      <h2>Findings</h2>
      <div class="kpi">{len(analysis.get('findings', []))}</div>
      <p class="muted">{sev_summary or 'No issues found'}</p>
      <div class="verdict {verdict_class}">{verdict}</div>
    </article>
    <article class="card">
      <h2>Change Stats</h2>
      <p><strong>Changesets:</strong> {stats.get('total_changesets', 0)}</p>
      <p><strong>Files changed:</strong> {stats.get('total_files_changed', 0)}</p>
      <p><strong>Lines added:</strong> +{stats.get('total_lines_added', 0)} &bull; <strong>Removed:</strong> -{stats.get('total_lines_removed', 0)}</p>
      <ul>{cs_items}</ul>
    </article>
  </section>

  <section class="card" style="margin-bottom:16px;">
    <h2>Findings (by severity)</h2>
    <table>
      <thead><tr><th>Severity</th><th>File</th><th>Issue</th><th>Detail</th><th>Suggestion</th></tr></thead>
      <tbody>{findings_rows}</tbody>
    </table>
  </section>

  <section class="card" style="margin-bottom:16px;">
    <h2>Changed Files Summary</h2>
    <table>
      <thead><tr><th>File</th><th>Change</th><th>Purpose</th></tr></thead>
      <tbody>{files_rows or '<tr><td colspan="3" class="muted">See findings table above</td></tr>'}</tbody>
    </table>
  </section>

  <section class="grid">
    <article class="card">
      <h2>Test Scenarios</h2>
      <ul>{test_items or '<li class="muted">None identified</li>'}</ul>
    </article>
    <article class="card">
      <h2>Residual Risks</h2>
      <ul>{risk_items or '<li class="muted">None identified</li>'}</ul>
    </article>
  </section>

  <section class="card" style="margin-bottom:16px;">
    <h2>Review Summary</h2>
    <p>{_html_esc(analysis.get('summary', 'No summary available'))}</p>
  </section>

  <p class="footer">Generated by Agent ONE &bull; {today} &bull; Automated code review &mdash; human review required before merge</p>
</main>
</body>
</html>"""


def _html_esc(s: str) -> str:
    """Basic HTML escaping."""
    if not s:
        return ""
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


# ── Async Code Review Job Tracking (disk-persisted) ──
_review_jobs: dict[str, dict] = {}

# Phase descriptions and progress estimates for user-facing messages
_PHASE_INFO = {
    "started":           {"pct": 5,  "desc": "Initializing code review..."},
    "fetching_jira":     {"pct": 10, "desc": "Fetching Jira context and linked changesets..."},
    "fetching_diffs":    {"pct": 25, "desc": "Fetching code diffs from TFS (this is the slowest step)..."},
    "analyzing":         {"pct": 60, "desc": "Running AI analysis on code changes..."},
    "generating_report": {"pct": 85, "desc": "Generating HTML report..."},
    "attaching_report":  {"pct": 95, "desc": "Attaching report to Jira card..."},
    "done":              {"pct": 100, "desc": "Complete."},
}


def _persist_job(jira_key: str, status: str, phase: str = "started",
                 result: dict = None, error: str = None):
    """Write job status to a JSON file so it survives server restarts."""
    os.makedirs(_STATUS_DIR, exist_ok=True)
    status_file = os.path.join(_STATUS_DIR, f"review_{jira_key}_status.json")
    data = {
        "status": status,
        "jira_key": jira_key,
        "phase": phase,
        "timestamp": _time.time(),
        "started": _review_jobs.get(jira_key, {}).get("started", _time.time()),
    }
    if result:
        data["result"] = result
    if error:
        data["error"] = error
    try:
        with open(status_file, "w", encoding="utf-8") as f:
            _json.dump(data, f)
    except Exception as e:
        _logger.warning(f"Failed to persist job status for {jira_key}: {e}")


def _load_persisted_job(jira_key: str) -> dict | None:
    """Load job status from disk (fallback when in-memory state is lost)."""
    status_file = os.path.join(_STATUS_DIR, f"review_{jira_key}_status.json")
    if os.path.isfile(status_file):
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            pass
    return None


def _update_job_phase(jira_key: str, phase: str):
    """Update the phase of a running job (in-memory + disk)."""
    if jira_key in _review_jobs:
        _review_jobs[jira_key]["phase"] = phase
    _persist_job(jira_key, "running", phase)


def start_code_review(jira_key: str) -> dict:
    """Start a code review in the background. Returns immediately (~1s).

    The review (Jira fetch → TFS diffs → LLM analysis → HTML report) runs in
    a background thread and typically completes in 45-60 seconds.
    Use get_code_review_status(jira_key) to check when it is done.
    """
    jira_key = jira_key.strip().upper()

    # If already running (in-memory check), don't start another
    if jira_key in _review_jobs and _review_jobs[jira_key]["status"] == "running":
        elapsed = _time.time() - _review_jobs[jira_key]["started"]
        phase = _review_jobs[jira_key].get("phase", "started")
        phase_info = _PHASE_INFO.get(phase, _PHASE_INFO["started"])
        return {
            "status": "already_running",
            "jira_key": jira_key,
            "elapsed_seconds": round(elapsed, 1),
            "phase": phase,
            "progress_pct": phase_info["pct"],
            "phase_description": phase_info["desc"],
            "message": f"A code review for {jira_key} is already in progress "
                       f"({round(elapsed)}s elapsed, {phase_info['pct']}% — {phase_info['desc']}). "
                       f"Ask for the code review status in about {max(10, 55 - round(elapsed))} seconds.",
        }

    # Register the job as running
    _review_jobs[jira_key] = {
        "status": "running",
        "started": _time.time(),
        "phase": "started",
        "result": None,
        "error": None,
    }
    _persist_job(jira_key, "running", "started")

    def _worker():
        try:
            result = _do_code_review_sync(jira_key)
            _review_jobs[jira_key]["status"] = "done"
            _review_jobs[jira_key]["phase"] = "done"
            _review_jobs[jira_key]["result"] = result
            _persist_job(jira_key, "done", "done", result=result)
        except Exception as e:
            _logger.error(f"Background code review [{jira_key}] failed: {e}")
            _review_jobs[jira_key]["status"] = "error"
            _review_jobs[jira_key]["error"] = str(e)
            _persist_job(jira_key, "error", error=str(e))

    t = threading.Thread(target=_worker, daemon=True, name=f"code-review-{jira_key}")
    t.start()
    _logger.info(f"Code review [{jira_key}]: started background thread")

    return {
        "status": "started",
        "jira_key": jira_key,
        "progress_pct": 5,
        "phase": "started",
        "phase_description": "Initializing code review...",
        "message": f"Code review for {jira_key} has been started in the background. "
                   f"It typically takes 45-60 seconds. "
                   f"Ask for the code review status of {jira_key} in about a minute to get the results.",
        "estimated_seconds": 50,
    }


def get_code_review_status(jira_key: str) -> dict:
    """Check the status of a background code review.

    Returns the full review results (summary, verdict, findings, local report path)
    when the review is done, progress info if still running, or an error message.
    Checks in-memory job tracker first, then falls back to disk-persisted status.
    """
    jira_key = jira_key.strip().upper()

    # --- Check 1: In-memory job tracker ---
    if jira_key in _review_jobs:
        job = _review_jobs[jira_key]
        elapsed = _time.time() - job["started"]
        phase = job.get("phase", "started")

        if job["status"] == "running":
            phase_info = _PHASE_INFO.get(phase, _PHASE_INFO["started"])
            return {
                "status": "running",
                "jira_key": jira_key,
                "elapsed_seconds": round(elapsed, 1),
                "phase": phase,
                "progress_pct": phase_info["pct"],
                "phase_description": phase_info["desc"],
                "message": f"Code review for {jira_key}: {phase_info['desc']} "
                           f"({phase_info['pct']}% complete, {round(elapsed)}s elapsed). "
                           f"Check back in ~{max(5, 55 - round(elapsed))} seconds.",
            }
        elif job["status"] == "done":
            return {"status": "done", "jira_key": jira_key, **job["result"]}
        else:  # error — fall through to disk/file checks
            pass

    # --- Check 2: Disk-persisted job status (survives server restarts) ---
    persisted = _load_persisted_job(jira_key)
    if persisted:
        if persisted["status"] == "done" and persisted.get("result"):
            return {"status": "done", "jira_key": jira_key, **persisted["result"]}
        elif persisted["status"] == "running":
            elapsed = _time.time() - persisted.get("started", _time.time())
            phase = persisted.get("phase", "started")
            phase_info = _PHASE_INFO.get(phase, _PHASE_INFO["started"])
            # If it's been "running" for > 120s, it probably crashed silently
            if elapsed > 120:
                pass  # Fall through to file check
            else:
                return {
                    "status": "running",
                    "jira_key": jira_key,
                    "elapsed_seconds": round(elapsed, 1),
                    "phase": phase,
                    "progress_pct": phase_info["pct"],
                    "phase_description": phase_info["desc"],
                    "message": f"Code review for {jira_key}: {phase_info['desc']} "
                               f"({phase_info['pct']}% complete, {round(elapsed)}s elapsed).",
                }
        elif persisted["status"] == "error":
            pass  # Fall through to file check

    # --- Check 3: Existing report file on disk ---
    import glob
    pattern = os.path.join(_REVIEW_REPORTS_DIR, f"{jira_key}_Code_Review_*.html")
    existing = sorted(glob.glob(pattern), reverse=True)
    if existing:
        filepath = existing[0]
        filename = os.path.basename(filepath)
        return {
            "status": "done",
            "jira_key": jira_key,
            "message": f"Code review report for {jira_key} is ready.",
            "report_filename": filename,
            "report_local_path": filepath,
            "report_url": f"/reports/{filename}",
        }

    # --- Check 4: In-memory error with no disk report ---
    if jira_key in _review_jobs and _review_jobs[jira_key]["status"] == "error":
        return {
            "status": "error",
            "jira_key": jira_key,
            "error": _review_jobs[jira_key].get("error", "Unknown"),
            "message": f"Code review for {jira_key} failed: {_review_jobs[jira_key].get('error', 'Unknown')}. "
                       f"You can retry by asking for a code review again.",
        }

    return {
        "status": "not_found",
        "jira_key": jira_key,
        "message": f"No code review found for {jira_key}. "
                   f"Use start_code_review to begin one.",
    }


def _do_code_review_sync(jira_key: str) -> dict:
    """Internal synchronous code review worker. Called by start_code_review's background thread."""
    import concurrent.futures
    from datetime import date as _d
    t0 = _time.time()

    # Step 1: Parallel data gathering — Jira + changeset IDs
    _update_job_phase(jira_key, "fetching_jira")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jira_fut = pool.submit(fetch_jira_context, jira_key)
        cs_fut = pool.submit(extract_changeset_ids_from_jira, jira_key)
        jira_ctx = jira_fut.result(timeout=20)
        cs_refs = cs_fut.result(timeout=20)

    t1 = _time.time()
    _logger.info(f"Code review [{jira_key}]: Jira + CS refs in {t1-t0:.1f}s, {len(cs_refs)} changesets")

    # Step 2: Fetch diffs — parallel, capped at 5 changesets
    _update_job_phase(jira_key, "fetching_diffs")
    changesets = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(fetch_changeset_with_diffs, ref["id"]): ref
                for ref in cs_refs[:5]}
        for fut in concurrent.futures.as_completed(futs, timeout=30):
            try:
                changesets.append(fut.result())
            except Exception as e:
                ref = futs[fut]
                _logger.warning(f"Code review [{jira_key}]: CS {ref['id']} diff failed: {e}")

    # Sort by changeset ID for deterministic order
    changesets.sort(key=lambda c: c.get("changeset_id", 0))

    t2 = _time.time()
    _logger.info(f"Code review [{jira_key}]: diffs fetched in {t2-t1:.1f}s")

    stats = {
        "total_changesets": len(changesets),
        "total_files_changed": sum(cs.get("total_files_changed", 0) for cs in changesets),
        "total_lines_added": sum(cs.get("total_lines_added", 0) for cs in changesets),
        "total_lines_removed": sum(cs.get("total_lines_removed", 0) for cs in changesets),
    }

    # Step 3: LLM analysis (single focused call)
    _update_job_phase(jira_key, "analyzing")
    analysis = _llm_analyze_diffs(jira_ctx, changesets)
    t3 = _time.time()
    _logger.info(f"Code review [{jira_key}]: LLM analysis in {t3-t2:.1f}s")

    _update_job_phase(jira_key, "generating_report")
    # Step 4: Generate HTML report
    html = _generate_review_html(jira_key, jira_ctx, changesets, stats, analysis)
    os.makedirs(_REVIEW_REPORTS_DIR, exist_ok=True)
    filename = f"{jira_key}_Code_Review_{_d.today().isoformat()}.html"
    filepath = os.path.join(_REVIEW_REPORTS_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    # Step 5: Attach report to Jira card
    _update_job_phase(jira_key, "attaching_report")
    attachment_url = ""
    try:
        att = jira_attach_file(jira_key, filepath)
        attachment_url = att.get("content_url", "")
        _logger.info(f"Code review [{jira_key}]: report attached to Jira -> {attachment_url}")
    except Exception as e:
        _logger.warning(f"Code review [{jira_key}]: failed to attach report to Jira: {e}")

    elapsed = _time.time() - t0
    _logger.info(f"Code review [{jira_key}]: complete in {elapsed:.1f}s -> {filepath}")

    # Build a text summary for the agent to return
    sev_counts = {}
    for finding in analysis.get("findings", []):
        s = finding.get("severity", "Info")
        sev_counts[s] = sev_counts.get(s, 0) + 1
    sev_line = ", ".join(f"{v} {k}" for k, v in sorted(sev_counts.items(),
                key=lambda x: ["Critical","High","Medium","Low","Info"].index(x[0])
                if x[0] in ["Critical","High","Medium","Low","Info"] else 99))

    # Extract the top (highest severity) finding for quick display
    _sev_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Info": 4}
    sorted_findings = sorted(
        analysis.get("findings", []),
        key=lambda f: _sev_order.get(f.get("severity", "Info"), 99),
    )
    top_finding = ""
    if sorted_findings:
        tf = sorted_findings[0]
        top_finding = f"[{tf.get('severity', 'Info')}] {tf.get('title', '')} — {tf.get('file', '')}"

    return {
        "jira_key": jira_key,
        "summary": analysis.get("summary", ""),
        "verdict": analysis.get("verdict", "Needs Discussion"),
        "findings_count": len(analysis.get("findings", [])),
        "findings_by_severity": sev_line or "No issues",
        "top_finding": top_finding,
        "files_changed": stats["total_files_changed"],
        "changesets_reviewed": stats["total_changesets"],
        "test_scenarios": analysis.get("test_scenarios", []),
        "residual_risks": analysis.get("residual_risks", []),
        "report_local_path": filepath,
        "report_url": f"/reports/{filename}",
        "attachment_url": attachment_url,
        "elapsed_seconds": round(elapsed, 1),
    }


# ── Async Build Failure Analysis Job Tracking ──
_failure_analysis_jobs: dict[str, dict] = {}

_FAILURE_PHASE_INFO = {
    "started":            {"pct": 5,   "desc": "Initializing build failure analysis..."},
    "fetching_build":     {"pct": 10,  "desc": "Fetching build details and timeline..."},
    "fetching_logs":      {"pct": 25,  "desc": "Fetching failed task logs..."},
    "fetching_changes":   {"pct": 40,  "desc": "Fetching associated changesets..."},
    "fetching_jira":      {"pct": 50,  "desc": "Fetching Jira context..."},
    "analyzing":          {"pct": 65,  "desc": "Running AI root cause analysis..."},
    "generating_report":  {"pct": 85,  "desc": "Generating HTML report..."},
    "done":               {"pct": 100, "desc": "Complete."},
}

def _persist_failure_job(job_key: str, status: str, phase: str = "started",
                         result: dict = None, error: str = None):
    """Write failure analysis job status to disk."""
    os.makedirs(_STATUS_DIR, exist_ok=True)
    status_file = os.path.join(_STATUS_DIR, f"failure_{job_key}_status.json")
    data = {
        "status": status,
        "job_key": job_key,
        "phase": phase,
        "timestamp": _time.time(),
        "started": _failure_analysis_jobs.get(job_key, {}).get("started", _time.time()),
    }
    if result:
        data["result"] = result
    if error:
        data["error"] = error
    try:
        with open(status_file, "w", encoding="utf-8") as f:
            _json.dump(data, f)
    except Exception as e:
        _logger.warning(f"Failed to persist failure analysis status for {job_key}: {e}")


def _load_persisted_failure_job(job_key: str) -> dict | None:
    status_file = os.path.join(_STATUS_DIR, f"failure_{job_key}_status.json")
    if os.path.isfile(status_file):
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            pass
    return None


def _update_failure_phase(job_key: str, phase: str):
    if job_key in _failure_analysis_jobs:
        _failure_analysis_jobs[job_key]["phase"] = phase
    _persist_failure_job(job_key, "running", phase)


def _llm_analyze_build_failure(build_info: dict, failed_tasks: list[dict],
                                changes: list[dict], jira_ctx: dict | None) -> dict:
    """Use LLM to determine root cause of a build failure."""
    from openai import OpenAI as _OpenAI

    # Build condensed log payload
    MAX_LOG_CHARS = 10000
    log_parts = []
    total = 0
    for ft in failed_tasks:
        header = f"--- FAILED TASK: {ft['task_name']} (result={ft['result']}, errors={ft['error_count']}) ---\n"
        issues_text = ""
        for iss in ft.get("issues", []):
            issues_text += f"  [{iss.get('type','')}] {iss.get('message','')}\n"
        log_tail = ft.get("log_tail", "")
        chunk = header + issues_text + log_tail + "\n"
        if total + len(chunk) > MAX_LOG_CHARS:
            log_parts.append("[... remaining logs truncated ...]")
            break
        log_parts.append(chunk)
        total += len(chunk)
    all_logs = "\n".join(log_parts)

    # Build changeset summary
    changes_text = ""
    for ch in changes[:20]:
        changes_text += f"- C{ch.get('id','')}: {ch.get('message','')[:120]} (by {ch.get('author','')})\n"

    jira_text = ""
    if jira_ctx:
        jira_text = f"""## Jira Context
- **Key**: {jira_ctx.get('jira_key', 'N/A')}
- **Summary**: {jira_ctx.get('summary', 'N/A')}
- **Type**: {jira_ctx.get('issue_type', 'N/A')} | **Priority**: {jira_ctx.get('priority', 'N/A')}
- **Description**: {jira_ctx.get('description', '')[:600]}
"""

    prompt = f"""You are a senior build/release engineer for OnBase (Hyland Software). Analyze this build failure.

## Build Information
- **Build ID**: {build_info.get('build_id', '')}
- **Build Number**: {build_info.get('build_number', '')}
- **Definition**: {build_info.get('definition_name', '')}
- **Status**: {build_info.get('status', '')} | **Result**: {build_info.get('result', '')}
- **Source Branch**: {build_info.get('source_branch', '')}
- **Start Time**: {build_info.get('start_time', '')}
- **Finish Time**: {build_info.get('finish_time', '')}
- **Requested By**: {build_info.get('requested_by', '')}

{jira_text}

## Failed Tasks & Logs
{all_logs}

## Associated Changesets (most recent first)
{changes_text or 'No changesets found.'}

## Analysis Instructions
1. Determine the root cause of the failure from the logs and error messages.
2. Classify the failure: Code Defect, Test Environment Issue, Infrastructure, Configuration, \
Dependency/Schema Mismatch, Flaky Test, Build System Issue, or Other.
3. If a specific changeset triggered the failure, identify it.
4. Describe the failure chain (what failed first, what cascaded).
5. Recommend specific resolution steps.
6. Assess severity and impact on the release pipeline.

Respond in this EXACT JSON format (no markdown fences):
{{"root_cause_summary": "1-2 sentence root cause description",
"failure_category": "Code Defect|Test Environment|Infrastructure|Configuration|Schema Mismatch|Flaky Test|Build System|Dependency|Other",
"is_code_defect": true or false,
"triggering_changeset": {{"id": "C405415 or null", "jira_key": "DB-2311 or null", "message": "commit msg or null", "author": "name or null", "timestamp": "date or null"}},
"failure_chain": [{{"stage": "stage name", "detail": "what happened"}}],
"error_messages": ["key error message 1", "key error message 2"],
"resolution": {{"summary": "brief resolution description", "steps": ["step 1", "step 2"], "assigned_to": "team or person suggestion"}},
"severity": "Critical|High|Medium|Low",
"impact": "description of release/pipeline impact",
"affected_test_fixtures": ["fixture or test class names that failed"]}}
"""
    client = _OpenAI(
        base_url="https://models.inference.ai.azure.com",
        api_key=os.environ.get("GITHUB_TOKEN", ""),
        max_retries=0,
    )
    try:
        resp = client.chat.completions.create(
            model=_REVIEW_LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=3000,
        )
        raw = resp.choices[0].message.content or "{}"
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        return _json.loads(raw)
    except Exception as e:
        _logger.error(f"LLM build failure analysis failed: {e}")
        return {
            "root_cause_summary": f"LLM analysis failed: {e}",
            "failure_category": "Other",
            "is_code_defect": False,
            "triggering_changeset": None,
            "failure_chain": [],
            "error_messages": [],
            "resolution": {"summary": "Manual investigation required.", "steps": [], "assigned_to": ""},
            "severity": "Medium",
            "impact": "Unknown — automated analysis failed.",
            "affected_test_fixtures": [],
        }


def _generate_failure_html(build_info: dict, failed_tasks: list[dict],
                           changes: list[dict], analysis: dict,
                           jira_ctx: dict | None) -> str:
    """Generate a professional HTML build failure report."""
    from datetime import date as _d

    build_id = build_info.get("build_id", "")
    build_num = build_info.get("build_number", "")
    def_name = build_info.get("definition_name", "")
    jira_key = jira_ctx.get("jira_key", "") if jira_ctx else ""
    today = _d.today().isoformat()

    cat = _html_esc(analysis.get("failure_category", "Unknown"))
    is_defect = analysis.get("is_code_defect", False)
    severity = analysis.get("severity", "Medium")

    sev_colors = {"Critical": "#dc3545", "High": "#ff6b35", "Medium": "#ff9800", "Low": "#28a745"}
    sev_color = sev_colors.get(severity, "#666")

    # Failure chain table rows
    chain_rows = ""
    for step in analysis.get("failure_chain", []):
        chain_rows += f"""                <tr>
                    <td>{_html_esc(step.get('stage', ''))}</td>
                    <td>{_html_esc(step.get('detail', ''))}</td>
                </tr>
"""

    # Triggering changeset table
    tcs = analysis.get("triggering_changeset") or {}
    cs_rows = ""
    if tcs and tcs.get("id"):
        for field, val in [("ID", tcs.get("id", "")), ("Jira", tcs.get("jira_key", "")),
                           ("Message", tcs.get("message", "")), ("Author", tcs.get("author", "")),
                           ("Timestamp", tcs.get("timestamp", ""))]:
            if val:
                cs_rows += f"""                <tr>
                    <td>{_html_esc(field)}</td>
                    <td>{_html_esc(str(val))}</td>
                </tr>
"""
    cs_section = ""
    if cs_rows:
        cs_section = f"""
        <div class="section">
            <div class="section-title">TRIGGERING CHANGESET</div>
            <table>
                <tr><th>Field</th><th>Value</th></tr>
{cs_rows}            </table>
        </div>"""

    # Error messages
    error_msgs = analysis.get("error_messages", [])
    error_box = ""
    if error_msgs:
        error_box = '<div class="error-box">' + "<br>".join(_html_esc(m) for m in error_msgs[:5]) + "</div>"

    # Resolution
    resolution = analysis.get("resolution", {})
    res_summary = _html_esc(resolution.get("summary", ""))
    res_steps = resolution.get("steps", [])
    steps_html = ""
    if res_steps:
        steps_html = "<ul>" + "".join(f"<li>{_html_esc(s)}</li>" for s in res_steps) + "</ul>"
    assigned_to = _html_esc(resolution.get("assigned_to", ""))

    # Failed tasks summary table
    task_rows = ""
    for ft in failed_tasks[:10]:
        task_rows += f"""                <tr>
                    <td>{_html_esc(ft.get('task_name', ''))}</td>
                    <td>{_html_esc(ft.get('result', ''))}</td>
                    <td>{ft.get('error_count', 0)}</td>
                    <td>{_html_esc('; '.join(iss.get('message','')[:80] for iss in ft.get('issues',[])))}</td>
                </tr>
"""

    # Affected test fixtures
    fixtures = analysis.get("affected_test_fixtures", [])
    fixtures_html = ""
    if fixtures:
        fixtures_html = "<ul>" + "".join(f"<li>{_html_esc(f)}</li>" for f in fixtures) + "</ul>"

    # Impact
    impact = _html_esc(analysis.get("impact", ""))

    # Verdict label
    if is_defect:
        verdict_html = '<strong style="color: #dc3545;">✗ Code Defect — Requires Fix</strong>'
    else:
        verdict_html = '<strong class="success">✓ Environment/Infrastructure Issue (Not Code Defect)</strong>'

    # Build URL
    build_url = build_info.get("url", "")

    # Header subtitle
    subtitle_parts = []
    if jira_key:
        subtitle_parts.append(f'<span class="issue-key">{_html_esc(jira_key)}</span>')
    subtitle_parts.append(f"Build ID: {build_id}")
    subtitle_parts.append(f"Date: {today}")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Build Failure Analysis — {_html_esc(def_name)}</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            line-height: 1.5;
            color: #333;
            background: #f5f5f5;
            margin: 0;
            padding: 20px;
        }}
        .container {{
            max-width: 8.5in;
            background: white;
            margin: 0 auto;
            padding: 0.75in;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            box-sizing: border-box;
        }}
        .header {{
            border-bottom: 3px solid #0066cc;
            padding-bottom: 8px;
            margin-bottom: 12px;
        }}
        .header h1 {{
            margin: 0;
            font-size: 18px;
            color: #0066cc;
        }}
        .header p {{
            margin: 2px 0 0 0;
            font-size: 11px;
            color: #666;
        }}
        .issue-key {{
            display: inline-block;
            background: #0066cc;
            color: white;
            padding: 2px 6px;
            border-radius: 3px;
            font-weight: bold;
            font-size: 11px;
            margin-right: 8px;
        }}
        .section {{
            margin-bottom: 14px;
        }}
        .section-title {{
            font-weight: bold;
            color: #0066cc;
            font-size: 12px;
            border-bottom: 1px solid #ccc;
            padding-bottom: 3px;
            margin-bottom: 6px;
        }}
        .content {{
            font-size: 11px;
            line-height: 1.4;
        }}
        table {{
            width: 100%;
            font-size: 10px;
            border-collapse: collapse;
            margin-bottom: 8px;
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 4px;
            text-align: left;
        }}
        th {{
            background: #f0f0f0;
            font-weight: bold;
        }}
        .error-box {{
            background: #fff3cd;
            border-left: 3px solid #ff9800;
            padding: 6px;
            margin: 6px 0;
            font-size: 10px;
            font-family: 'Courier New', monospace;
            word-break: break-word;
        }}
        .success {{
            color: #28a745;
            font-weight: bold;
        }}
        .warning {{
            color: #ff9800;
            font-weight: bold;
        }}
        .severity-badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 3px;
            font-weight: bold;
            font-size: 10px;
            color: white;
        }}
        ul {{
            margin: 4px 0;
            padding-left: 16px;
        }}
        li {{
            margin: 2px 0;
        }}
        .footer {{
            border-top: 1px solid #ccc;
            margin-top: 12px;
            padding-top: 6px;
            font-size: 9px;
            color: #999;
        }}
        a {{
            color: #0066cc;
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Build Failure — Root Cause Analysis</h1>
            <p>
                {' | '.join(subtitle_parts)}
            </p>
            <p>Pipeline: <strong>{_html_esc(def_name)}</strong> | Build: <strong>{_html_esc(build_num)}</strong></p>
        </div>

        <div class="section">
            <div class="section-title">FAILURE SUMMARY</div>
            <div class="content">
                <span class="severity-badge" style="background:{sev_color};">{_html_esc(severity)}</span>
                &nbsp; Category: <strong>{cat}</strong>
                <p>{_html_esc(analysis.get('root_cause_summary', ''))}</p>
                {error_box}
            </div>
        </div>

        <div class="section">
            <div class="section-title">ROOT CAUSE</div>
            <div class="content">
                {verdict_html}
                <p>{_html_esc(analysis.get('root_cause_summary', ''))}</p>
            </div>
        </div>

        <div class="section">
            <div class="section-title">FAILURE CHAIN</div>
            <table>
                <tr><th>Stage</th><th>Detail</th></tr>
{chain_rows}            </table>
        </div>
{cs_section}

        <div class="section">
            <div class="section-title">FAILED TASKS</div>
            <table>
                <tr><th>Task</th><th>Result</th><th>Errors</th><th>Key Issue</th></tr>
{task_rows}            </table>
        </div>

        {"<div class='section'><div class='section-title'>AFFECTED TEST FIXTURES</div><div class='content'>" + fixtures_html + "</div></div>" if fixtures_html else ""}

        <div class="section">
            <div class="section-title">RESOLUTION</div>
            <div class="content">
                <strong>{_html_esc(res_summary)}</strong>
                {steps_html}
                {"<p><strong>Assign to:</strong> " + assigned_to + "</p>" if assigned_to else ""}
            </div>
        </div>

        <div class="section">
            <div class="section-title">IMPACT</div>
            <div class="content">{_html_esc(impact)}</div>
        </div>

        <div class="footer">
            Report Generated: {today} | Build ID: {build_id}
            {' | <a href="' + _html_esc(build_url) + '">View Build</a>' if build_url else ''}
        </div>
    </div>
</body>
</html>"""


def _do_build_failure_analysis_sync(build_id: int, jira_key: str | None = None) -> dict:
    """Internal synchronous build failure analysis worker."""
    import concurrent.futures
    from datetime import date as _d
    job_key = str(build_id)
    t0 = _time.time()

    # Step 1: Get build details + timeline
    _update_failure_phase(job_key, "fetching_build")
    build_info = get_build_detail(build_id)
    failed_tasks_raw = get_failed_tasks(build_id)
    t1 = _time.time()
    _logger.info(f"Build failure [{build_id}]: build info in {t1-t0:.1f}s, {len(failed_tasks_raw)} failed tasks")

    # Step 2: Fetch logs for failed tasks (parallel, limit to 5)
    _update_failure_phase(job_key, "fetching_logs")
    failed_tasks = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        def _fetch_task_log(task):
            detail = {
                "task_name": task["name"],
                "task_type": task["type"],
                "result": task["result"],
                "error_count": task["error_count"],
                "warning_count": task["warning_count"],
                "issues": task["issues"],
                "log_id": task["log_id"],
            }
            if task["log_id"]:
                try:
                    log_text = get_build_log(build_id, task["log_id"])
                    lines = log_text.splitlines()
                    if len(lines) > 300:
                        detail["log_tail"] = "\n".join(lines[-300:])
                        detail["log_truncated"] = True
                        detail["total_log_lines"] = len(lines)
                    else:
                        detail["log_tail"] = log_text
                        detail["log_truncated"] = False
                except Exception as e:
                    detail["log_error"] = str(e)
            return detail

        futs = {pool.submit(_fetch_task_log, t): t for t in failed_tasks_raw[:5]}
        for fut in concurrent.futures.as_completed(futs, timeout=30):
            try:
                failed_tasks.append(fut.result())
            except Exception as e:
                _logger.warning(f"Build failure [{build_id}]: log fetch failed: {e}")

    t2 = _time.time()
    _logger.info(f"Build failure [{build_id}]: logs fetched in {t2-t1:.1f}s")

    # Step 3: Fetch associated changesets
    _update_failure_phase(job_key, "fetching_changes")
    try:
        changes = get_build_changes(build_id)
    except Exception:
        changes = []
    t3 = _time.time()

    # Step 4: Optionally fetch Jira context
    jira_ctx = None
    if jira_key:
        _update_failure_phase(job_key, "fetching_jira")
        try:
            jira_ctx = fetch_jira_context(jira_key)
        except Exception as e:
            _logger.warning(f"Build failure [{build_id}]: Jira fetch failed: {e}")

    # Step 5: LLM root cause analysis
    _update_failure_phase(job_key, "analyzing")
    analysis = _llm_analyze_build_failure(build_info, failed_tasks, changes, jira_ctx)
    t4 = _time.time()
    _logger.info(f"Build failure [{build_id}]: LLM analysis in {t4-t3:.1f}s")

    # Step 6: Generate HTML report
    _update_failure_phase(job_key, "generating_report")
    html = _generate_failure_html(build_info, failed_tasks, changes, analysis, jira_ctx)
    os.makedirs(_FAILURE_REPORTS_DIR, exist_ok=True)

    # Build filename
    def_name_safe = re.sub(r"[^a-zA-Z0-9_-]", "_", build_info.get("definition_name", "build"))[:40]
    jira_prefix = f"{jira_key}_" if jira_key else ""
    filename = f"{jira_prefix}{def_name_safe}_Failure_Report.html"
    filepath = os.path.join(_FAILURE_REPORTS_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    elapsed = _time.time() - t0
    _logger.info(f"Build failure [{build_id}]: complete in {elapsed:.1f}s -> {filepath}")

    return {
        "build_id": build_id,
        "build_number": build_info.get("build_number", ""),
        "definition_name": build_info.get("definition_name", ""),
        "jira_key": jira_key or "",
        "root_cause": analysis.get("root_cause_summary", ""),
        "failure_category": analysis.get("failure_category", ""),
        "is_code_defect": analysis.get("is_code_defect", False),
        "severity": analysis.get("severity", "Medium"),
        "resolution": analysis.get("resolution", {}).get("summary", ""),
        "resolution_steps": analysis.get("resolution", {}).get("steps", []),
        "failed_tasks_count": len(failed_tasks),
        "changesets_count": len(changes),
        "report_local_path": filepath,
        "report_filename": filename,
        "elapsed_seconds": round(elapsed, 1),
    }


def start_build_failure_analysis(build_id: int, jira_key: str = "") -> dict:
    """Start a build failure analysis in the background. Returns immediately.

    Analyzes a failed TFS build: fetches build details, failed task logs,
    associated changesets, optionally Jira context, runs AI root cause
    analysis, and generates an HTML report.
    Use get_build_failure_analysis_status(build_id) to check results.
    """
    job_key = str(build_id)
    jira_key = jira_key.strip().upper() if jira_key else ""

    if job_key in _failure_analysis_jobs and _failure_analysis_jobs[job_key]["status"] == "running":
        elapsed = _time.time() - _failure_analysis_jobs[job_key]["started"]
        phase = _failure_analysis_jobs[job_key].get("phase", "started")
        pi = _FAILURE_PHASE_INFO.get(phase, _FAILURE_PHASE_INFO["started"])
        return {
            "status": "already_running",
            "build_id": build_id,
            "elapsed_seconds": round(elapsed, 1),
            "phase": phase,
            "progress_pct": pi["pct"],
            "phase_description": pi["desc"],
            "message": f"Build failure analysis for build {build_id} is already in progress "
                       f"({round(elapsed)}s elapsed, {pi['pct']}% — {pi['desc']}). "
                       f"Ask for the status in about {max(5, 40-round(elapsed))} seconds.",
        }

    _failure_analysis_jobs[job_key] = {
        "status": "running",
        "started": _time.time(),
        "phase": "started",
        "result": None,
        "error": None,
    }
    _persist_failure_job(job_key, "running", "started")

    def _worker():
        try:
            result = _do_build_failure_analysis_sync(build_id, jira_key or None)
            _failure_analysis_jobs[job_key]["status"] = "done"
            _failure_analysis_jobs[job_key]["phase"] = "done"
            _failure_analysis_jobs[job_key]["result"] = result
            _persist_failure_job(job_key, "done", "done", result=result)
        except Exception as e:
            _logger.error(f"Build failure analysis [{build_id}] failed: {e}")
            _failure_analysis_jobs[job_key]["status"] = "error"
            _failure_analysis_jobs[job_key]["error"] = str(e)
            _persist_failure_job(job_key, "error", error=str(e))

    t = threading.Thread(target=_worker, daemon=True, name=f"build-failure-{build_id}")
    t.start()
    _logger.info(f"Build failure analysis [{build_id}]: started background thread")

    return {
        "status": "started",
        "build_id": build_id,
        "jira_key": jira_key,
        "progress_pct": 5,
        "phase": "started",
        "phase_description": "Initializing build failure analysis...",
        "message": f"Build failure analysis for build {build_id} has been started. "
                   f"It typically takes 30-45 seconds. "
                   f"Ask for the build failure analysis status for build {build_id} in about 30 seconds.",
        "estimated_seconds": 35,
    }


def get_build_failure_analysis_status(build_id: int) -> dict:
    """Check the status of a background build failure analysis.

    Returns the full analysis results (root cause, severity, report path)
    when done, progress info if still running, or an error message.
    """
    job_key = str(build_id)

    # In-memory check
    if job_key in _failure_analysis_jobs:
        job = _failure_analysis_jobs[job_key]
        elapsed = _time.time() - job["started"]
        phase = job.get("phase", "started")

        if job["status"] == "running":
            pi = _FAILURE_PHASE_INFO.get(phase, _FAILURE_PHASE_INFO["started"])
            return {
                "status": "running",
                "build_id": build_id,
                "elapsed_seconds": round(elapsed, 1),
                "phase": phase,
                "progress_pct": pi["pct"],
                "phase_description": pi["desc"],
                "message": f"Build failure analysis for build {build_id}: {pi['desc']} "
                           f"({pi['pct']}% complete, {round(elapsed)}s elapsed).",
            }
        elif job["status"] == "done":
            return {"status": "done", "build_id": build_id, **job["result"]}
        # error — fall through

    # Disk-persisted check
    persisted = _load_persisted_failure_job(job_key)
    if persisted:
        if persisted["status"] == "done" and persisted.get("result"):
            return {"status": "done", "build_id": build_id, **persisted["result"]}
        elif persisted["status"] == "running":
            elapsed = _time.time() - persisted.get("started", _time.time())
            phase = persisted.get("phase", "started")
            pi = _FAILURE_PHASE_INFO.get(phase, _FAILURE_PHASE_INFO["started"])
            if elapsed <= 120:
                return {
                    "status": "running",
                    "build_id": build_id,
                    "elapsed_seconds": round(elapsed, 1),
                    "phase": phase,
                    "progress_pct": pi["pct"],
                    "phase_description": pi["desc"],
                    "message": f"Build failure analysis for build {build_id}: {pi['desc']} "
                               f"({pi['pct']}% complete, {round(elapsed)}s elapsed).",
                }

    # Check for existing report file on disk
    import glob
    pattern = os.path.join(_FAILURE_REPORTS_DIR, f"*_Failure_Report.html")
    for filepath in sorted(glob.glob(pattern), reverse=True):
        if str(build_id) in os.path.basename(filepath) or str(build_id) in open(filepath, "r", encoding="utf-8", errors="ignore").read()[:2000]:
            filename = os.path.basename(filepath)
            return {
                "status": "done",
                "build_id": build_id,
                "message": f"Build failure analysis report for build {build_id} is ready.",
                "report_filename": filename,
                "report_local_path": filepath,
            }

    # In-memory error
    if job_key in _failure_analysis_jobs and _failure_analysis_jobs[job_key]["status"] == "error":
        return {
            "status": "error",
            "build_id": build_id,
            "error": _failure_analysis_jobs[job_key].get("error", "Unknown"),
            "message": f"Build failure analysis for build {build_id} failed. You can retry.",
        }

    return {
        "status": "not_found",
        "build_id": build_id,
        "message": f"No build failure analysis found for build {build_id}. "
                   f"Use start_build_failure_analysis to begin one.",
    }


# ------------------------------------------------
# STATIC FILE SERVING — Code Review Reports
# ------------------------------------------------

from fastapi.responses import FileResponse

@app.get(
    "/reports/{filename}",
    summary="Serve a generated code review HTML report",
)
async def serve_report(filename: str):
    # Security: only serve .html files from the reports directories, no path traversal
    safe_name = os.path.basename(filename)
    if not safe_name.endswith(".html") or safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    # Search all report directories in priority order
    for report_dir in (_REVIEW_REPORTS_DIR, _FAILURE_REPORTS_DIR,
                       _REPO_REPORTS_DIR, _ANALYSIS_REPORTS_DIR):
        filepath = os.path.join(report_dir, safe_name)
        if os.path.isfile(filepath):
            return FileResponse(filepath, media_type="text/html")
    raise HTTPException(status_code=404, detail="Report not found")


# ------------------------------------------------
# API ENDPOINTS
# ------------------------------------------------

@app.get(
    "/analysis-bundle/{jira_key}",
    summary="Full analysis bundle for AI test generation",
    description=(
        "Fetches Jira issue context (summary, description, repro steps) "
        "and all linked TFS changeset code diffs. Returns a structured "
        "payload optimized for LLM-driven test scenario generation."
    ),
)
def api_analysis_bundle(jira_key: str, _key=Depends(verify_api_key)):
    try:
        return build_analysis_bundle(jira_key)
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        detail = f"API error: {e}"
        if status == 401:
            detail = (
                "Authentication failed. Check TFS_PAT, JIRA_EMAIL, "
                "and JIRA_API_TOKEN environment variables."
            )
        raise HTTPException(status_code=status, detail=detail)
    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail="Cannot reach TFS or Jira server.",
        )


@app.get(
    "/jira-context/{jira_key}",
    summary="Get structured Jira issue context",
    description=(
        "Returns summary, description, repro steps, testing recommendations, "
        "linked issues, and parent epic for a Jira key."
    ),
)
def api_jira_context(jira_key: str, _key=Depends(verify_api_key)):
    try:
        return fetch_jira_context(jira_key)
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"Jira API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach Jira server.")


@app.get(
    "/changeset-diffs/{changeset_id}",
    summary="Get code diffs for a TFS changeset",
    description=(
        "Returns changeset metadata and unified diffs for every changed "
        "file in the given TFVC changeset."
    ),
)
def api_changeset_diffs(changeset_id: int, _key=Depends(verify_api_key)):
    try:
        return fetch_changeset_with_diffs(changeset_id)
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"TFS API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach TFS server.")


@app.get(
    "/shelvesets/search",
    summary="Search TFS shelvesets",
    description=(
        "Search for TFS shelvesets by name, owner, or Jira key. "
        "Returns matching shelvesets with metadata."
    ),
)
def api_search_shelvesets(
    name: str = None,
    owner: str = None,
    jira_key: str = None,
    top: int = 50,
    _key=Depends(verify_api_key),
):
    try:
        if jira_key:
            return {"shelvesets": find_shelvesets_by_jira_key(jira_key)}
        return {"shelvesets": search_shelvesets(name=name, owner=owner, top=top)}
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"TFS API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach TFS server.")


@app.get(
    "/shelvesets/{shelveset_name}/diffs",
    summary="Get code diffs for a TFS shelveset",
    description=(
        "Returns shelveset metadata and unified diffs for every changed "
        "file in the given shelveset. Requires the shelveset owner "
        "(unique name, e.g. DOMAIN\\user)."
    ),
)
def api_shelveset_diffs(
    shelveset_name: str,
    owner: str,
    _key=Depends(verify_api_key),
):
    try:
        return fetch_shelveset_with_diffs(shelveset_name, owner)
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"TFS API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach TFS server.")


@app.post(
    "/shelvesets/{shelveset_name}/comments",
    summary="Post a discussion comment on a TFS shelveset",
    description=(
        "Posts a Markdown-formatted comment to the discussion thread of a TFS shelveset. "
        "Use this to attach code review summaries directly to the shelveset page in TFS."
    ),
)
def api_post_shelveset_comment(
    shelveset_name: str,
    owner: str,
    comment: str = None,
    _key=Depends(verify_api_key),
    body: dict = None,
):
    comment_text = comment or (body.get("comment") if body else None)
    if not comment_text:
        raise HTTPException(status_code=400, detail="'comment' is required (query param or JSON body).")
    try:
        result = post_shelveset_comment(shelveset_name, owner, comment_text)
        return {
            "status": "posted",
            "shelveset": shelveset_name,
            "owner": owner,
            "web_url": get_shelveset_web_url(shelveset_name, owner),
            "thread": result,
        }
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        detail = e.response.text[:500] if e.response is not None else str(e)
        raise HTTPException(status_code=status, detail=f"TFS Discussion API error: {detail}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach TFS server.")


@app.get(
    "/shelvesets/{shelveset_name}/comments",
    summary="Get discussion comments on a TFS shelveset",
    description="Returns all discussion threads and comments on a TFS shelveset.",
)
def api_get_shelveset_comments(
    shelveset_name: str,
    owner: str,
    _key=Depends(verify_api_key),
):
    try:
        threads = get_shelveset_discussion_threads(shelveset_name, owner)
        return {
            "shelveset": shelveset_name,
            "owner": owner,
            "web_url": get_shelveset_web_url(shelveset_name, owner),
            "threads": threads,
            "total": len(threads),
        }
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"TFS API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach TFS server.")


# ------------------------------------------------
# LEGACY ENDPOINT (backward compatible)
# ------------------------------------------------

def detect_intent(query):
    q = query.lower()
    changeset = re.search(r"changeset\s*(\d+)", q)
    build = re.search(r"build\s*([\d\.]+)", q)
    jira = re.search(r"[A-Z]+-\d+", query)
    shelveset_kw = any(kw in q for kw in ("shelveset", "shelve", "shelved"))

    # GitHub intents: owner/repo or PR patterns
    github_pr_match = re.search(
        r"(?:github|gh|pr|pull\s*request)\s+(?:(?:https?://github\.com/)?(\S+?)/(\S+?)(?:/pull/|#)(\d+))",
        q,
    )
    github_repo_match = re.search(
        r"(?:github|gh)\s+(?:repo\s+)?(?:https?://github\.com/)?(\S+?)/(\S+?)(?:\s|$)",
        q,
    )
    github_kw = any(kw in q for kw in ("github", "gh repo", "pull request", " pr #"))
    code_scanning_kw = any(kw in q for kw in ("code scan", "code-scan", "scanning alert", "codeql"))
    dependabot_kw = any(kw in q for kw in ("dependabot", "dependency alert", "vulnerability alert", "dep alert"))

    # GitHub PR by URL or explicit reference
    if github_pr_match:
        return "github_pr", {
            "owner": github_pr_match.group(1),
            "repo": github_pr_match.group(2),
            "pr_number": int(github_pr_match.group(3)),
        }

    # GitHub repo lookup (owner/repo form)
    if github_kw and github_repo_match and not github_pr_match:
        owner = github_repo_match.group(1)
        repo_name = github_repo_match.group(2).rstrip("/")
        if code_scanning_kw:
            return "github_code_scanning", {"owner": owner, "repo": repo_name}
        if dependabot_kw:
            return "github_dependabot", {"owner": owner, "repo": repo_name}
        if "pull" in q or "pr" in q:
            return "github_pulls", {"owner": owner, "repo": repo_name}
        return "github_repo", {"owner": owner, "repo": repo_name}

    # Alias-based or short-name repo reference (no owner/ prefix)
    alias_match = re.search(r"(?:github|gh)\s+(?:repo\s+)?(\S+)", q)
    if github_kw and alias_match and not github_repo_match and not github_pr_match:
        alias = alias_match.group(1).strip().rstrip("/")
        owner, repo_name = resolve_github_repo(alias)
        if code_scanning_kw:
            return "github_code_scanning", {"owner": owner, "repo": repo_name}
        if dependabot_kw:
            return "github_dependabot", {"owner": owner, "repo": repo_name}
        if "pull" in q or "pr" in q:
            return "github_pulls", {"owner": owner, "repo": repo_name}
        return "github_repo", {"owner": owner, "repo": repo_name}

    # Shelveset intents take priority when keyword is present
    if shelveset_kw:
        if jira:
            return "shelveset_by_jira", jira.group()
        # Check for owner/author pattern
        owner_match = re.search(
            r"(?:by|author|owner|from|created by)\s+([\w\\. ]+)",
            q,
        )
        if owner_match:
            return "shelveset_by_owner", owner_match.group(1).strip()
        # Check for an explicit shelveset name / ID
        name_match = re.search(
            r"shelveset\s+(?:id\s+|named?\s+)?([^\s,]+)", q,
        )
        if name_match:
            return "shelveset_by_name", name_match.group(1).strip()
        return "shelveset_list", None

    if jira:
        jira_key = jira.group()
        if "commit" in q or "changeset" in q:
            return "jira_commits", jira_key
        return "analysis_bundle", jira_key

    if changeset:
        return "changeset_diffs", int(changeset.group(1))

    if build:
        return "build", build.group(1)

    return "unknown", query


@app.post("/devops-agent", summary="Legacy natural-language query endpoint")
def devops_agent(payload: dict, _key=Depends(verify_api_key)):
    query = payload.get("query")
    if not query:
        raise HTTPException(
            status_code=400,
            detail="Missing 'query' field in request body.",
        )
    try:
        intent, value = detect_intent(query)

        if intent == "github_pr":
            return fetch_github_pr_with_diffs(value["owner"], value["repo"], value["pr_number"])

        if intent == "github_pulls":
            pulls = list_github_pulls(value["owner"], value["repo"])
            return {"type": "github_pulls", "owner": value["owner"], "repo": value["repo"], "pulls": pulls, "total": len(pulls)}

        if intent == "github_repo":
            return {"type": "github_repo", **fetch_github_repo(value["owner"], value["repo"])}

        if intent == "github_code_scanning":
            alerts = list_code_scanning_alerts(value["owner"], value["repo"])
            return {"type": "github_code_scanning", "owner": value["owner"], "repo": value["repo"], "alerts": alerts, "total": len(alerts)}

        if intent == "github_dependabot":
            alerts = list_dependabot_alerts(value["owner"], value["repo"])
            return {"type": "github_dependabot", "owner": value["owner"], "repo": value["repo"], "alerts": alerts, "total": len(alerts)}

        if intent == "shelveset_by_jira":
            shelvesets = find_shelvesets_by_jira_key(value)
            if not shelvesets:
                return {"message": f"No shelvesets found matching Jira key {value}."}
            results = []
            for ss in shelvesets:
                detail = fetch_shelveset_with_diffs(
                    ss["name"], ss["owner_unique_name"],
                )
                results.append(detail)
            return {"type": "shelvesets", "shelvesets": results, "total": len(results)}

        if intent == "shelveset_by_owner":
            shelvesets = search_shelvesets(owner=value)
            if not shelvesets:
                return {"message": f"No shelvesets found for owner '{value}'."}
            return {"type": "shelvesets", "shelvesets": shelvesets, "total": len(shelvesets)}

        if intent == "shelveset_by_name":
            shelvesets = search_shelvesets(name=value)
            if not shelvesets:
                return {"message": f"No shelveset found with name '{value}'."}
            results = []
            for ss in shelvesets:
                detail = fetch_shelveset_with_diffs(
                    ss["name"], ss["owner_unique_name"],
                )
                results.append(detail)
            return {"type": "shelvesets", "shelvesets": results, "total": len(results)}

        if intent == "shelveset_list":
            shelvesets = search_shelvesets(top=20)
            return {"type": "shelvesets", "shelvesets": shelvesets, "total": len(shelvesets)}

        if intent == "analysis_bundle":
            return build_analysis_bundle(value)
        if intent == "changeset_diffs":
            return fetch_changeset_with_diffs(value)
        if intent == "build":
            return {
                "type": "build",
                "version": value,
                "build_url": f"{BUILD_DIRECTOR}/Builds/BuildInfo/OnBase/{value}",
            }
        return {
            "message": (
                "Unable to determine request. Try: CSFMD-10783, "
                "changeset 403399, build 26.1.0.336, "
                "'shelveset for SBPWC-12559', "
                "'github owner/repo', 'github owner/repo pr #123', "
                "'github api-server code scanning alerts', or "
                "'github ng-apps dependabot alerts'"
            ),
        }

    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        detail = f"API error {status}: {e}"
        if status == 401:
            detail = "Authentication failed. Check environment variables."
        raise HTTPException(status_code=status, detail=detail)
    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail="Could not connect to TFS or Jira server.",
        )
# ------------------------------------------------
# BRANCH CONFIGURATION - OnBase Version to TFS Path
# ------------------------------------------------

ONBASE_BRANCHES = {
    "DEV":   "$/OnBase/DEV/Core/OnBase.NET",
    "18":    "$/OnBase/REL/18/Service_179/Core/OnBase.NET",
    "19.8":  "$/OnBase/REL/19/Service_19.8/Core/OnBase.NET",
    "19.12": "$/OnBase/REL/19/Service_19.12/Core/OnBase.NET",
    "20":    "$/OnBase/REL/20/Service_20.3/Core/OnBase.NET",
    "20.3":  "$/OnBase/REL/20/Service_20.3/Core/OnBase.NET",
    "20.8":  "$/OnBase/REL/20/Service_20.8/Core/OnBase.NET",
    "21":    "$/OnBase/REL/21/Service_21.1/Core/OnBase.NET",
    "21.1":  "$/OnBase/REL/21/Service_21.1/Core/OnBase.NET",
    "22":    "$/OnBase/REL/22/Service_22.1/Core/OnBase.NET",
    "22.1":  "$/OnBase/REL/22/Service_22.1/Core/OnBase.NET",
    "23":    "$/OnBase/REL/23/Service_23.1/Core/OnBase.NET",
    "23.1":  "$/OnBase/REL/23/Service_23.1/Core/OnBase.NET",
    "24":    "$/OnBase/REL/24/Service_24.1/Core/OnBase.NET",
    "24.1":  "$/OnBase/REL/24/Service_24.1/Core/OnBase.NET",
    "25.1":  "$/OnBase/REL/25/Service_25.1/Core/OnBase.NET",
    "25.2":  "$/OnBase/REL/25/Service_25.2/Core/OnBase.NET",
}

DEFAULT_TARGET_BRANCH = "DEV"

LOF_JQL_FILTER = (
    'project = "SBP - WorkView" AND issuetype = Bug '
    'AND "Defect Category" = "Loss of Functionality" '
    'AND backport != Yes ORDER BY created DESC'
)
LOF_FILTER_ID = "50387"

LOF_HIGH_RISK_PATHS = [
    "WorkView/Hyland.WorkView.Core/Services/FilterService",
    "WorkView/Hyland.WorkView.Core/Services/FilterQueryService",
    "WorkView/Hyland.WorkView.Core/Services/ObjectService",
    "WorkView/Hyland.WorkView.Core/Services/PermissionService",
    "WorkView/Hyland.WorkView.Core/Services/AttributeService",
    "WorkView/Hyland.WorkView.Core/Services/AttributeEncryptionService",
    "WorkView/Hyland.WorkView.InterfaceServices",
    "WorkView/RestApis",
    "WorkView/Hyland.WorkView.Core/Services/SecurityResolver",
    "WorkView/Hyland.WorkView.Core/Services/UserSecurityService",
]


def resolve_branch_path(version: str) -> str:
    key = version.strip().upper() if version.strip().upper() == "DEV" else version.strip()
    path = ONBASE_BRANCHES.get(key)
    if not path:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown OnBase version '{version}'. "
                f"Valid values: {', '.join(sorted(ONBASE_BRANCHES.keys()))}"
            ),
        )
    return path


def get_branch_file_content(branch_root: str, relative_path: str) -> Optional[str]:
    full_path = f"{branch_root}/{relative_path}"
    return get_latest_file_content(full_path)


# ------------------------------------------------
# HELPERS - BRANCH COMPARISON
# ------------------------------------------------

def list_branch_files(tfvc_folder: str, recursion: str = "Full") -> list[dict]:
    encoded = quote(tfvc_folder, safe="/$")
    url = (
        f"{TFS_URL}/tfvc/items"
        f"?scopePath={encoded}"
        f"&recursionLevel={recursion}"
        f"&api-version=5.0"
    )
    data = tfs_get(url)
    items = []
    for item in data.get("value", []):
        if item.get("isFolder", False):
            continue
        items.append({
            "path": item.get("path", ""),
            "size": item.get("size", 0),
            "changesetVersion": item.get("version", 0),
        })
    return items


def get_branch_changesets(
    tfvc_path: str,
    from_date: str = None,
    to_date: str = None,
    top: int = 100,
) -> list[dict]:
    params = {
        "searchCriteria.itemPath": tfvc_path,
        "$top": str(top),
        "api-version": "5.0",
    }
    if from_date:
        params["searchCriteria.fromDate"] = from_date
    if to_date:
        params["searchCriteria.toDate"] = to_date

    url = f"{TFS_URL}/tfvc/changesets"
    r = requests.get(url, params=params, auth=TFS_AUTH, timeout=60)
    r.raise_for_status()
    data = r.json()

    results = []
    for cs in data.get("value", []):
        results.append({
            "changeset_id": cs.get("changesetId"),
            "comment": cs.get("comment", ""),
            "author": cs.get("author", {}).get("displayName", ""),
            "date": cs.get("createdDate", ""),
        })
    return results


def compute_branch_file_diff(
    relative_path: str,
    source_root: str,
    target_root: str,
    max_lines: int = MAX_DIFF_LINES,
) -> dict:
    ext = os.path.splitext(relative_path)[1].lower()
    if ext in BINARY_EXTENSIONS:
        return {
            "relative_path": relative_path,
            "diff": "[Binary file - diff not available]",
            "lines_added": 0,
            "lines_removed": 0,
            "source_exists": True,
            "target_exists": True,
        }

    source_content = get_branch_file_content(source_root, relative_path) or ""
    target_content = get_branch_file_content(target_root, relative_path) or ""

    source_exists = source_content != ""
    target_exists = target_content != ""

    if source_content == target_content:
        return {
            "relative_path": relative_path,
            "diff": "",
            "lines_added": 0,
            "lines_removed": 0,
            "identical": True,
            "source_exists": source_exists,
            "target_exists": target_exists,
        }

    diff_lines = list(difflib.unified_diff(
        source_content.splitlines(keepends=True),
        target_content.splitlines(keepends=True),
        fromfile=f"source/{relative_path}",
        tofile=f"target/{relative_path}",
        n=3,
    ))

    added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))

    truncated = max_lines > 0 and len(diff_lines) > max_lines
    if truncated:
        diff_lines = diff_lines[:max_lines]

    diff_text = "".join(diff_lines)
    if truncated:
        diff_text += f"\n... [truncated - first {max_lines} lines shown]"

    return {
        "relative_path": relative_path,
        "diff": diff_text,
        "lines_added": added,
        "lines_removed": removed,
        "identical": False,
        "source_exists": source_exists,
        "target_exists": target_exists,
    }


def jira_search(jql: str, max_results: int = 50) -> list[dict]:
    if not JIRA_EMAIL or not JIRA_API_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="JIRA_EMAIL and JIRA_API_TOKEN environment variables must be set.",
        )
    # Jira Cloud deprecated GET /rest/api/3/search (returns 410).
    # Use POST /rest/api/3/search/jql instead.
    url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
    body = {
        "jql": jql,
        "maxResults": max_results,
        "fields": [
            "summary", "status", "issuetype", "priority", "assignee",
            "created", "fixVersions", "customfield_10830", "customfield_11816",
            "description", "issuelinks", "parent",
        ],
    }
    r = requests.post(
        url,
        json=body,
        auth=(JIRA_EMAIL, JIRA_API_TOKEN),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()

    issues = []
    for issue in data.get("issues", []):
        fields = issue.get("fields", {})
        fix_versions = [
            v.get("name", "") for v in fields.get("fixVersions", [])
        ]
        issues.append({
            "key": issue.get("key", ""),
            "summary": fields.get("summary", ""),
            "status": fields.get("status", {}).get("name", ""),
            "priority": fields.get("priority", {}).get("name", ""),
            "assignee": (fields.get("assignee") or {}).get("displayName", ""),
            "created": fields.get("created", ""),
            "fix_versions": fix_versions,
            "description_preview": adf_to_text(
                fields.get("description")
            )[:500],
        })

    return issues


# ------------------------------------------------
# LOF ANALYSIS ENDPOINTS
# ------------------------------------------------

@app.get(
    "/branch-diff",
    summary="Diff a file between two OnBase version branches",
    description=(
        "Compares a file (by relative path) between a source and target "
        "OnBase branch. Source/target can be version labels like '20', "
        "'24.1', 'DEV', etc. Returns a unified diff."
    ),
)
def api_branch_diff(
    relative_path: str,
    source_version: str,
    target_version: str = DEFAULT_TARGET_BRANCH,
    max_lines: int = MAX_DIFF_LINES,
    _key=Depends(verify_api_key),
):
    try:
        source_root = resolve_branch_path(source_version)
        target_root = resolve_branch_path(target_version)
        result = compute_branch_file_diff(relative_path, source_root, target_root, max_lines=max_lines)
        return {
            "source_version": source_version,
            "target_version": target_version,
            "source_branch": source_root,
            "target_branch": target_root,
            **result,
        }
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"TFS API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach TFS server.")


@app.get(
    "/branch-file-content",
    summary="Get file content from an OnBase version branch",
    description=(
        "Fetches the content of a file from a specific OnBase version branch. "
        "Supports optional start_line/end_line for partial reads (1-based). "
        "Use max_lines=0 to get the entire file."
    ),
)
def api_branch_file_content(
    relative_path: str,
    version: str = DEFAULT_TARGET_BRANCH,
    start_line: int = 0,
    end_line: int = 0,
    _key=Depends(verify_api_key),
):
    try:
        branch_root = resolve_branch_path(version)
        content_text = get_branch_file_content(branch_root, relative_path)
        if content_text is None:
            raise HTTPException(
                status_code=404,
                detail=f"File not found: {relative_path} in branch {version}",
            )
        lines = content_text.splitlines()
        total_lines = len(lines)
        sl = max(1, start_line) if start_line > 0 else 1
        el = min(total_lines, end_line) if end_line > 0 else total_lines
        selected = lines[sl - 1 : el]
        return {
            "version": version,
            "branch_root": branch_root,
            "relative_path": relative_path,
            "total_lines": total_lines,
            "start_line": sl,
            "end_line": el,
            "content": "\n".join(selected),
        }
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"TFS API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach TFS server.")


@app.get(
    "/branch-files",
    summary="List files under an OnBase branch path",
    description=(
        "Lists all files under a given relative folder path within a "
        "specific OnBase version branch. Use recursion=OneLevel for "
        "shallow listing or Full for recursive."
    ),
)
def api_branch_files(
    relative_path: str,
    version: str = DEFAULT_TARGET_BRANCH,
    recursion: str = "Full",
    _key=Depends(verify_api_key),
):
    try:
        branch_root = resolve_branch_path(version)
        full_path = f"{branch_root}/{relative_path}"
        files = list_branch_files(full_path, recursion)
        return {
            "version": version,
            "branch_root": branch_root,
            "folder": relative_path,
            "files": files,
            "total_files": len(files),
        }
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"TFS API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach TFS server.")


@app.get(
    "/branch-history",
    summary="Get changeset history for a branch path",
    description=(
        "Returns changesets affecting a folder or file within an OnBase "
        "version branch, optionally filtered by date range. "
        "Dates should be ISO format (e.g. 2025-01-01)."
    ),
)
def api_branch_history(
    relative_path: str,
    version: str = DEFAULT_TARGET_BRANCH,
    from_date: str = None,
    to_date: str = None,
    top: int = 100,
    _key=Depends(verify_api_key),
):
    try:
        branch_root = resolve_branch_path(version)
        full_path = f"{branch_root}/{relative_path}"
        changesets = get_branch_changesets(full_path, from_date, to_date, top)
        return {
            "version": version,
            "branch_root": branch_root,
            "path": relative_path,
            "from_date": from_date,
            "to_date": to_date,
            "changesets": changesets,
            "total_changesets": len(changesets),
        }
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"TFS API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach TFS server.")


@app.get(
    "/jira-search",
    summary="Execute a JQL search",
    description=(
        "Runs a JQL query against Jira and returns structured issue data. "
        "Use for searching Loss of Functionality bugs or any JQL query."
    ),
)
def api_jira_search(
    jql: str = None,
    filter_id: str = None,
    max_results: int = 50,
    _key=Depends(verify_api_key),
):
    try:
        if filter_id:
            effective_jql = f"filter={filter_id}"
        elif jql:
            effective_jql = jql
        else:
            raise HTTPException(
                status_code=400,
                detail="Provide either 'jql' or 'filter_id' parameter.",
            )
        issues = jira_search(effective_jql, max_results)
        return {
            "jql": effective_jql,
            "issues": issues,
            "total_returned": len(issues),
        }
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"Jira API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach Jira server.")


@app.get(
    "/branches",
    summary="List all known OnBase version branches",
    description="Returns the mapping of OnBase version labels to TFS branch paths.",
)
def api_list_branches(_key=Depends(verify_api_key)):
    return {
        "branches": {
            k: v for k, v in sorted(ONBASE_BRANCHES.items())
        },
        "default_target": DEFAULT_TARGET_BRANCH,
    }


@app.post(
    "/lof-analysis",
    summary="Run Loss of Functionality analysis between two branches",
    description=(
        "Compares high-risk WorkView files between a source OnBase version "
        "and a target version (default: DEV/26.1). Identifies code "
        "divergences that could indicate loss of functionality after "
        "upgrade. Optionally fetches LoF bugs from Jira for correlation."
    ),
)
def api_lof_analysis(
    payload: dict,
    _key=Depends(verify_api_key),
):
    source_version = payload.get("source_version")
    target_version = payload.get("target_version", DEFAULT_TARGET_BRANCH)
    include_jira = payload.get("include_jira", False)
    custom_paths = payload.get("paths")

    if not source_version:
        raise HTTPException(
            status_code=400,
            detail="'source_version' is required (e.g. '20', '22.1', '24.1').",
        )

    try:
        source_root = resolve_branch_path(source_version)
        target_root = resolve_branch_path(target_version)

        scan_paths = custom_paths if custom_paths else LOF_HIGH_RISK_PATHS
        divergences = []

        for rel_path in scan_paths:
            source_folder = f"{source_root}/{rel_path}"
            target_folder = f"{target_root}/{rel_path}"

            try:
                source_files_raw = list_branch_files(source_folder, "Full")
            except Exception:
                source_files_raw = []
            try:
                target_files_raw = list_branch_files(target_folder, "Full")
            except Exception:
                target_files_raw = []

            def to_relative(files, root):
                result = {}
                for f in files:
                    full = f["path"]
                    if root in full:
                        r = full.split(root, 1)[1].lstrip("/")
                        result[r] = f
                return result

            source_files = to_relative(source_files_raw, source_root)
            target_files = to_relative(target_files_raw, target_root)

            all_files = set(source_files.keys()) | set(target_files.keys())

            for file_rel in sorted(all_files):
                in_source = file_rel in source_files
                in_target = file_rel in target_files

                if in_source and in_target:
                    src_ver = source_files[file_rel].get("changesetVersion", 0)
                    tgt_ver = target_files[file_rel].get("changesetVersion", 0)
                    if src_ver == tgt_ver:
                        continue
                    divergences.append({
                        "relative_path": file_rel,
                        "scan_area": rel_path,
                        "status": "modified",
                        "source_changeset": src_ver,
                        "target_changeset": tgt_ver,
                    })
                elif in_target and not in_source:
                    divergences.append({
                        "relative_path": file_rel,
                        "scan_area": rel_path,
                        "status": "added_in_target",
                        "note": f"File exists in {target_version} but not {source_version}",
                    })
                elif in_source and not in_target:
                    divergences.append({
                        "relative_path": file_rel,
                        "scan_area": rel_path,
                        "status": "removed_in_target",
                        "note": f"File exists in {source_version} but not {target_version}",
                    })

        result = {
            "source_version": source_version,
            "target_version": target_version,
            "source_branch": source_root,
            "target_branch": target_root,
            "scanned_paths": scan_paths,
            "divergences": divergences,
            "total_divergences": len(divergences),
            "summary": {
                "modified": sum(1 for d in divergences if d["status"] == "modified"),
                "added_in_target": sum(1 for d in divergences if d["status"] == "added_in_target"),
                "removed_in_target": sum(1 for d in divergences if d["status"] == "removed_in_target"),
            },
        }

        if include_jira:
            try:
                lof_bugs = jira_search(LOF_JQL_FILTER, max_results=100)
                result["lof_bugs"] = lof_bugs
                result["total_lof_bugs"] = len(lof_bugs)
            except Exception as e:
                result["lof_bugs_error"] = str(e)

        return result

    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail="Cannot reach TFS or Jira server.",
        )





# ------------------------------------------------
# CONFIGURATION - TFS BUILD / PIPELINES
# ------------------------------------------------

# Project-scoped TFS API base (builds are project-level)
TFS_PROJECT_URL = "http://dev-tfs:8080/tfs/HylandCollection/OnBase/_apis"

# Well-known pipeline folder paths
PIPELINE_FOLDERS = {
    "official_builds": r"\- OnBase Official Builds -",
    "workview_tests": r"\WorkView\Tests",
    "dev_official_builds": r"\- OnBase Official Builds -\Dev Official Builds",
}


# ------------------------------------------------
# HELPERS - BUILD DIRECTOR
# ------------------------------------------------

# Version aliases: map user-friendly names to BuildDirector version keys
BUILDDIRECTOR_VERSION_MAP = {
    "DEV": "26.1",  # DEV maps to the latest major version
    "26.1": "26.1", "26": "26.1",
    "25.2": "25.2",
    "25.1": "25.1", "25": "25.1",
    "24.1": "24.1", "24": "24.1",
    "23.1": "23.1", "23": "23.1",
    "22.1": "22.1", "22": "22.1",
    "21.1": "21.1", "21": "21.1",
    "20.8": "20.8",
    "20.3": "20.3", "20": "20.3",
    "19.12": "19.12",
    "19.8": "19.8", "19": "19.8",
    "18.1": "18.1", "18.0": "18.0", "18": "18.0",
}


# BuildDirector uses Windows Integrated Auth (Negotiate/Kerberos).
# Python requests can't do this natively, so we use a helper script.
_BD_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bd_fetch.ps1")

# -- BuildDirector response cache (TTL-based) --
# PowerShell subprocess calls take 10-40s each. Cache responses for 30 minutes.
# BuildDirector data changes slowly (builds run hourly at most), so a longer
# TTL avoids expensive cache misses between queries.
_BD_CACHE: dict[str, tuple[float, any]] = {}
_BD_CACHE_TTL = 1800  # seconds (30 minutes)


def _bd_get_cached(path: str, top: int = 0) -> dict | list:
    """Cached wrapper around _bd_get. Returns cached data if available and fresh.

    If a cache entry exists for the same path with a LARGER top value,
    reuse that data (it's a superset). This avoids redundant calls when
    cache warming fetches top=200 but callers use top=10 or top=20.
    """
    cache_key = f"{path}|{top}"
    now = _time.time()
    # Exact match
    if cache_key in _BD_CACHE:
        ts, data = _BD_CACHE[cache_key]
        if now - ts < _BD_CACHE_TTL:
            return data
    # Check if a larger top is cached (superset of our request)
    if top > 0:
        for k, (ts, data) in _BD_CACHE.items():
            if k.startswith(f"{path}|") and now - ts < _BD_CACHE_TTL:
                cached_top = int(k.split("|")[1])
                if cached_top >= top:
                    return data
    result = _bd_get(path, top)
    _BD_CACHE[cache_key] = (now, result)
    return result


def _bd_get(path: str, top: int = 0) -> dict | list:
    """GET request against the BuildDirector API using Windows integrated auth.

    Uses requests with NTLM auth for direct HTTP calls — much faster than
    spawning a PowerShell subprocess (~5-15s vs 60-120s).
    Optional `top` parameter: slices the first N items from array responses.
    """
    url = f"{BUILD_DIRECTOR}/api/{path}"
    try:
        from requests_ntlm import HttpNtlmAuth
        resp = requests.get(url, auth=HttpNtlmAuth('', ''), timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except ImportError:
        # Fallback to PowerShell if requests-ntlm not installed
        _logger.warning("requests-ntlm not installed, falling back to PowerShell for BuildDirector")
        ps_cmd = (
            f'$wc = New-Object System.Net.WebClient; '
            f'$wc.UseDefaultCredentials = $true; '
            f'$json = $wc.DownloadString("{url}"); '
            f'$json'
        )
        result = _subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=502,
                detail=f"BuildDirector request failed: {result.stderr.strip()[:500]}",
            )
        data = _json.loads(result.stdout)

    # Slice top N items if requested (for builds list endpoints)
    if top > 0 and isinstance(data, list):
        data = data[:top]
    return data


def _resolve_bd_version(version: str) -> str:
    """Resolve a user-provided version string to a BuildDirector version key."""
    v = version.strip()
    # Exact 4-part build number (e.g. 24.1.38.1000) ? extract major.minor
    parts = v.split(".")
    if len(parts) == 4:
        v = f"{parts[0]}.{parts[1]}"
    key = BUILDDIRECTOR_VERSION_MAP.get(v) or BUILDDIRECTOR_VERSION_MAP.get(v.upper())
    if not key:
        # Try as-is (might be a valid BuildDirector version like "17.9")
        key = v
    return key


def _format_bd_build(b: dict) -> dict:
    """Format a raw BuildDirector build object into a clean dict."""
    v = b.get("version", {})
    major = v.get("major", 0)
    minor = v.get("minor", 0)
    sp = v.get("servicePack", 0)
    build_num = b.get("number", 0)
    full_version = f"{major}.{minor}.{sp}.{build_num}"

    locations = b.get("locations", [])
    network_loc = next(
        (loc.get("location", "") for loc in locations if loc.get("type") == "Network"),
        locations[0].get("location", "") if locations else "",
    )

    return {
        "version": full_version,
        "major_minor": f"{major}.{minor}",
        "service_pack": sp,
        "build_number": build_num,
        "is_complete": b.get("isComplete", False),
        "has_failed": b.get("hasFailed", False),
        "status": "Failed" if b.get("hasFailed") else ("Complete" if b.get("isComplete") else "In Progress"),
        "started_on": b.get("startedOn", ""),
        "completed_on": b.get("completedOn", ""),
        "requested_by": b.get("requestedBy", ""),
        "built_for": (b.get("builtFor") or "").strip().rstrip("-").strip(),
        "workstation": b.get("workstation", ""),
        "location": network_loc,
        "builddirector_url": f"{BUILD_DIRECTOR}/Builds/BuildInfo/OnBase/{full_version}",
    }


def bd_list_versions() -> list[dict]:
    """List all OnBase versions available in BuildDirector."""
    data = _bd_get_cached("OnBase/versions")
    # Group by major.minor, take the latest servicePack for each
    seen = {}
    for v in data:
        key = f"{v['major']}.{v['minor']}"
        if key not in seen or v.get("servicePack", 0) > seen[key].get("servicePack", 0):
            seen[key] = v
    versions = []
    for key in sorted(seen.keys(), key=lambda x: [int(p) for p in x.split(".")], reverse=True):
        v = seen[key]
        versions.append({
            "version": key,
            "latest_service_pack": v.get("servicePack", 0),
            "is_disabled": v.get("isDisabled", False),
            "friendly_name": v.get("friendlyName"),
        })
    return versions


def bd_list_builds(version: str, top: int = 10, successful_only: bool = False) -> dict:
    """List builds for a given OnBase version from BuildDirector.

    Args:
        version: OnBase version (e.g. '24.1', 'DEV', '25.2', or full '24.1.38.1000')
        top: max number of builds to return (most recent first)
        successful_only: if True, only include completed, non-failed builds
    """
    bd_version = _resolve_bd_version(version)
    # Fetch more than requested to allow filtering; builds come sorted newest-first
    fetch_top = top * 3 if successful_only else top
    data = _bd_get_cached(f"onbase/builds/allSlim/{bd_version}", top=max(fetch_top, 20))
    # Data comes sorted by most recent first
    builds = []
    for b in data:
        formatted = _format_bd_build(b)
        if successful_only and (formatted["has_failed"] or not formatted["is_complete"]):
            continue
        builds.append(formatted)
        if len(builds) >= top:
            break
    return {
        "version": bd_version,
        "builds": builds,
        "total_returned": len(builds),
        "total_available": len(data),
    }


def bd_get_latest_build(version: str) -> dict:
    """Get the latest successful build for an OnBase version from BuildDirector.

    Args:
        version: OnBase version (e.g. '24.1', 'DEV', '25.2')
    """
    bd_version = _resolve_bd_version(version)
    # Fetch top 10 — the latest successful is almost always in the first few
    data = _bd_get_cached(f"onbase/builds/allSlim/{bd_version}", top=10)
    # Find the most recent complete, non-failed build
    for b in data:
        if b.get("isComplete") and not b.get("hasFailed"):
            return {
                "version": bd_version,
                "latest_build": _format_bd_build(b),
                "total_builds": len(data),
            }
    # No successful builds found — return the most recent anyway
    if data:
        return {
            "version": bd_version,
            "latest_build": _format_bd_build(data[0]),
            "total_builds": len(data),
            "warning": "No successful builds found; returning most recent build regardless of status.",
        }
    return {
        "version": bd_version,
        "latest_build": None,
        "total_builds": 0,
        "error": f"No builds found for version {bd_version} in BuildDirector.",
    }


def bd_search_build(build_number: str) -> dict:
    """Search for a specific build by full version number (e.g. '24.1.38.1000').

    Args:
        build_number: Full 4-part build number like '24.1.38.1000'
    """
    parts = build_number.strip().split(".")
    if len(parts) != 4:
        return {"error": f"Expected 4-part version (e.g. 24.1.38.1000), got '{build_number}'"}
    major_minor = f"{parts[0]}.{parts[1]}"
    target_sp = int(parts[2])
    target_num = int(parts[3])

    data = _bd_get_cached(f"onbase/builds/allSlim/{major_minor}", top=200)
    for b in data:
        v = b.get("version", {})
        if v.get("servicePack") == target_sp and b.get("number") == target_num:
            return {
                "found": True,
                "build": _format_bd_build(b),
            }
    return {
        "found": False,
        "error": f"Build {build_number} not found in BuildDirector.",
        "available_count": len(data),
    }


# -- BuildDirector – Version-ID map (for calendar API) --
# Maps major.minor ? BuildDirector internal versionId.
# Built dynamically on first use from /api/OnBase/versions.
_BD_VERSION_ID_CACHE: dict[str, int] = {}


def _bd_get_version_id(version: str) -> int:
    """Resolve a major.minor version string to its BuildDirector versionId.

    The calendar events API requires the numeric versionId, not the version string.
    We cache the full map on first call.
    """
    bd_ver = _resolve_bd_version(version)
    if not _BD_VERSION_ID_CACHE:
        all_versions = _bd_get_cached("OnBase/versions")
        # First pass: collect lowest servicePack per major.minor
        best_sp: dict[str, int] = {}  # key -> lowest sp seen
        for v in all_versions:
            key = f"{v['major']}.{v['minor']}"
            sp = v.get("servicePack", 0)
            if key not in best_sp or sp < best_sp[key]:
                best_sp[key] = sp
                _BD_VERSION_ID_CACHE[key] = v["id"]
    vid = _BD_VERSION_ID_CACHE.get(bd_ver)
    if not vid:
        raise HTTPException(
            status_code=404,
            detail=f"No BuildDirector versionId found for '{bd_ver}'. "
                   f"Known versions: {sorted(_BD_VERSION_ID_CACHE.keys())}",
        )
    return vid


def bd_get_botw_builds(version: str, top: int = 5) -> dict:
    """Get the most recent Build Of The Week (BOTW) builds for an OnBase version.

    BOTW builds have build_number == 1000. Returns the N most recent BOTW builds.

    Args:
        version: OnBase version (e.g. '24.1', '25.2', 'DEV')
        top: max number of BOTW builds to return
    """
    bd_version = _resolve_bd_version(version)
    # Use top=50 (pre-warmed on startup, ~12 weeks of builds).
    # BOTWs are weekly, so 50 builds is more than enough. No fallback to top=200
    # because that requires an uncached 30s+ PowerShell call which causes timeouts.
    data = _bd_get_cached(f"onbase/builds/allSlim/{bd_version}", top=50)
    botw_builds = []
    for b in data:
        if b.get("number") == 1000:
            botw_builds.append(_format_bd_build(b))
            if len(botw_builds) >= top:
                break
    if not botw_builds:
        return {
            "version": bd_version,
            "botw_builds": [],
            "message": f"No BOTW builds (build_number=1000) found for {bd_version}.",
        }
    return {
        "version": bd_version,
        "botw_builds": botw_builds,
        "latest_botw": botw_builds[0],
        "total_returned": len(botw_builds),
    }


def bd_get_build_calendar(version: str) -> dict:
    """Get the build schedule/calendar for an OnBase version from BuildDirector.

    Returns upcoming and recent build events including Lockdown, Build of the Week,
    Smoke Test, Publish Build, and No Build of the Week dates.

    Event types:
      0 = Lockdown (red)
      1 = Build of the Week / Release (green)
      2 = No Build of the Week (gray)
      3 = Smoke Test / Publish Build (blue)

    Args:
        version: OnBase version (e.g. '24.1', '25.2', 'DEV', '25.1')
    """
    bd_version = _resolve_bd_version(version)
    version_id = _bd_get_version_id(bd_version)
    events = _bd_get_cached(f"onbase/news/events/{version_id}")

    # Type mapping for readability
    type_names = {0: "Lockdown", 1: "Build of the Week", 2: "No Build of the Week", 3: "Smoke Test / Publish Build"}

    formatted_events = []
    for e in events:
        formatted_events.append({
            "title": e.get("title", ""),
            "start": e.get("start", ""),
            "end": e.get("end", ""),
            "type": e.get("type", -1),
            "type_name": type_names.get(e.get("type", -1), "Unknown"),
            "color": e.get("color", ""),
        })

    # Sort events by start date
    formatted_events.sort(key=lambda x: x["start"])

    # Extract BOTW cycle dates — BOTH the current/most-recent cycle AND the next future cycle.
    # This is critical: if a BOTW was generated April 9 and publishes April 15,
    # and today is April 14, the user needs THAT cycle's dates, not the next one.
    from datetime import date as _date
    today = _date.today().isoformat()

    all_botw = [e for e in formatted_events if e["type"] == 1]
    all_publish = [e for e in formatted_events if e["type"] == 3 and "publish" in e["title"].lower()]
    all_lockdown = [e for e in formatted_events if e["type"] == 0]

    upcoming_botw = [e for e in all_botw if e["start"] >= today]
    upcoming_publish = [e for e in all_publish if e["start"] >= today]
    upcoming_lockdown = [e for e in all_lockdown if e["start"] >= today]

    # Recent past BOTWs (generated before today)
    past_botw = [e for e in all_botw if e["start"] < today]

    schedule_summary = {}

    # Current/most-recent BOTW cycle — the one whose publish date may still be upcoming
    if past_botw:
        latest_past = past_botw[-1]  # most recent past BOTW generation
        cycle = {"botw_generation": latest_past["start"], "botw_title": latest_past["title"]}
        # Find lockdown that preceded this BOTW
        prior_lockdowns = [e for e in all_lockdown if e["start"] <= latest_past["start"]]
        if prior_lockdowns:
            cycle["lockdown_start"] = prior_lockdowns[-1]["start"]
            cycle["lockdown_end"] = prior_lockdowns[-1]["end"]
        # Find publish date after this BOTW generation
        post_publish = [e for e in all_publish if e["start"] > latest_past["start"]]
        if post_publish:
            cycle["publish_date"] = post_publish[0]["start"]
        schedule_summary["current_botw_cycle"] = cycle

    # Next future BOTW cycle
    if upcoming_botw:
        next_botw = upcoming_botw[0]
        next_cycle = {"botw_generation": next_botw["start"], "botw_title": next_botw["title"]}
        prior_lockdowns = [e for e in all_lockdown if e["start"] <= next_botw["start"] and e["start"] >= today]
        if prior_lockdowns:
            next_cycle["lockdown_start"] = prior_lockdowns[-1]["start"]
            next_cycle["lockdown_end"] = prior_lockdowns[-1]["end"]
        post_publish = [e for e in all_publish if e["start"] > next_botw["start"]]
        if post_publish:
            next_cycle["publish_date"] = post_publish[0]["start"]
        schedule_summary["next_botw_cycle"] = next_cycle

    return {
        "version": bd_version,
        "version_id": version_id,
        "total_events": len(formatted_events),
        "schedule_summary": schedule_summary,
        "upcoming_botw_dates": list(dict.fromkeys(e["start"] for e in upcoming_botw))[:5],
        "upcoming_publish_dates": list(dict.fromkeys(e["start"] for e in upcoming_publish))[:5],
        "upcoming_lockdown_dates": list({e["start"]: {"start": e["start"], "end": e["end"]} for e in upcoming_lockdown}.values())[:5],
        "events": formatted_events,
    }


def jira_get_fixed_in_build(jira_key: str) -> dict:
    """Get the 'Fixed in Build' field for a Jira issue.

    Reads customfield_10542 which contains comma-separated build numbers
    (e.g. '26.1.0.441, 26.1.0.434').

    Args:
        jira_key: Jira issue key (e.g. 'SBPWC-12657')
    """
    issue = jira_get(f"issue/{jira_key}?fields=customfield_10542,summary,status")
    fields = issue.get("fields", {})
    raw_value = fields.get("customfield_10542") or ""
    summary = fields.get("summary", "")
    status = fields.get("status", {}).get("name", "")

    # Parse comma-separated build numbers
    builds = [b.strip() for b in raw_value.split(",") if b.strip()] if raw_value else []

    return {
        "jira_key": jira_key,
        "summary": summary,
        "status": status,
        "fixed_in_build_raw": raw_value,
        "fixed_in_builds": builds,
        "build_count": len(builds),
    }


def jira_cards_in_build(build_number: str, max_results: int = 50) -> dict:
    """Find all Jira cards fixed in a specific build number.

    Searches using the 'Fixed In Build' short-text field in Jira.
    Excludes Documentation project cards.

    Args:
        build_number: Full or partial build number (e.g. '26.1.0.439')
        max_results: Maximum number of results to return
    """
    # Escape the build number for JQL text search
    jql = f'"Fixed In Build[Short text]" ~ "\\"{build_number}\\"" AND NOT project = Documentation'
    issues = jira_search(jql, max_results=max_results)
    return {
        "build_number": build_number,
        "jql_used": jql,
        "total_cards": len(issues),
        "cards": issues,
    }


# ------------------------------------------------
# HELPERS - TFS BUILDS / PIPELINES
# ------------------------------------------------

def tfs_build_get(path: str, params: dict = None) -> dict:
    """GET request against the project-scoped TFS Build API."""
    url = f"{TFS_PROJECT_URL}/build/{path}"
    r = requests.get(url, params=params, auth=TFS_AUTH, timeout=60)
    r.raise_for_status()
    return r.json()


def tfs_build_get_text(path: str, params: dict = None) -> str:
    """GET request returning text (e.g., log content)."""
    url = f"{TFS_PROJECT_URL}/build/{path}"
    r = requests.get(
        url, params=params, auth=TFS_AUTH,
        headers={"Accept": "text/plain"},
        timeout=60,
    )
    r.raise_for_status()
    return r.text


def list_pipeline_definitions(
    folder: str = None,
    name_filter: str = None,
    top: int = 100,
) -> list[dict]:
    """List build definitions (pipelines), optionally filtered by folder or name."""
    params = {"api-version": "5.0", "$top": str(top)}
    if folder:
        params["path"] = folder
    if name_filter:
        params["name"] = name_filter

    data = tfs_build_get("definitions", params)
    definitions = []
    for d in data.get("value", []):
        definitions.append({
            "id": d.get("id"),
            "name": d.get("name", ""),
            "path": d.get("path", ""),
            "full_path": f"{d.get('path', '')}\\{d.get('name', '')}",
            "queue_status": d.get("queueStatus", ""),
            "revision": d.get("revision"),
            "url": d.get("_links", {}).get("web", {}).get("href", ""),
        })
    return definitions


def list_builds(
    definition_id: int = None,
    definition_name: str = None,
    status_filter: str = None,
    result_filter: str = None,
    top: int = 20,
    branch_name: str = None,
    requested_for: str = None,
    tag_filters: str = None,
    build_number: str = None,
    reason_filter: str = None,
    query_order: str = "finishTimeDescending",
) -> list[dict]:
    """List builds with various filters."""
    params = {
        "api-version": "5.0",
        "$top": str(top),
        "queryOrder": query_order,
    }
    if definition_id:
        params["definitions"] = str(definition_id)
    if status_filter:
        params["statusFilter"] = status_filter
    if result_filter:
        params["resultFilter"] = result_filter
    if branch_name:
        params["branchName"] = branch_name
    if requested_for:
        params["requestedFor"] = requested_for
    if tag_filters:
        params["tagFilters"] = tag_filters
    if build_number:
        params["buildNumber"] = build_number
    if reason_filter:
        params["reasonFilter"] = reason_filter

    data = tfs_build_get("builds", params)
    builds = []
    for b in data.get("value", []):
        definition = b.get("definition", {})
        # Match by definition name if specified (client-side filter)
        if definition_name and definition_name.lower() not in definition.get("name", "").lower():
            continue
        builds.append(_format_build(b))
    return builds


def _format_build(b: dict) -> dict:
    """Format a raw TFS build object into a clean dict."""
    definition = b.get("definition", {})
    requested_by = b.get("requestedBy", {})
    requested_for = b.get("requestedFor", {})
    source_version = b.get("sourceVersion", "")

    # Extract Jira key from build parameters or source version comment
    build_number = b.get("buildNumber", "")
    params_str = str(b.get("parameters", ""))
    source_branch = b.get("sourceBranch", "")

    return {
        "build_id": b.get("id"),
        "build_number": build_number,
        "status": b.get("status", ""),
        "result": b.get("result", ""),
        "definition_id": definition.get("id"),
        "definition_name": definition.get("name", ""),
        "definition_path": definition.get("path", ""),
        "start_time": b.get("startTime", ""),
        "finish_time": b.get("finishTime", ""),
        "queue_time": b.get("queueTime", ""),
        "requested_by": requested_by.get("displayName", ""),
        "requested_for": requested_for.get("displayName", ""),
        "source_branch": source_branch,
        "source_version": source_version,
        "reason": b.get("reason", ""),
        "url": b.get("_links", {}).get("web", {}).get("href", ""),
    }


def get_build_detail(build_id: int) -> dict:
    """Get detailed information about a single build."""
    data = tfs_build_get(f"builds/{build_id}", {"api-version": "5.0"})
    return _format_build(data)


def get_build_timeline(build_id: int) -> list[dict]:
    """Get build timeline - shows each task/phase and its result."""
    data = tfs_build_get(f"builds/{build_id}/timeline", {"api-version": "5.0"})
    records = []
    for rec in data.get("records", []):
        records.append({
            "id": rec.get("id", ""),
            "parent_id": rec.get("parentId", ""),
            "type": rec.get("type", ""),
            "name": rec.get("name", ""),
            "state": rec.get("state", ""),
            "result": rec.get("result", ""),
            "start_time": rec.get("startTime", ""),
            "finish_time": rec.get("finishTime", ""),
            "log_id": rec.get("log", {}).get("id") if rec.get("log") else None,
            "log_url": rec.get("log", {}).get("url", "") if rec.get("log") else "",
            "error_count": rec.get("errorCount", 0),
            "warning_count": rec.get("warningCount", 0),
            "issues": [
                {
                    "type": iss.get("type", ""),
                    "message": iss.get("message", ""),
                    "category": iss.get("category", ""),
                }
                for iss in rec.get("issues", [])
            ],
        })
    return records


def get_build_log(build_id: int, log_id: int) -> str:
    """Get the text content of a specific build log."""
    return tfs_build_get_text(
        f"builds/{build_id}/logs/{log_id}",
        {"api-version": "5.0"},
    )


def get_build_logs_list(build_id: int) -> list[dict]:
    """List all logs available for a build."""
    data = tfs_build_get(f"builds/{build_id}/logs", {"api-version": "5.0"})
    logs = []
    for log in data.get("value", []):
        logs.append({
            "id": log.get("id"),
            "type": log.get("type", ""),
            "line_count": log.get("lineCount", 0),
            "created_on": log.get("createdOn", ""),
            "url": log.get("url", ""),
        })
    return logs


def get_build_changes(build_id: int) -> list[dict]:
    """Get the changesets/commits associated with a build."""
    data = tfs_build_get(f"builds/{build_id}/changes", {"api-version": "5.0"})
    changes = []
    for ch in data.get("value", []):
        changes.append({
            "id": ch.get("id", ""),
            "message": ch.get("message", ""),
            "author": ch.get("author", {}).get("displayName", ""),
            "timestamp": ch.get("timestamp", ""),
            "location": ch.get("location", ""),
        })
    return changes


def search_builds_by_jira_key(
    jira_key: str,
    definition_id: int = None,
    folder: str = None,
    top: int = 20,
) -> list[dict]:
    """
    Search for builds that reference a Jira key. Checks build changes
    (changeset comments) which is where TFS stores the Jira key from
    the check-in comment.
    If definition_id or folder is specified, narrow the search.
    """
    params = {
        "api-version": "5.0",
        "$top": str(100),
        "queryOrder": "finishTimeDescending",
    }

    # If a specific pipeline folder is given, get all definitions in it
    # then search builds for each
    if folder:
        defs = list_pipeline_definitions(folder=folder)
        def_ids = [str(d["id"]) for d in defs]
        if def_ids:
            params["definitions"] = ",".join(def_ids)
    elif definition_id:
        params["definitions"] = str(definition_id)

    data = tfs_build_get("builds", params)
    matches = []
    jira_upper = jira_key.upper()

    for b in data.get("value", []):
        build_number = (b.get("buildNumber") or "").upper()
        source_version = (b.get("sourceVersion") or "").upper()
        params_str = str(b.get("parameters", "")).upper()

        # Quick check: build number or parameters
        if (
            jira_upper in build_number
            or jira_upper in source_version
            or jira_upper in params_str
        ):
            formatted = _format_build(b)
            formatted["matched_via"] = "build_metadata"
            matches.append(formatted)
            if len(matches) >= top:
                break
            continue

        # Deeper check: fetch build changes (changeset comments)
        try:
            changes = get_build_changes(b["id"])
            for ch in changes:
                if jira_upper in (ch.get("message") or "").upper():
                    formatted = _format_build(b)
                    formatted["matched_via"] = "build_changes"
                    formatted["matching_changeset"] = ch["id"]
                    formatted["changeset_message"] = ch["message"]
                    matches.append(formatted)
                    break
        except Exception:
            pass

        if len(matches) >= top:
            break

    return matches


def get_failed_tasks(build_id: int) -> list[dict]:
    """Get only the failed tasks from a build timeline, with their log IDs for further investigation."""
    timeline = get_build_timeline(build_id)
    failed = []
    for rec in timeline:
        if rec.get("result") in ("failed", "canceled") or rec.get("error_count", 0) > 0:
            failed.append(rec)
    return failed


def get_build_failure_summary(build_id: int, include_logs: bool = False, max_log_lines: int = 200) -> dict:
    """
    Get a comprehensive failure summary for a build.
    Includes build details, failed tasks, and optionally the last N lines
    of each failed task's log.
    """
    build = get_build_detail(build_id)
    failed_tasks = get_failed_tasks(build_id)

    failure_details = []
    for task in failed_tasks:
        detail = {
            "task_name": task["name"],
            "task_type": task["type"],
            "result": task["result"],
            "error_count": task["error_count"],
            "warning_count": task["warning_count"],
            "issues": task["issues"],
            "log_id": task["log_id"],
        }
        if include_logs and task["log_id"]:
            try:
                log_text = get_build_log(build_id, task["log_id"])
                lines = log_text.splitlines()
                if len(lines) > max_log_lines:
                    detail["log_tail"] = "\n".join(lines[-max_log_lines:])
                    detail["log_truncated"] = True
                    detail["total_log_lines"] = len(lines)
                else:
                    detail["log_tail"] = log_text
                    detail["log_truncated"] = False
            except Exception as e:
                detail["log_error"] = str(e)
        failure_details.append(detail)

    return {
        "build": build,
        "total_failed_tasks": len(failed_tasks),
        "failed_tasks": failure_details,
    }


# ------------------------------------------------
# API ENDPOINTS - PIPELINES / BUILDS
# ------------------------------------------------

@app.get(
    "/pipelines",
    summary="List build pipeline definitions",
    description=(
        "Lists build pipeline definitions (build definitions) in TFS. "
        "Optionally filter by folder path (e.g. \\WorkView\\Tests\\DEV) "
        "or by a well-known alias (official_builds, workview_tests, "
        "dev_official_builds). For workview_tests, use the 'version' "
        "parameter (e.g. DEV, 25.1, 24.1) to specify the subfolder. "
        "Use name_filter for name matching."
    ),
)
def api_list_pipelines(
    folder: str = None,
    folder_alias: str = None,
    version: str = None,
    name_filter: str = None,
    top: int = 100,
    _key=Depends(verify_api_key),
):
    try:
        effective_folder = folder
        if folder_alias:
            effective_folder = PIPELINE_FOLDERS.get(
                folder_alias.lower(),
            )
            if not effective_folder:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Unknown folder alias '{folder_alias}'. "
                        f"Valid aliases: {', '.join(PIPELINE_FOLDERS.keys())}"
                    ),
                )

        # Append version subfolder for workview_tests
        if version and effective_folder:
            effective_folder = f"{effective_folder}\\{version.upper()}" if version.upper() == "DEV" else f"{effective_folder}\\{version}"

        defs = list_pipeline_definitions(
            folder=effective_folder,
            name_filter=name_filter,
            top=top,
        )
        return {
            "folder": effective_folder,
            "definitions": defs,
            "total": len(defs),
        }
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"TFS API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach TFS server.")


@app.get(
    "/pipelines/{definition_id}/builds",
    summary="List builds for a specific pipeline",
    description=(
        "Returns recent builds for a given pipeline definition ID. "
        "Filter by status (inProgress, completed, cancelling, postponed, "
        "notStarted, all) or result (succeeded, partiallySucceeded, "
        "failed, canceled)."
    ),
)
def api_pipeline_builds(
    definition_id: int,
    status_filter: str = None,
    result_filter: str = None,
    top: int = 20,
    _key=Depends(verify_api_key),
):
    try:
        builds = list_builds(
            definition_id=definition_id,
            status_filter=status_filter,
            result_filter=result_filter,
            top=top,
        )
        return {
            "definition_id": definition_id,
            "builds": builds,
            "total": len(builds),
        }
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"TFS API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach TFS server.")


@app.get(
    "/builds/search/by-jira-key",
    summary="Search builds by Jira key",
    description=(
        "Searches recent builds for references to a Jira key "
        "(e.g. SBPWC-12624) in build changes (changeset comments). "
        "Optionally narrow by pipeline definition or folder."
    ),
)
def api_search_builds_by_jira(
    jira_key: str,
    definition_id: int = None,
    folder: str = None,
    folder_alias: str = None,
    top: int = 20,
    _key=Depends(verify_api_key),
):
    try:
        effective_folder = folder
        if folder_alias:
            effective_folder = PIPELINE_FOLDERS.get(folder_alias.lower())
            if not effective_folder:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Unknown folder alias '{folder_alias}'. "
                        f"Valid aliases: {', '.join(PIPELINE_FOLDERS.keys())}"
                    ),
                )

        builds = search_builds_by_jira_key(
            jira_key,
            definition_id=definition_id,
            folder=effective_folder,
            top=top,
        )
        return {
            "jira_key": jira_key,
            "folder": effective_folder,
            "definition_id": definition_id,
            "builds": builds,
            "total": len(builds),
        }
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"TFS API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach TFS server.")


@app.get(
    "/builds/{build_id}",
    summary="Get build details",
    description="Returns detailed information about a specific build by ID.",
)
def api_build_detail(build_id: int, _key=Depends(verify_api_key)):
    try:
        return get_build_detail(build_id)
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"TFS API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach TFS server.")


@app.get(
    "/builds/{build_id}/changes",
    summary="Get changesets associated with a build",
    description=(
        "Returns the list of changesets (with comments/messages) that "
        "were included in this build. This is how to find which Jira key "
        "triggered a specific build."
    ),
)
def api_build_changes(build_id: int, _key=Depends(verify_api_key)):
    try:
        changes = get_build_changes(build_id)
        return {
            "build_id": build_id,
            "changes": changes,
            "total": len(changes),
        }
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"TFS API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach TFS server.")


@app.get(
    "/builds/{build_id}/timeline",
    summary="Get build timeline",
    description=(
        "Returns the timeline of a build showing all phases, jobs, "
        "and tasks with their results. Use this to understand which "
        "step in a build failed and why."
    ),
)
def api_build_timeline(build_id: int, _key=Depends(verify_api_key)):
    try:
        timeline = get_build_timeline(build_id)
        return {
            "build_id": build_id,
            "records": timeline,
            "total_records": len(timeline),
        }
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"TFS API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach TFS server.")


@app.get(
    "/builds/{build_id}/failures",
    summary="Get build failure summary",
    description=(
        "Returns a focused summary of why a build failed: the build "
        "details plus all failed tasks with their error messages. "
        "Set include_logs=true to also fetch the tail of each failed "
        "task's log output."
    ),
)
def api_build_failures(
    build_id: int,
    include_logs: bool = False,
    max_log_lines: int = 200,
    _key=Depends(verify_api_key),
):
    try:
        return get_build_failure_summary(build_id, include_logs, max_log_lines)
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"TFS API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach TFS server.")


@app.get(
    "/builds/{build_id}/logs",
    summary="List all logs for a build",
    description="Returns a list of all log files available for a build.",
)
def api_build_logs_list(build_id: int, _key=Depends(verify_api_key)):
    try:
        logs = get_build_logs_list(build_id)
        return {
            "build_id": build_id,
            "logs": logs,
            "total": len(logs),
        }
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"TFS API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach TFS server.")


@app.get(
    "/builds/{build_id}/logs/{log_id}",
    summary="Get build log content",
    description=(
        "Returns the text content of a specific log from a build. "
        "Use the timeline or logs list endpoint to find the log_id."
    ),
)
def api_build_log_content(
    build_id: int,
    log_id: int,
    tail: int = 0,
    _key=Depends(verify_api_key),
):
    try:
        log_text = get_build_log(build_id, log_id)
        if tail > 0:
            lines = log_text.splitlines()
            if len(lines) > tail:
                log_text = "\n".join(lines[-tail:])
                return {
                    "build_id": build_id,
                    "log_id": log_id,
                    "content": log_text,
                    "truncated": True,
                    "total_lines": len(lines),
                    "lines_shown": tail,
                }
        return {
            "build_id": build_id,
            "log_id": log_id,
            "content": log_text,
            "truncated": False,
        }
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"TFS API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach TFS server.")


@app.get(
    "/pipeline-folders",
    summary="List well-known pipeline folder aliases",
    description=(
        "Returns the mapping of well-known folder aliases to TFS "
        "pipeline folder paths. Use these aliases in other pipeline "
        "endpoints."
    ),
)
def api_pipeline_folders(_key=Depends(verify_api_key)):
    return {"folders": PIPELINE_FOLDERS}


# ------------------------------------------------
# API ENDPOINTS — GITHUB
# ------------------------------------------------

# NOTE: Static paths MUST be registered before {owner}/{repo} wildcard routes.

@app.get(
    "/github/org/repos",
    summary="List repos in the GitHub organization",
    description=(
        "Lists repositories in the configured GitHub org (default: HylandFoundation). "
        "Filter by type (all, public, private, forks, sources, member). "
        "Sort by created, updated, pushed, full_name."
    ),
)
def api_github_org_repos(
    org: str = None,
    sort: str = "updated",
    repo_type: str = "all",
    per_page: int = 30,
    _key=Depends(verify_api_key),
):
    try:
        repos = list_github_org_repos(org, per_page, sort, repo_type)
        return {
            "org": org or GITHUB_ORG,
            "repos": repos,
            "total": len(repos),
        }
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"GitHub API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub API.")


@app.get(
    "/github/org/repos/search",
    summary="Search repos in the GitHub organization",
    description=(
        "Search repositories by keyword within the GitHub org (default: HylandFoundation)."
    ),
)
def api_github_org_repo_search(
    query: str,
    org: str = None,
    per_page: int = 20,
    _key=Depends(verify_api_key),
):
    try:
        results = search_github_repos(query, org, per_page)
        return {
            "org": org or GITHUB_ORG,
            "query": query,
            "results": results,
            "total": len(results),
        }
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"GitHub API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub API.")


@app.get(
    "/github/repos",
    summary="List well-known repo aliases",
    description=(
        "Returns the mapping of short repo aliases to full repository names "
        "within the HylandFoundation org. Use these aliases with the "
        "natural-language endpoint."
    ),
)
def api_github_repo_aliases(_key=Depends(verify_api_key)):
    return {
        "org": GITHUB_ORG,
        "aliases": GITHUB_REPO_ALIASES,
    }


@app.get(
    "/github/{owner}/{repo}",
    summary="Get GitHub repository info",
    description=(
        "Returns metadata for a GitHub repository: description, "
        "default branch, language, topics, star/fork counts, etc."
    ),
)
def api_github_repo(owner: str, repo: str, _key=Depends(verify_api_key)):
    try:
        return fetch_github_repo(owner, repo)
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"GitHub API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub API.")


@app.get(
    "/github/{owner}/{repo}/pulls",
    summary="List pull requests for a GitHub repo",
    description=(
        "Returns pull requests for a GitHub repository. "
        "Filter by state (open, closed, all), sort by created/updated/popularity, "
        "and control direction (asc, desc)."
    ),
)
def api_github_pulls(
    owner: str,
    repo: str,
    state: str = "open",
    sort: str = "updated",
    direction: str = "desc",
    per_page: int = 30,
    _key=Depends(verify_api_key),
):
    try:
        pulls = list_github_pulls(owner, repo, state, sort, direction, per_page)
        return {
            "owner": owner,
            "repo": repo,
            "state": state,
            "pulls": pulls,
            "total": len(pulls),
        }
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"GitHub API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub API.")


@app.get(
    "/github/{owner}/{repo}/pulls/{pr_number}",
    summary="Get GitHub PR details with diffs",
    description=(
        "Returns full pull request details including metadata, body, "
        "merge status, and unified diffs for all changed files."
    ),
)
def api_github_pr(
    owner: str,
    repo: str,
    pr_number: int,
    _key=Depends(verify_api_key),
):
    try:
        return fetch_github_pr_with_diffs(owner, repo, pr_number)
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"GitHub API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub API.")


@app.get(
    "/github/{owner}/{repo}/pulls/{pr_number}/files",
    summary="Get files changed in a GitHub PR",
    description=(
        "Returns the list of files changed in a pull request with "
        "per-file patch diffs, additions, and deletions."
    ),
)
def api_github_pr_files(
    owner: str,
    repo: str,
    pr_number: int,
    _key=Depends(verify_api_key),
):
    try:
        files = fetch_github_pr_files(owner, repo, pr_number)
        return {
            "owner": owner,
            "repo": repo,
            "pr_number": pr_number,
            "files": files,
            "total_files": len(files),
        }
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"GitHub API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub API.")


@app.get(
    "/github/{owner}/{repo}/pulls/{pr_number}/reviews",
    summary="Get reviews for a GitHub PR",
    description=(
        "Returns all reviews submitted on a pull request, including "
        "reviewer, state (approved/changes_requested/commented), and body."
    ),
)
def api_github_pr_reviews(
    owner: str,
    repo: str,
    pr_number: int,
    _key=Depends(verify_api_key),
):
    try:
        reviews = fetch_github_pr_reviews(owner, repo, pr_number)
        return {
            "owner": owner,
            "repo": repo,
            "pr_number": pr_number,
            "reviews": reviews,
            "total": len(reviews),
        }
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"GitHub API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub API.")


@app.get(
    "/github/{owner}/{repo}/contents/{path:path}",
    summary="Get file content from a GitHub repo",
    description=(
        "Fetches the decoded content of a file from a GitHub repository. "
        "Optionally specify a ref (branch, tag, or commit SHA)."
    ),
)
def api_github_file_content(
    owner: str,
    repo: str,
    path: str,
    ref: str = None,
    _key=Depends(verify_api_key),
):
    try:
        return fetch_github_file_content(owner, repo, path, ref)
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"GitHub API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub API.")


@app.get(
    "/github/{owner}/{repo}/pulls/search",
    summary="Search pull requests in a GitHub repo",
    description=(
        "Searches pull requests by keyword using the GitHub search API. "
        "Useful for finding PRs by title, body text, or label."
    ),
)
def api_github_pr_search(
    owner: str,
    repo: str,
    query: str,
    per_page: int = 20,
    _key=Depends(verify_api_key),
):
    try:
        results = search_github_prs(owner, repo, query, per_page)
        return {
            "owner": owner,
            "repo": repo,
            "query": query,
            "results": results,
            "total": len(results),
        }
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"GitHub API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub API.")


@app.get(
    "/github/{owner}/{repo}/pulls/{pr_number}/comments",
    summary="Get inline review comments on a GitHub PR",
    description=(
        "Returns all code-level review comments on a pull request. "
        "These are comments attached to specific lines in the diff."
    ),
)
def api_github_pr_comments(
    owner: str,
    repo: str,
    pr_number: int,
    _key=Depends(verify_api_key),
):
    try:
        comments = fetch_github_pr_review_comments(owner, repo, pr_number)
        return {
            "owner": owner,
            "repo": repo,
            "pr_number": pr_number,
            "comments": comments,
            "total": len(comments),
        }
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"GitHub API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub API.")


@app.get(
    "/github/{owner}/{repo}/code-scanning/alerts",
    summary="List code scanning alerts for a GitHub repo",
    description=(
        "Returns code scanning (CodeQL / third-party) alerts for a repository. "
        "Filter by state (open, closed, dismissed, fixed) and severity "
        "(critical, high, medium, low, warning, note, error)."
    ),
)
def api_code_scanning_alerts(
    owner: str,
    repo: str,
    state: str = "open",
    severity: str = None,
    per_page: int = 30,
    _key=Depends(verify_api_key),
):
    try:
        alerts = list_code_scanning_alerts(owner, repo, state, severity, per_page)
        return {
            "owner": owner,
            "repo": repo,
            "state": state,
            "alerts": alerts,
            "total": len(alerts),
        }
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"GitHub API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub API.")


@app.get(
    "/github/{owner}/{repo}/code-scanning/alerts/{alert_number}",
    summary="Get a single code scanning alert",
    description=(
        "Returns full details for a specific code scanning alert, "
        "including the rule, tool, location, and dismissal info."
    ),
)
def api_code_scanning_alert_detail(
    owner: str,
    repo: str,
    alert_number: int,
    _key=Depends(verify_api_key),
):
    try:
        return get_code_scanning_alert(owner, repo, alert_number)
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"GitHub API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub API.")


@app.get(
    "/github/{owner}/{repo}/dependabot/alerts",
    summary="List Dependabot alerts for a GitHub repo",
    description=(
        "Returns Dependabot vulnerability alerts for a repository. "
        "Filter by state (auto_dismissed, dismissed, fixed, open) "
        "and severity (critical, high, medium, low)."
    ),
)
def api_dependabot_alerts(
    owner: str,
    repo: str,
    state: str = "open",
    severity: str = None,
    per_page: int = 30,
    _key=Depends(verify_api_key),
):
    try:
        alerts = list_dependabot_alerts(owner, repo, state, severity, per_page)
        return {
            "owner": owner,
            "repo": repo,
            "state": state,
            "alerts": alerts,
            "total": len(alerts),
        }
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"GitHub API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub API.")


@app.get(
    "/github/{owner}/{repo}/dependabot/alerts/{alert_number}",
    summary="Get a single Dependabot alert",
    description=(
        "Returns full details for a specific Dependabot alert, "
        "including the advisory, CVSS score, CWEs, and patching info."
    ),
)
def api_dependabot_alert_detail(
    owner: str,
    repo: str,
    alert_number: int,
    _key=Depends(verify_api_key),
):
    try:
        return get_dependabot_alert(owner, repo, alert_number)
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"GitHub API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub API.")


# ------------------------------------------------
# GITHUB — WRITE ROUTES
# ------------------------------------------------

@app.post(
    "/github/{owner}/{repo}/branches",
    summary="Create a branch in a GitHub repository",
    description=(
        "Creates a new branch from a source branch (defaults to the repo's "
        "default branch if not specified)."
    ),
)
def api_github_create_branch(
    owner: str,
    repo: str,
    branch: str,
    from_branch: str = "",
    _key=Depends(verify_api_key),
):
    try:
        return create_github_branch(owner, repo, branch, from_branch)
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"GitHub API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub API.")


@app.put(
    "/github/{owner}/{repo}/contents/{path:path}",
    summary="Create or update a file in a GitHub repository",
    description=(
        "Creates a new file or updates an existing file in the specified branch. "
        "For updates, provide the current file SHA."
    ),
)
def api_github_create_or_update_file(
    owner: str,
    repo: str,
    path: str,
    content: str,
    message: str,
    branch: str,
    sha: str = "",
    _key=Depends(verify_api_key),
):
    try:
        return create_or_update_github_file(owner, repo, path, content, message, branch, sha)
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"GitHub API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub API.")


class _FileItem(BaseModel):
    path: str = Field(..., description="File path within the repo")
    content: str = Field(..., description="File content (plain text)")


class _PushFilesBody(BaseModel):
    files: List[_FileItem] = Field(..., description="List of files to push")
    message: str = Field(..., description="Commit message")


@app.post(
    "/github/{owner}/{repo}/push",
    summary="Push multiple files in a single commit",
    description=(
        "Pushes multiple files to a branch in a single commit using the "
        "Git trees API. Useful for atomic multi-file changes."
    ),
)
def api_github_push_files(
    owner: str,
    repo: str,
    branch: str,
    body: _PushFilesBody,
    _key=Depends(verify_api_key),
):
    try:
        files_list = [{"path": f.path, "content": f.content} for f in body.files]
        return push_github_files(owner, repo, branch, files_list, body.message)
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"GitHub API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub API.")


@app.post(
    "/github/{owner}/{repo}/pulls",
    summary="Create a pull request",
    description=(
        "Creates a pull request from a head branch to a base branch "
        "(defaults to the repo's default branch)."
    ),
)
def api_github_create_pull_request(
    owner: str,
    repo: str,
    title: str,
    head: str,
    base: str = "",
    body: str = "",
    draft: bool = False,
    _key=Depends(verify_api_key),
):
    try:
        return create_github_pull_request(owner, repo, title, head, base, body, draft)
    except HTTPException:
        raise
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=status, detail=f"GitHub API error: {e}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub API.")


# ------------------------------------------------
# PROGET PACKAGE SEARCH
# ------------------------------------------------

PROGET_BASE_URL = "https://proget.onbase.net"

# NuGet feeds to search on ProGet (NuGet v3 search API)
PROGET_NUGET_FEEDS = ["NuGet", "TestFeed"]


def proget_search_packages(
    package_name: str,
    feed: str = "",
    top: int = 10,
) -> dict:
    """Search ProGet for NuGet packages by name using the NuGet v3 search API.

    Queries the NuGet v3 search endpoint on each feed (NuGet, TestFeed) and
    aggregates results. Returns packages with version info sorted by version.

    Args:
        package_name: Package name or partial name to search (e.g. "cefsharp", "Infragistics.WPF")
        feed: Optional feed filter ("NuGet", "TestFeed", or "" for all feeds)
        top: Max package IDs to return per feed
    Returns:
        dict with packages list, latest_version, and metadata.
    """
    browse_url = (
        f"{PROGET_BASE_URL}/packages"
        f"?Cache=all&PackageSearch={quote(package_name)}"
        f"&orderBy=Date&descending=true"
    )
    _logger.info(f"ProGet search: {package_name} (feed={feed or 'all'})")

    feeds_to_search = [feed] if feed else PROGET_NUGET_FEEDS
    packages = []
    errors = []

    for feed_name in feeds_to_search:
        api_url = (
            f"{PROGET_BASE_URL}/nuget/{quote(feed_name)}/v3/search"
            f"?q={quote(package_name)}&take={top}"
        )
        try:
            resp = requests.get(api_url, timeout=12, verify=False)
            resp.raise_for_status()
            data = resp.json()

            for pkg in data.get("data", []):
                pkg_id = pkg.get("id", "")
                latest_ver = pkg.get("version", "")
                description = pkg.get("description", "")
                total_downloads = sum(
                    v.get("downloads", 0) for v in pkg.get("versions", [])
                )
                # Get version list (newest first based on version number)
                versions = pkg.get("versions", [])

                packages.append({
                    "feed": feed_name,
                    "name": pkg_id,
                    "version": latest_ver,
                    "description": description[:120] if description else "",
                    "downloads": total_downloads,
                    "all_versions": [
                        {"version": v.get("version", ""), "downloads": v.get("downloads", 0)}
                        for v in versions[:5]  # top 5 versions per package
                    ],
                })
        except requests.exceptions.RequestException as e:
            errors.append(f"{feed_name}: {e}")
            _logger.warning(f"ProGet search failed for feed {feed_name}: {e}")
        except (ValueError, KeyError) as e:
            errors.append(f"{feed_name}: parse error: {e}")

    # Deduplicate by package name (prefer NuGet feed over TestFeed)
    seen = {}
    for pkg in packages:
        key = pkg["name"].lower()
        if key not in seen or pkg["feed"] == "NuGet":
            seen[key] = pkg
    deduped = list(seen.values())

    # Sort by version (highest first)
    def _version_tuple(v: str):
        try:
            return tuple(int(x) for x in v.split("."))
        except (ValueError, AttributeError):
            return (0,)

    deduped.sort(key=lambda p: _version_tuple(p["version"]), reverse=True)

    # Determine highest version across all packages
    latest_version = ""
    latest_name = package_name
    if deduped:
        latest_version = deduped[0]["version"]
        latest_name = deduped[0]["name"]

    result = {
        "search_term": package_name,
        "total_found": len(deduped),
        "latest_version": latest_version,
        "latest_package": latest_name,
        "packages": deduped[:top],
        "proget_url": browse_url,
    }
    if errors:
        result["warnings"] = errors

    return result


# ------------------------------------------------
# ENTRY POINT
# ------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("API_HOST", "127.0.0.1")  # Set to 0.0.0.0 on VM
    port = int(os.getenv("API_PORT", "9000"))
    uvicorn.run("TFSMCP:app", host=host, port=port, reload=True)
