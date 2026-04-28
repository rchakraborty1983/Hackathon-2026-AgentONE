"""
AgentONE — FastAPI server that powers the Teams bot via Copilot Studio HTTP plugin actions.

Endpoints:
    POST /chat      — Main conversational endpoint (routes to appropriate agent)
    POST /analyze   — Direct Jira analysis fast-path (no LLM routing)
    GET  /status    — Health check (TFS/Jira/GitHub connectivity)
    GET  /          — Root info
"""

import json
import logging
import re
import sys
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Ensure C:\TFS_MCP is on the path for imports
sys.path.insert(0, "C:\\TFS_MCP")
# Ensure our own package is importable
sys.path.insert(0, "C:\\TFS_MCP\\teams_bot")

from config import API_HOST, API_PORT, API_KEY, PUBLIC_BASE_URL
from agent_router import route, get_agent_module
from agent_executor import execute
from tool_registry import TOOL_DEFINITIONS
from ux_enhancements import (
    is_welcome_message, is_skill_menu_request,
    WELCOME_MESSAGE, SKILL_MENU,
    enrich_response, track_conversation_start, is_new_conversation,
    get_suggested_actions, STANDARD_ACTIONS,
    check_bare_command, check_pending_action,
)

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("agentone")

# ── Conversation memory (in-memory, keyed by conversation_id) ──
_conversations: dict[str, list[dict]] = {}
MAX_HISTORY = 20


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"AgentONE starting on {API_HOST}:{API_PORT}")
    # Do NOT log any portion of API_KEY -- even a prefix is sensitive data.
    logger.info(f"API Key configured: {bool(API_KEY)}")
    logger.info(f"Tools registered: {len(TOOL_DEFINITIONS)}")
    # Warm BuildDirector cache in AgentONE's process (background, non-blocking).
    # AgentONE imports TFSMCP functions directly, so it has its own _BD_CACHE.
    # Re-warm every 20 min (before the 30-min TTL expires) so cache never goes cold.
    import threading
    from TFSMCP import _warm_bd_cache
    threading.Thread(
        target=_warm_bd_cache,
        kwargs={"repeat_interval": 1200},  # 20 minutes
        daemon=True,
        name="bd-cache-warm",
    ).start()
    yield
    logger.info("AgentONE shutting down")


app = FastAPI(
    title="AgentONE",
    description="OnBase DevOps Agent API — powers the Teams bot via Copilot Studio",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,     # Disable /docs (Swagger UI)
    redoc_url=None,    # Disable /redoc
    openapi_url=None,  # Disable /openapi.json
)

# Thread pool for timeout-wrapped execution
_executor = ThreadPoolExecutor(max_workers=4)

# Server-side response timeout (seconds).
# Copilot Studio HTTP connector allows up to ~180s (despite unclear error messages).
# We set 120s to leave buffer. Long tasks that exceed this get async continuation.
RESPONSE_TIMEOUT = 120

# ── Async task continuation ──
# When a request times out, the agent keeps running in background.
# Store the future so the user can retrieve the result later.
import threading

_background_tasks: dict[str, dict] = {}  # conversation_id → {future, agent, message, started_at}
_BACKGROUND_TASK_TTL = 600  # 10 min — discard stale tasks

_TASK_STATUS_PATTERNS = re.compile(
    r'^(?:check|get|show|what.s)\s*(?:the\s+)?(?:last\s+)?(?:task|job|query|result)\s*(?:status)?$',
    re.IGNORECASE,
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every incoming request with headers and timing."""
    start = time.time()
    method = request.method
    path = request.url.path
    client = request.client.host if request.client else "unknown"
    # Do NOT log any portion of the inbound API key -- only whether one was supplied.
    api_key_supplied = bool(request.headers.get("x-api-key", ""))
    logger.info(f"→ {method} {path} from {client} | API-Key supplied: {api_key_supplied}")

    response = await call_next(request)

    elapsed = time.time() - start
    logger.info(f"← {method} {path} → {response.status_code} in {elapsed:.1f}s")
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch ALL unhandled exceptions so the server never crashes on a request."""
    logger.error(f"Unhandled exception on {request.method} {request.url.path}: {exc}")
    logger.error(traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={
            "response": f"Internal server error: {type(exc).__name__}: {str(exc)[:200]}",
            "agent_used": "error",
            "conversation_id": "",
            "tool_calls_made": 0,
        },
    )


# ── Auth ──
def _verify_api_key(x_api_key: str | None):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")


# ── Request/Response models ──
class ChatRequest(BaseModel):
    message: str
    conversation_id: str = ""


class ChatResponse(BaseModel):
    response: str
    agent_used: str
    conversation_id: str
    tool_calls_made: int = 0
    suggested_actions: list[dict] = []


class AnalyzeRequest(BaseModel):
    jira_key: str


# ── Jira key extraction pattern ──
_JIRA_KEY_RE = re.compile(r'\b([A-Z][A-Z0-9]+-\d+)\b')

# Patterns that indicate a code review START request
_CODE_REVIEW_START_PATTERNS = re.compile(
    r'code\s*review|review\s*(the\s+)?code|review\s+shelveset|'
    r'shelveset\s+review|html\s+report.*review|review.*html\s+report|'
    r'create.*review|do\s+a\s+review|perform.*review',
    re.IGNORECASE,
)

# Patterns that indicate a code review STATUS check
_CODE_REVIEW_STATUS_PATTERNS = re.compile(
    r'review\s+status|status.*review|is\s+the\s+review\s+done|'
    r'check.*review|review.*ready|review.*progress|review.*finished',
    re.IGNORECASE,
)

# ── GHAS Fix + PR fast-path patterns ──
# Detect "fix alert #747 and create PR" style requests
_GHAS_FIX_PR_START_PATTERNS = re.compile(
    r'fix\s+.*alert\s*#?\s*(\d+).*\b(pr|pull\s*request)\b|'
    r'\b(pr|pull\s*request)\b.*fix\s+.*alert\s*#?\s*(\d+)|'
    r'fix\s+.*ghas\s*.*alert\s*#?\s*(\d+)|'
    r'fix\s+.*(?:code\s*scanning|security)\s+alert\s*#?\s*(\d+).*\b(pr|pull\s*request)\b',
    re.IGNORECASE,
)
_GHAS_ALERT_NUMBER_RE = re.compile(r'alert\s*#?\s*(\d+)', re.IGNORECASE)
_GHAS_REPO_RE = re.compile(r'\b(?:in|on|for|repo)\s+(\S+)', re.IGNORECASE)

# Detect GHAS fix PR status checks
_GHAS_FIX_PR_STATUS_PATTERNS = re.compile(
    r'(?:pr|pull\s*request|fix)\s+status.*alert\s*#?\s*(\d+)|'
    r'status.*(?:pr|fix|pull\s*request).*alert\s*#?\s*(\d+)|'
    r'is\s+the\s+(?:pr|fix)\s+(?:done|ready).*alert\s*#?\s*(\d+)|'
    r'alert\s*#?\s*(\d+).*(?:pr|fix)\s+(?:status|done|ready|progress)',
    re.IGNORECASE,
)

# ── Build failure analysis fast-path patterns ──
_BUILD_FAILURE_START_PATTERNS = re.compile(
    r'(?:analyze|investigate|diagnose|check)\s+(?:build\s+)?failure.*\b(\d+)\b|'
    r'build\s+(\d+)\s+(?:fail|failure|broke|broken|red)|'
    r'why\s+did\s+build\s+(\d+)\s+fail|'
    r'build\s+failure.*\b(\d+)\b|'
    r'failure\s+(?:analysis|report).*build\s*(?:#?\s*)?(\d+)',
    re.IGNORECASE,
)

_BUILD_FAILURE_STATUS_PATTERNS = re.compile(
    r'(?:build\s+)?failure\s+(?:analysis\s+)?status.*\b(\d+)\b|'
    r'status.*(?:build\s+)?failure.*\b(\d+)\b|'
    r'is\s+the\s+(?:build\s+)?failure\s+(?:analysis\s+)?(?:done|ready).*\b(\d+)\b|'
    r'build\s+(\d+)\s+failure\s+(?:status|done|ready|progress)',
    re.IGNORECASE,
)

# ── Build/BOTW fast-path patterns ──
# Version extraction: "25.2", "24.1", "DEV", "26.1" etc.
_VERSION_RE = re.compile(r'\b(\d{2}\.\d)\b|\b(DEV)\b', re.IGNORECASE)

# Full 4-part build version in conversation history (e.g. "26.1.0.447")
_FULL_BUILD_VERSION_RE = re.compile(r'\b(\d{2}\.\d+\.\d+\.\d+)\b')

_BOTW_PATTERNS = re.compile(
    r'botw|build\s+of\s+the\s+week|next\s+build|build\s+date|'
    r'build\s+schedule|build\s+calendar|lockdown\s+date|'
    r'when\s+is\s+.*(build|lockdown|botw)|next\s+lockdown|'
    r'upcoming\s+build|build\s+cycle',
    re.IGNORECASE,
)

# ── "What changed since previous build" fast-path ──
_WHAT_CHANGED_PATTERNS = re.compile(
    r'what\s+changed\s+since\s+(?:the\s+)?previous\s+build|'
    r'what\s+changed\s+(?:in|from|between)\s+(?:the\s+)?(?:last|previous|latest)\s+build|'
    r'what.*cards.*(?:in|for)\s+(?:this|the\s+latest|the\s+last)\s+build|'
    r'what\s+(?:is|are)\s+(?:in|included\s+in)\s+(?:this|the\s+latest)\s+build|'
    r'changes?\s+(?:in|from)\s+(?:the\s+)?(?:last|latest|previous)\s+build',
    re.IGNORECASE,
)


def _extract_build_version_from_history(conversation_id: str) -> str | None:
    """Extract the most recent full build version (X.Y.Z.W) from conversation history."""
    history = _conversations.get(conversation_id, [])
    # Scan backwards — most recent messages first
    for msg in reversed(history):
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        if not content:
            continue
        matches = _FULL_BUILD_VERSION_RE.findall(content)
        if matches:
            return matches[-1]  # last occurrence in the message
    return None


def _try_what_changed_fast_path(message: str, conversation_id: str) -> str | None:
    """Detect 'what changed since previous build' and use JQL to get correct cards."""
    if not _WHAT_CHANGED_PATTERNS.search(message):
        return None

    # Try to get build version from the message itself first
    ver_match = _FULL_BUILD_VERSION_RE.search(message)
    current_build = ver_match.group(1) if ver_match else None

    # Fall back to conversation history
    if not current_build:
        current_build = _extract_build_version_from_history(conversation_id)

    if not current_build:
        logger.info("Fast-path: 'what changed' detected but no build version in context")
        return None

    logger.info(f"Fast-path: 'what changed' for build {current_build}")

    from TFSMCP import jira_cards_in_build, bd_list_builds

    try:
        # Get the cards in the current build via JQL
        cards_result = jira_cards_in_build(current_build)
        cards = cards_result.get("cards", [])

        # Determine the previous build version for context
        parts = current_build.split(".")
        major_minor = f"{parts[0]}.{parts[1]}"
        prev_build = None
        try:
            builds_data = bd_list_builds(major_minor, top=5, successful_only=True)
            builds_list = builds_data.get("builds", [])
            for i, b in enumerate(builds_list):
                if b.get("version") == current_build and i + 1 < len(builds_list):
                    prev_build = builds_list[i + 1].get("version")
                    break
        except Exception as e:
            logger.warning(f"Could not determine previous build: {e}")

        return _format_what_changed_response(current_build, prev_build, cards, cards_result.get("jql_used", ""))
    except Exception as e:
        logger.error(f"Fast-path 'what changed' failed: {e}")
        return f"Error fetching cards for build {current_build}: {e}"


def _format_what_changed_response(current_build: str, prev_build: str | None,
                                   cards: list, jql_used: str) -> str:
    """Format the 'what changed' response with Jira cards in the build."""
    if prev_build:
        parts = [f"The changes included in OnBase build **{current_build}** compared to "
                 f"the previous build **{prev_build}** are associated with these Jira cards:\n"]
    else:
        parts = [f"The Jira cards fixed in OnBase build **{current_build}**:\n"]

    if not cards:
        parts.append("No Jira cards found with this build number in the 'Fixed In Build' field.")
        parts.append(f"\n*JQL used: `{jql_used}`*")
        return "\n".join(parts)

    for i, card in enumerate(cards, 1):
        key = card.get("key", "")
        summary = card.get("summary", card.get("fields", {}).get("summary", "N/A"))
        status = card.get("status", card.get("fields", {}).get("status", {}).get("name", ""))
        jira_url = f"https://hyland.atlassian.net/browse/{key}"
        parts.append(f"{i}. [{key}]({jira_url}): {summary}")

    parts.append(f"\nIf you want, I can provide more detailed information or diffs for these specific changes.")

    return "\n".join(parts)


def _try_build_fast_path(message: str) -> str | None:
    """Detect BOTW/build calendar queries and call BuildDirector directly."""
    if not _BOTW_PATTERNS.search(message):
        return None

    # Extract version
    ver_match = _VERSION_RE.search(message)
    if not ver_match:
        return None

    version = ver_match.group(1) or ver_match.group(2)
    logger.info(f"Fast-path: BUILD/BOTW query for version {version}")

    from TFSMCP import bd_get_build_calendar, bd_get_botw_builds

    try:
        calendar = bd_get_build_calendar(version)
        botw_data = bd_get_botw_builds(version, top=3)
        return _format_build_response(version, calendar, botw_data)
    except Exception as e:
        logger.error(f"Fast-path build query failed: {e}")
        return f"Error fetching build data for {version}: {e}"


def _format_build_response(version: str, calendar: dict, botw_data: dict) -> str:
    """Format build calendar + BOTW data into a user-friendly message."""
    parts = [f"**Build Schedule for OnBase {calendar.get('version', version)}**\n"]

    summary = calendar.get("schedule_summary", {})

    # Current BOTW cycle
    current = summary.get("current_botw_cycle")
    if current:
        parts.append("**Current BOTW Cycle:**")
        if current.get("lockdown_start"):
            parts.append(f"- Lockdown: {current['lockdown_start']} to {current.get('lockdown_end', 'N/A')}")
        parts.append(f"- BOTW Generation: {current.get('botw_generation', 'N/A')} ({current.get('botw_title', '')})")
        if current.get("publish_date"):
            parts.append(f"- Publish Build: {current['publish_date']}")
        parts.append("")

    # Next BOTW cycle
    next_cycle = summary.get("next_botw_cycle")
    if next_cycle:
        parts.append("**Next BOTW Cycle:**")
        if next_cycle.get("lockdown_start"):
            parts.append(f"- Lockdown: {next_cycle['lockdown_start']} to {next_cycle.get('lockdown_end', 'N/A')}")
        parts.append(f"- BOTW Generation: {next_cycle.get('botw_generation', 'N/A')} ({next_cycle.get('botw_title', '')})")
        if next_cycle.get("publish_date"):
            parts.append(f"- Publish Build: {next_cycle['publish_date']}")
        parts.append("")

    # Upcoming key dates
    upcoming_botw = calendar.get("upcoming_botw_dates", [])
    upcoming_lockdown = calendar.get("upcoming_lockdown_dates", [])
    if upcoming_botw or upcoming_lockdown:
        parts.append("**Upcoming Key Dates:**")
        parts.append("| Date | Event |")
        parts.append("|------|-------|")
        events = []
        for d in upcoming_lockdown[:3]:
            if isinstance(d, dict):
                events.append((d["start"], f"Lockdown (ends {d.get('end', 'N/A')})"))
            else:
                events.append((d, "Lockdown"))
        for d in upcoming_botw[:3]:
            events.append((d, "Build of the Week"))
        events.sort(key=lambda x: x[0])
        for date, event in events[:6]:
            parts.append(f"| {date} | {event} |")
        parts.append("")

    # Recent BOTW builds
    botw_builds = botw_data.get("botw_builds", [])
    if botw_builds:
        parts.append("**Recent BOTW Builds:**")
        for b in botw_builds[:3]:
            build_ver = b.get("version", "")
            completed = (b.get("completed_on") or b.get("started_on") or "")[:10]
            status = b.get("status", "")
            parts.append(f"- {build_ver} ({completed}) - {status}")

    return "\n".join(parts)


# ── ProGet fast-path patterns ──
_PROGET_PATTERNS = re.compile(
    r'proget|package\s+(?:check|search|version|lookup|info)|'
    r'latest\s+(?:cefsharp|infragistics|webview2)|'
    r'(?:cefsharp|infragistics)\s+(?:version|package)',
    re.IGNORECASE,
)

# Extract package name from common query patterns
_PACKAGE_NAME_RE = re.compile(
    r'(?:package|check|search|latest|version\s+of|look\s*up)\s+'
    r'(?:for\s+)?(?:package\s+)?["\']?([A-Za-z@][\w./@-]+)',
    re.IGNORECASE,
)

# Well-known package names that can appear without "package" keyword
_KNOWN_PACKAGES = re.compile(
    r'\b(cefsharp|infragistics\.?\w*|webview2|@hyland/\w+|newtonsoft|serilog)\b',
    re.IGNORECASE,
)


def _try_proget_fast_path(message: str) -> str | None:
    """Detect ProGet package queries and fetch directly — no LLM needed."""
    if not _PROGET_PATTERNS.search(message):
        return None

    from TFSMCP import proget_search_packages

    # Extract package name
    pkg_match = _PACKAGE_NAME_RE.search(message)
    if pkg_match:
        package_name = pkg_match.group(1).strip().strip("\"'")
    else:
        # Try well-known package names
        known_match = _KNOWN_PACKAGES.search(message)
        if known_match:
            package_name = known_match.group(1)
        else:
            return None

    # Normalize common shorthand
    pkg_lower = package_name.lower()
    if pkg_lower == "cefsharp":
        package_name = "cefsharp"
    elif pkg_lower.startswith("infragistics"):
        package_name = package_name  # keep as-is

    logger.info(f"Fast-path: ProGet search for '{package_name}'")

    try:
        result = proget_search_packages(package_name, top=10)
        return _format_proget_response(result)
    except Exception as e:
        logger.error(f"Fast-path ProGet query failed: {e}")
        return f"Error searching ProGet for '{package_name}': {e}"


def _format_proget_response(result: dict) -> str:
    """Format ProGet search results into a user-friendly message."""
    if result.get("error"):
        return f"**ProGet Search Error**: {result['error']}"

    search_term = result.get("search_term", "")
    total = result.get("total_found", 0)
    latest = result.get("latest_version", "N/A")
    latest_pkg = result.get("latest_package", search_term)
    packages = result.get("packages", [])
    url = result.get("proget_url", "")

    parts = [f"**ProGet Package Search: `{search_term}`**\n"]

    if latest:
        parts.append(f"**Latest/Highest Version**: **{latest_pkg} {latest}**\n")

    if packages:
        parts.append(f"**Found {total} package(s)** (sorted by version, highest first):\n")
        parts.append("| Feed | Package | Latest Version | Downloads | Recent Versions |")
        parts.append("|------|---------|----------------|-----------|-----------------|")
        for pkg in packages:
            recent = ", ".join(
                v.get("version", "") for v in pkg.get("all_versions", [])[:3]
            )
            parts.append(
                f"| {pkg.get('feed', '')} "
                f"| {pkg.get('name', '')} "
                f"| **{pkg.get('version', '')}** "
                f"| {pkg.get('downloads', 0):,} "
                f"| {recent} |"
            )
    else:
        parts.append(f"No packages found matching `{search_term}` on ProGet.")

    if url:
        parts.append(f"\n🔗 [View on ProGet]({url})")

    return "\n".join(parts)


# ── Branch compare fast-path patterns ──
_BRANCH_COMPARE_PATTERNS = re.compile(
    r'compare\s+(\w+(?:\.\d+)?)\s+(?:and|vs|with|to)\s+(\w+(?:\.\d+)?)\s+(?:for|file)\s+(.+)',
    re.IGNORECASE,
)

_BRANCH_COMPARE_ALT = re.compile(
    r'(?:diff|compare|difference)\s+(?:between\s+)?(?:branch(?:es)?\s+)?'
    r'(\w+(?:\.\d+)?)\s+(?:and|vs|with|to)\s+(\w+(?:\.\d+)?)\s+(?:for|in|file)\s+(.+)',
    re.IGNORECASE,
)


def _try_branch_compare_fast_path(message: str) -> str | None:
    """Detect branch compare queries and call compute_branch_file_diff directly."""
    match = _BRANCH_COMPARE_PATTERNS.search(message)
    if not match:
        match = _BRANCH_COMPARE_ALT.search(message)
    if not match:
        return None

    source_ver = match.group(1).strip()
    target_ver = match.group(2).strip()
    file_path = match.group(3).strip().strip("\"'`")

    from TFSMCP import compute_branch_file_diff, resolve_branch_path, list_branch_files

    logger.info(f"Fast-path: branch compare {source_ver} vs {target_ver} for '{file_path}'")

    try:
        source_root = resolve_branch_path(source_ver)
        target_root = resolve_branch_path(target_ver)

        # If user gave just a filename (no path separators), search TFS for the full path
        if "/" not in file_path and "\\" not in file_path:
            logger.info(f"  Bare filename detected — trying TFS path resolution for '{file_path}'")
            # Search common subdirectories in priority order (most likely first)
            _SEARCH_SUBDIRS = ["WorkView", "Libraries", "Applications"]
            found = False
            for subdir in _SEARCH_SUBDIRS:
                try:
                    sub_files = list_branch_files(f"{source_root}/{subdir}", recursion="Full")
                    matches = [
                        f for f in sub_files
                        if f["path"].lower().endswith("/" + file_path.lower())
                    ]
                    if matches:
                        best = matches[0]["path"]
                        if best.lower().startswith(source_root.lower()):
                            file_path = best[len(source_root):].lstrip("/")
                        else:
                            file_path = best
                        logger.info(f"  Resolved to: {file_path} (in {subdir}/)")
                        found = True
                        break
                except Exception:
                    continue
            if not found:
                logger.warning(f"  File '{file_path}' not found in priority TFS subdirs")

        result = compute_branch_file_diff(file_path, source_root, target_root)

        if isinstance(result, str):
            import json as _json_mod
            result = _json_mod.loads(result)

        return _format_branch_compare_response(source_ver, target_ver, file_path, result)
    except Exception as e:
        logger.error(f"Fast-path branch compare failed: {e}")
        return f"Error comparing `{file_path}` between {source_ver} and {target_ver}: {e}"


def _format_branch_compare_response(
    source_ver: str, target_ver: str, file_path: str, result: dict,
) -> str:
    """Format branch compare result into a user-friendly message."""
    parts = [f"**Branch Compare: `{file_path}`**"]
    parts.append(f"**{source_ver}** vs **{target_ver}**\n")

    source_exists = result.get("source_exists", True)
    target_exists = result.get("target_exists", True)

    if not source_exists and not target_exists:
        return f"File `{file_path}` does not exist in either {source_ver} or {target_ver}."
    if not source_exists:
        parts.append(f"⚠️ File exists only in **{target_ver}** (new file)")
    elif not target_exists:
        parts.append(f"⚠️ File exists only in **{source_ver}** (deleted in {target_ver})")

    lines_added = result.get("lines_added", 0)
    lines_removed = result.get("lines_removed", 0)

    if lines_added == 0 and lines_removed == 0 and source_exists and target_exists:
        parts.append("✅ **Files are identical** — no differences found.")
        return "\n".join(parts)

    parts.append(f"📊 **+{lines_added} lines added**, **-{lines_removed} lines removed**\n")

    diff = result.get("diff", "")
    if diff:
        # Truncate very long diffs for chat display
        if len(diff) > 3000:
            diff = diff[:3000] + "\n... (diff truncated — use TFS directly for full diff)"
        parts.append(f"```diff\n{diff}\n```")

    return "\n".join(parts)


def _try_code_review_fast_path(message: str) -> str | None:
    """Detect code review requests and handle them directly (no LLM needed).

    Returns a formatted response string, or None if the message is not a code review request.
    """
    from TFSMCP import start_code_review, get_code_review_status

    # Extract Jira key from the message
    key_match = _JIRA_KEY_RE.search(message)
    if not key_match:
        return None

    jira_key = key_match.group(1)

    # Check if this is a status check
    if _CODE_REVIEW_STATUS_PATTERNS.search(message):
        logger.info(f"Fast-path: code review STATUS for {jira_key}")
        try:
            result = get_code_review_status(jira_key)
            return _format_review_status(jira_key, result)
        except Exception as e:
            logger.error(f"Fast-path status check failed: {e}")
            return f"Error checking review status for {jira_key}: {e}"

    # Check if this is a code review start request
    if _CODE_REVIEW_START_PATTERNS.search(message):
        logger.info(f"Fast-path: START code review for {jira_key}")
        try:
            result = start_code_review(jira_key)
            return _format_review_start(jira_key, result)
        except Exception as e:
            logger.error(f"Fast-path code review start failed: {e}")
            return f"Error starting code review for {jira_key}: {e}"

    return None


def _format_review_start(jira_key: str, result: dict) -> str:
    """Format the start_code_review result into a user-friendly message."""
    status = result.get("status", "unknown")

    if status == "started":
        return (
            f"✅ Code review for "
            f"[{jira_key}](https://hyland.atlassian.net/browse/{jira_key}) "
            f"has been kicked off!\n\n"
            f"**Status**: In progress ⏳\n\n"
            f"**What's happening behind the scenes:**\n"
            f"1. Fetching Jira card details and linked changesets\n"
            f"2. Retrieving TFS code diffs\n"
            f"3. Running AI-powered code analysis\n"
            f"4. Generating a detailed HTML code review report\n"
            f"5. Attaching the report to the Jira card\n\n"
            f"⏱️ **Estimated time**: 45-60 seconds\n\n"
            f"⚠️ **Important**: I cannot send you a notification when it's done. "
            f"You need to come back and ask me:\n\n"
            f"> **\"review status {jira_key}\"**\n\n"
            f"The report WILL be generated — just check back in about a minute."
        )
    elif status == "already_running":
        elapsed = result.get("elapsed_seconds", 0)
        pct = result.get("progress_pct", 0)
        phase_desc = result.get("phase_description", "In progress...")
        return (
            f"**Code review for {jira_key} is already running** ⏳\n\n"
            f"- **Progress**: {pct}% — {phase_desc}\n"
            f"- **Elapsed**: {elapsed}s\n\n"
            f"Check back by asking **\"review status {jira_key}\"** in about "
            f"{max(10, 55 - round(elapsed))} seconds.\n\n"
            f"*(I can't send notifications — you need to ask me for status.)*"
        )
    else:
        return result.get("message", f"Code review for {jira_key}: {status}")


def _format_review_status(jira_key: str, result: dict) -> str:
    """Format the get_code_review_status result into a user-friendly message."""
    status = result.get("status", "unknown")

    if status == "done":
        summary = result.get("summary", "")
        verdict = result.get("verdict", "")
        findings = result.get("findings_count", 0)
        sev_line = result.get("findings_by_severity", "")
        top_finding = result.get("top_finding", "")
        attachment_url = result.get("attachment_url", "")
        report_path = result.get("report_local_path", "")

        # Verdict emoji
        verdict_emoji = {"Approve": "✅", "Request Changes": "❌",
                         "Needs Discussion": "⚠️"}.get(verdict, "ℹ️")

        jira_link = f"[{jira_key}](https://hyland.atlassian.net/browse/{jira_key})"

        # ── Upfront brief summary ──
        parts = [
            f"**Code Review — {jira_link}**\n",
            f"{verdict_emoji} **Verdict: {verdict}** | "
            f"**{findings} finding{'s' if findings != 1 else ''}** ({sev_line})",
        ]
        if top_finding:
            parts.append(f"🔍 **Top finding:** {top_finding}")

        # Report link — prefer Jira attachment, fallback to dev tunnel
        if attachment_url:
            parts.append(f"\n📄 **Full report:** [Download from Jira]({attachment_url})")
        elif report_path:
            full_url = get_report_url(report_path)
            parts.append(f"\n📄 **Full report:** {full_url}")

        if summary:
            parts.append(f"\n**Summary:**\n{summary}")

        return "\n".join(parts)

    elif status == "running":
        pct = result.get("progress_pct", 0)
        phase_desc = result.get("phase_description", "In progress...")
        elapsed = result.get("elapsed_seconds", 0)
        return (
            f"**Code review for {jira_key} is still running** ⏳\n\n"
            f"- **Progress**: {pct}% — {phase_desc}\n"
            f"- **Elapsed**: {elapsed:.0f}s\n\n"
            f"Check again in about {max(10, 55 - round(elapsed))} seconds."
        )

    elif status == "error":
        error = result.get("error", "Unknown error")
        return (
            f"**Code review for {jira_key} encountered an error** ❌\n\n"
            f"Error: {error}\n\n"
            f"You can retry by asking: **\"code review {jira_key}\"**"
        )

    elif status == "not_found":
        return (
            f"No code review found for {jira_key}. "
            f"To start one, ask: **\"code review {jira_key}\"**"
        )

    else:
        return result.get("message", f"Review status for {jira_key}: {status}")


# ── GHAS Fix + PR Fast-Path ──

def _try_ghas_fix_pr_fast_path(message: str) -> str | None:
    """Detect GHAS fix + PR requests and route to start/status directly."""
    from TFSMCP import start_ghas_fix_pr, get_ghas_fix_pr_status, resolve_github_repo, GITHUB_ORG

    # Check for status request first
    status_match = _GHAS_FIX_PR_STATUS_PATTERNS.search(message)
    if status_match:
        alert_num = next((g for g in status_match.groups() if g), None)
        if alert_num:
            owner, repo = _extract_repo_from_message(message)
            if owner and repo:
                logger.info(f"Fast-path: GHAS fix PR STATUS for {owner}/{repo} alert #{alert_num}")
                try:
                    result = get_ghas_fix_pr_status(owner, repo, int(alert_num))
                    return _format_ghas_fix_status(owner, repo, int(alert_num), result)
                except Exception as e:
                    logger.error(f"Fast-path GHAS fix status failed: {e}")
                    return f"Error checking GHAS fix status: {e}"

    # Check for start request
    if _GHAS_FIX_PR_START_PATTERNS.search(message):
        alert_match = _GHAS_ALERT_NUMBER_RE.search(message)
        if alert_match:
            alert_num = int(alert_match.group(1))
            owner, repo = _extract_repo_from_message(message)
            if owner and repo:
                logger.info(f"Fast-path: START GHAS fix PR for {owner}/{repo} alert #{alert_num}")
                try:
                    result = start_ghas_fix_pr(owner, repo, alert_num)
                    return _format_ghas_fix_start(owner, repo, alert_num, result)
                except Exception as e:
                    logger.error(f"Fast-path GHAS fix start failed: {e}")
                    return f"Error starting GHAS fix for alert #{alert_num}: {e}"

    return None


def _extract_repo_from_message(message: str) -> tuple[str, str]:
    """Extract owner/repo from message. Returns (owner, repo) or ('', '')."""
    from TFSMCP import resolve_github_repo, GITHUB_ORG

    # Look for "owner/repo" pattern
    repo_pattern = re.compile(r'(?:in|on|for|repo)\s+(\S+/\S+)', re.IGNORECASE)
    m = repo_pattern.search(message)
    if m:
        parts = m.group(1).strip().rstrip(",.?!").split("/", 1)
        if len(parts) == 2:
            return parts[0], parts[1]

    # Look for just a repo name (assume GITHUB_ORG)
    repo_name_pattern = re.compile(r'(?:in|on|for|repo)\s+([A-Za-z0-9_.-]+)', re.IGNORECASE)
    m = repo_name_pattern.search(message)
    if m:
        repo_name = m.group(1).strip().rstrip(",.?!")
        # Filter out common non-repo words
        if repo_name.lower() not in ("the", "a", "this", "my", "our", "that", "alert", "build", "failure", "status", "pr", "fix", "done", "ready"):
            owner, repo = resolve_github_repo(repo_name)
            return owner, repo

    # Look for known repo names directly in the message
    known_repos = ["GHAS-POC-AngularClient", "GHAS-POC-ReactClient", "GHAS-POC-DotNetAPI"]
    for kr in known_repos:
        if kr.lower() in message.lower():
            return GITHUB_ORG, kr

    return "", ""


def _format_ghas_fix_start(owner: str, repo: str, alert_number: int, result: dict) -> str:
    """Format the start_ghas_fix_pr result."""
    status = result.get("status", "unknown")

    if status == "started":
        return (
            f"🔧 **GHAS Fix + PR creation started for "
            f"[{owner}/{repo}](https://github.com/{owner}/{repo}) "
            f"alert #{alert_number}!**\n\n"
            f"**What's happening:**\n"
            f"1. Fetching alert details from GitHub\n"
            f"2. Retrieving the affected source file\n"
            f"3. Generating code fix with AI\n"
            f"4. Creating a fix branch\n"
            f"5. Pushing the fixed code\n"
            f"6. Opening a pull request\n\n"
            f"⏱️ **Estimated time**: ~15-20 seconds\n\n"
            f"⚠️ **Important**: Check back by asking:\n\n"
            f"> **\"PR status for alert #{alert_number} in {repo}\"**\n\n"
            f"The PR WILL be created — just check back in about 20 seconds."
        )
    elif status == "running":
        pct = result.get("progress_pct", 0)
        phase_desc = result.get("phase_description", "In progress...")
        elapsed = result.get("elapsed_seconds", 0)
        return (
            f"**GHAS fix already in progress for alert #{alert_number}** ⏳\n\n"
            f"- **Progress**: {pct}% — {phase_desc}\n"
            f"- **Elapsed**: {elapsed:.0f}s\n\n"
            f"Check back by asking **\"PR status for alert #{alert_number} in {repo}\"**."
        )
    elif status == "done":
        return _format_ghas_fix_done(owner, repo, alert_number, result)
    else:
        return result.get("message", f"GHAS fix for alert #{alert_number}: {status}")


def _format_ghas_fix_status(owner: str, repo: str, alert_number: int, result: dict) -> str:
    """Format the get_ghas_fix_pr_status result."""
    status = result.get("status", "unknown")

    if status == "done":
        return _format_ghas_fix_done(owner, repo, alert_number, result)

    elif status == "running":
        pct = result.get("progress_pct", 0)
        phase_desc = result.get("phase_description", "In progress...")
        elapsed = result.get("elapsed_seconds", 0)
        return (
            f"**GHAS fix for alert #{alert_number} is still running** ⏳\n\n"
            f"- **Progress**: {pct}% — {phase_desc}\n"
            f"- **Elapsed**: {elapsed:.0f}s\n\n"
            f"Check again in about {max(5, 20 - round(elapsed))} seconds."
        )

    elif status == "error":
        error = result.get("error", "Unknown error")
        return (
            f"**GHAS fix for alert #{alert_number} failed** ❌\n\n"
            f"Error: {error}\n\n"
            f"You can retry by asking: **\"fix alert #{alert_number} in {repo} and create PR\"**"
        )

    elif status == "not_found":
        return (
            f"No GHAS fix job found for alert #{alert_number}. "
            f"To start one, ask: **\"fix alert #{alert_number} in {repo} and create PR\"**"
        )

    else:
        return result.get("message", f"GHAS fix status for alert #{alert_number}: {status}")


def _format_ghas_fix_done(owner: str, repo: str, alert_number: int, result: dict) -> str:
    """Format a completed GHAS fix + PR result."""
    pr_url = result.get("pr_url", "")
    pr_number = result.get("pr_number", "")
    branch = result.get("branch", "")
    alert_url = result.get("alert_url", "")
    file_path = result.get("file_path", "")
    elapsed = result.get("elapsed_seconds", 0)
    severity = result.get("alert_severity", "")
    rule = result.get("alert_rule", "")

    parts = [
        f"✅ **GHAS Fix + PR created for alert #{alert_number}!**\n",
        f"- **Alert**: [{rule}]({alert_url}) (severity: {severity})",
        f"- **File fixed**: `{file_path}`",
        f"- **Branch**: `{branch}`",
    ]
    if pr_url:
        parts.append(f"- **Pull Request**: [PR #{pr_number}]({pr_url})")
    if elapsed:
        parts.append(f"- **Completed in**: {elapsed}s")

    parts.append(
        f"\n🔍 **Next steps**: Please review the PR and merge if the fix looks correct."
    )

    return "\n".join(parts)


# ── Build Failure Analysis Fast-Path ──

def _try_build_failure_fast_path(message: str) -> str | None:
    """Detect build failure analysis requests and route directly."""
    from TFSMCP import start_build_failure_analysis, get_build_failure_analysis_status

    # Extract build ID from TFS build URL if present (e.g. buildId=812833)
    url_build_match = re.search(r'buildId=(\d+)', message, re.IGNORECASE)

    # Check for status request first
    status_match = _BUILD_FAILURE_STATUS_PATTERNS.search(message)
    if status_match:
        build_id = next((g for g in status_match.groups() if g), None)
        if build_id:
            logger.info(f"Fast-path: build failure STATUS for build {build_id}")
            try:
                result = get_build_failure_analysis_status(int(build_id))
                return _format_build_failure_status(int(build_id), result)
            except Exception as e:
                logger.error(f"Fast-path build failure status failed: {e}")
                return f"Error checking build failure analysis status: {e}"

    # Check for start request (regex patterns OR TFS URL)
    start_match = _BUILD_FAILURE_START_PATTERNS.search(message)
    build_id_str = None
    if start_match:
        build_id_str = next((g for g in start_match.groups() if g), None)
    elif url_build_match:
        # URL like http://dev-tfs:8080/...?buildId=812833 with failure-related words
        if re.search(r'fail|broke|broken|red|analyze|investigate|why|what\s+happened', message, re.IGNORECASE) or url_build_match:
            build_id_str = url_build_match.group(1)

    if build_id_str:
        # Extract Jira key if present
        jira_match = _JIRA_KEY_RE.search(message)
        jira_key = jira_match.group(1) if jira_match else ""
        logger.info(f"Fast-path: START build failure analysis for build {build_id_str} (jira={jira_key})")
        try:
            result = start_build_failure_analysis(int(build_id_str), jira_key)
            return _format_build_failure_start(int(build_id_str), jira_key, result)
        except Exception as e:
            logger.error(f"Fast-path build failure start failed: {e}")
            return f"Error starting build failure analysis: {e}"

    return None


def _format_build_failure_start(build_id: int, jira_key: str, result: dict) -> str:
    """Format the start_build_failure_analysis result."""
    status = result.get("status", "unknown")

    if status == "started":
        jira_note = ""
        if jira_key:
            jira_note = (
                f"\n📎 The report will be **attached to "
                f"[{jira_key}](https://hyland.atlassian.net/browse/{jira_key})** when done.\n"
            )
        return (
            f"🔍 **Build failure analysis started for build {build_id}!**\n\n"
            f"**What's happening:**\n"
            f"1. Fetching build details and timeline\n"
            f"2. Retrieving failed task logs\n"
            f"3. Fetching associated changesets\n"
            f"4. Gathering Jira context\n"
            f"5. Running AI root cause analysis\n"
            f"6. Generating HTML report\n"
            f"7. Attaching report to Jira card\n\n"
            f"⏱️ **Estimated time**: 40-60 seconds\n"
            f"{jira_note}\n"
            f"⚠️ Check back by asking:\n\n"
            f"> **\"build failure status {build_id}\"**"
        )
    elif status == "running":
        pct = result.get("progress_pct", 0)
        phase_desc = result.get("phase_description", "In progress...")
        elapsed = result.get("elapsed_seconds", 0)
        return (
            f"**Build failure analysis for build {build_id} is already running** ⏳\n\n"
            f"- **Progress**: {pct}% — {phase_desc}\n"
            f"- **Elapsed**: {elapsed:.0f}s\n\n"
            f"Check back by asking **\"build failure status {build_id}\"**."
        )
    elif status == "done":
        return _format_build_failure_done(build_id, result)
    else:
        return result.get("message", f"Build failure analysis for build {build_id}: {status}")


def _format_build_failure_status(build_id: int, result: dict) -> str:
    """Format the get_build_failure_analysis_status result."""
    status = result.get("status", "unknown")

    if status == "done":
        return _format_build_failure_done(build_id, result)

    elif status == "running":
        pct = result.get("progress_pct", 0)
        phase_desc = result.get("phase_description", "In progress...")
        elapsed = result.get("elapsed_seconds", 0)
        return (
            f"**Build failure analysis for build {build_id} is still running** ⏳\n\n"
            f"- **Progress**: {pct}% — {phase_desc}\n"
            f"- **Elapsed**: {elapsed:.0f}s\n\n"
            f"Check again in about {max(10, 55 - round(elapsed))} seconds."
        )

    elif status == "error":
        error = result.get("error", "Unknown error")
        return (
            f"**Build failure analysis for build {build_id} failed** ❌\n\n"
            f"Error: {error}\n\n"
            f"You can retry by asking: **\"analyze build failure {build_id}\"**"
        )

    elif status == "not_found":
        return (
            f"No build failure analysis found for build {build_id}. "
            f"To start one, ask: **\"analyze build failure {build_id}\"**"
        )

    else:
        return result.get("message", f"Build failure status for build {build_id}: {status}")


def _format_build_failure_done(build_id: int, result: dict) -> str:
    """Format a completed build failure analysis result."""
    root_cause = result.get("root_cause", "")
    severity = result.get("severity", "")
    category = result.get("failure_category", "")
    jira_key = result.get("jira_key", "")
    attachment_url = result.get("attachment_url", "")
    report_path = result.get("report_local_path", "")
    build_number = result.get("build_number", "")
    definition = result.get("definition_name", "")
    failed_count = result.get("failed_tasks_count", 0)
    changesets_count = result.get("changesets_count", 0)
    elapsed = result.get("elapsed_seconds", 0)

    severity_emoji = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🟢"}.get(severity, "ℹ️")

    parts = [
        f"✅ **Build Failure Analysis Complete — Build {build_id}**\n",
    ]
    if build_number:
        parts.append(f"- **Build**: {build_number} ({definition})")
    parts.append(f"- {severity_emoji} **Severity**: {severity}")
    if category:
        parts.append(f"- **Category**: {category}")
    parts.append(f"- **Failed tasks**: {failed_count} | **Changesets**: {changesets_count}")
    if root_cause:
        parts.append(f"\n**Root Cause:**\n{root_cause}")

    # Report link — prefer Jira attachment
    if attachment_url and jira_key:
        parts.append(
            f"\n📄 **Full report**: [Download from Jira "
            f"({jira_key})]({attachment_url})"
        )
    elif report_path:
        full_url = get_report_url(report_path)
        parts.append(f"\n📄 **Full report**: {full_url}")

    if elapsed:
        parts.append(f"\n⏱️ Completed in {elapsed}s")

    return "\n".join(parts)


# ── Jenkins Pipeline Failure Fast-Path ──

_JENKINS_URL_RE = re.compile(
    r'https?://[^\s]+jenkins[^\s]*/(blue/organizations/jenkins/[^\s]+|job/[^\s]+)',
    re.IGNORECASE,
)

_JENKINS_KEYWORDS_RE = re.compile(
    r'jenkins\s*(pipeline|failure|fail|build|run|analyze|broken|red|unstable)',
    re.IGNORECASE,
)


def _try_jenkins_fast_path(message: str) -> str | None:
    """Detect Jenkins pipeline URLs and analyze failures directly."""
    from TFSMCP import jenkins_analyze_failure

    url_match = _JENKINS_URL_RE.search(message)
    if not url_match:
        return None

    url = url_match.group(0).rstrip('.,;:!?)')
    logger.info(f"Fast-path: Jenkins pipeline analysis for {url}")

    try:
        result = jenkins_analyze_failure(url)
        if isinstance(result, str):
            import json as _json
            result = _json.loads(result)
        return _format_jenkins_analysis(url, result)
    except Exception as e:
        logger.error(f"Fast-path Jenkins analysis failed: {e}")
        return f"❌ Error analyzing Jenkins pipeline: {e}"


def _format_jenkins_analysis(url: str, result: dict) -> str:
    """Format jenkins_analyze_failure output for Teams."""
    if result.get("error"):
        return f"❌ **Jenkins Analysis Failed**\n\n{result['error']}"

    pipeline = result.get("pipeline", "")
    branch = result.get("branch", "")
    run_number = result.get("run_number", "")
    run_result = result.get("result", "UNKNOWN")
    duration_s = result.get("duration_seconds", 0)
    commit = (result.get("commit_id") or "")[:8]
    summary = result.get("summary", {})
    stages = result.get("stages", [])
    failure_details = result.get("failure_details", [])

    result_emoji = {
        "SUCCESS": "✅", "UNSTABLE": "⚠️", "FAILURE": "❌",
        "ABORTED": "⏹️", "NOT_BUILT": "⬜",
    }.get(run_result, "❓")

    parts = [
        f"{result_emoji} **Jenkins Pipeline: {pipeline}**\n",
        f"- **Branch**: {branch} | **Run**: #{run_number}",
        f"- **Result**: {run_result} | **Duration**: {duration_s}s",
    ]
    if commit:
        parts.append(f"- **Commit**: `{commit}`")

    causes = result.get("causes", [])
    if causes:
        parts.append(f"- **Trigger**: {causes[0]}")

    # Stage summary
    total = summary.get("total_stages", len(stages))
    succeeded = summary.get("succeeded", 0)
    failed = summary.get("failed", 0)
    skipped = summary.get("skipped", 0)
    parts.append(f"\n**Stages**: {total} total | ✅ {succeeded} passed | ❌ {failed} failed/unstable | ⬜ {skipped} skipped\n")

    # Failed stages detail
    if failure_details:
        parts.append("### Failed / Unstable Stages\n")
        for fd in failure_details:
            stage_name = fd.get("node_name", "Unknown")
            node_id = fd.get("node_id", "")
            # Find the stage result
            stage_result = "FAILURE"
            for s in stages:
                if s.get("id") == node_id:
                    stage_result = s.get("result", "FAILURE")
                    break
            stage_emoji = "❌" if stage_result == "FAILURE" else "⚠️"
            parts.append(f"{stage_emoji} **{stage_name}** (node {node_id}) — {stage_result}")

            # Failed steps within this stage
            failed_steps = fd.get("failed_steps", [])
            if failed_steps:
                for step in failed_steps[:5]:
                    step_name = step.get("name", "")
                    step_desc = step.get("description", "")
                    desc_text = f" — {step_desc}" if step_desc else ""
                    parts.append(f"  └─ {step_name}{desc_text}")

            # Error summary
            error_summary = fd.get("error_summary", "")
            if error_summary:
                # Trim to last 500 chars for readability
                excerpt = error_summary[-500:].strip() if len(error_summary) > 500 else error_summary.strip()
                parts.append(f"\n```\n{excerpt}\n```\n")
            elif fd.get("log_tail"):
                # Show tail of log if no error summary
                log_tail = fd["log_tail"]
                excerpt = log_tail[-500:].strip() if len(log_tail) > 500 else log_tail.strip()
                parts.append(f"\n```\n{excerpt}\n```\n")

            # TFS build cross-reference
            tfs_build_id = fd.get("tfs_build_id")
            tfs_build_link = fd.get("tfs_build_link")
            if tfs_build_id:
                tfs_url = tfs_build_link or f"http://dev-tfs:8080/tfs/HylandCollection/OnBase/_build/results?buildId={tfs_build_id}"
                parts.append(f"  🔗 **TFS Build**: [{tfs_build_id}]({tfs_url})\n")

    # Blue Ocean link
    bo_url = result.get("blue_ocean_url", url)
    parts.append(f"\n🔗 [View in Jenkins Blue Ocean]({bo_url})")

    return "\n".join(parts)


# ── Endpoints ──

@app.get("/")
def root():
    return {
        "name": "AgentONE",
        "version": "1.0.0",
        "tools": len(TOOL_DEFINITIONS),
        "agents": ["tfs_jira", "onbase", "mrg_parser", "accessibility"],
    }


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, x_api_key: str | None = Header(None)):
    _verify_api_key(x_api_key)

    conversation_id = req.conversation_id or str(uuid.uuid4())
    logger.info(f"Chat [{conversation_id[:8]}]: {req.message[:100]}")

    try:
        # ── UX: Welcome / Help / Skill Menu interception ──
        if is_welcome_message(req.message):
            track_conversation_start(conversation_id)
            return ChatResponse(
                response=WELCOME_MESSAGE,
                agent_used="system",
                conversation_id=conversation_id,
                tool_calls_made=0,
                suggested_actions=STANDARD_ACTIONS,
            )

        if is_skill_menu_request(req.message):
            return ChatResponse(
                response=SKILL_MENU,
                agent_used="system",
                conversation_id=conversation_id,
                tool_calls_made=0,
                suggested_actions=STANDARD_ACTIONS,
            )

        # ── UX: Pending action — user is providing a parameter for a previous bare command ──
        combined_message = check_pending_action(req.message, conversation_id)
        if combined_message:
            logger.info(f"Pending action resolved: '{req.message}' → '{combined_message}'")
            # Replace the request message with the combined command and fall through
            req = ChatRequest(message=combined_message, conversation_id=req.conversation_id)

        # ── UX: Bare command — user clicked a suggestion without parameters ──
        bare_prompt = check_bare_command(req.message, conversation_id)
        if bare_prompt is not None:
            return ChatResponse(
                response=bare_prompt,
                agent_used="system",
                conversation_id=conversation_id,
                tool_calls_made=0,
                suggested_actions=[],  # No suggestions while waiting for input
            )

        # ── Async continuation: check if user is asking for a background task result ──
        if _TASK_STATUS_PATTERNS.search(req.message.strip()):
            bg = _background_tasks.pop(conversation_id, None)
            if bg and bg["future"].done():
                try:
                    result = bg["future"].result(timeout=0)
                    enriched = enrich_response(
                        response_text=result["response"],
                        user_message=bg["message"],
                        agent_used=result["agent_used"],
                        conversation_id=conversation_id,
                        tool_calls_made=result["tool_calls_made"],
                    )
                    return ChatResponse(
                        response=f"✅ **Your previous task finished!**\n\n{enriched}",
                        agent_used=result["agent_used"],
                        conversation_id=conversation_id,
                        tool_calls_made=result["tool_calls_made"],
                        suggested_actions=get_suggested_actions(enriched, bg["message"]),
                    )
                except Exception as e:
                    return ChatResponse(
                        response=f"The background task finished with an error: {e}",
                        agent_used="system",
                        conversation_id=conversation_id,
                        suggested_actions=STANDARD_ACTIONS,
                    )
            elif bg and not bg["future"].done():
                elapsed = int(time.time() - bg["started_at"])
                _background_tasks[conversation_id] = bg  # put it back
                return ChatResponse(
                    response=(
                        f"⏳ Your task is still running ({elapsed}s elapsed).\n\n"
                        f"Original request: *\"{bg['message'][:80]}\"*\n\n"
                        f"Check back in a bit — say **\"check task status\"** again."
                    ),
                    agent_used="system",
                    conversation_id=conversation_id,
                    suggested_actions=[],
                )
            else:
                return ChatResponse(
                    response="No background tasks found. Start a new request and I'll process it for you.",
                    agent_used="system",
                    conversation_id=conversation_id,
                    suggested_actions=STANDARD_ACTIONS,
                )

        # ── Fast-path: Code review requests bypass LLM for speed ──
        # Code reviews are async 2-step operations. Routing through the LLM
        # adds 5-15s of latency that can push past Copilot Studio's 30s timeout.
        # Detect the pattern directly and call start_code_review / get_code_review_status.
        fast_result = _try_code_review_fast_path(req.message)
        if fast_result is not None:
            enriched = enrich_response(
                response_text=fast_result,
                user_message=req.message,
                agent_used="tfs_jira",
                conversation_id=conversation_id,
                tool_calls_made=1,
            )
            return ChatResponse(
                response=enriched,
                agent_used="tfs_jira",
                conversation_id=conversation_id,
                tool_calls_made=1,
                suggested_actions=get_suggested_actions(fast_result, req.message),
            )

        # ── Fast-path: GHAS Fix + PR creation bypass LLM ──
        fast_result = _try_ghas_fix_pr_fast_path(req.message)
        if fast_result is not None:
            enriched = enrich_response(
                response_text=fast_result,
                user_message=req.message,
                agent_used="tfs_jira",
                conversation_id=conversation_id,
                tool_calls_made=1,
            )
            return ChatResponse(
                response=enriched,
                agent_used="tfs_jira",
                conversation_id=conversation_id,
                tool_calls_made=1,
                suggested_actions=get_suggested_actions(fast_result, req.message),
            )

        # ── Fast-path: Build failure analysis bypass LLM ──
        fast_result = _try_build_failure_fast_path(req.message)
        if fast_result is not None:
            enriched = enrich_response(
                response_text=fast_result,
                user_message=req.message,
                agent_used="tfs_jira",
                conversation_id=conversation_id,
                tool_calls_made=1,
            )
            return ChatResponse(
                response=enriched,
                agent_used="tfs_jira",
                conversation_id=conversation_id,
                tool_calls_made=1,
                suggested_actions=get_suggested_actions(fast_result, req.message),
            )

        # ── Fast-path: Jenkins pipeline failure analysis bypass LLM ──
        fast_result = _try_jenkins_fast_path(req.message)
        if fast_result is not None:
            enriched = enrich_response(
                response_text=fast_result,
                user_message=req.message,
                agent_used="tfs_jira",
                conversation_id=conversation_id,
                tool_calls_made=1,
            )
            return ChatResponse(
                response=enriched,
                agent_used="tfs_jira",
                conversation_id=conversation_id,
                tool_calls_made=1,
                suggested_actions=get_suggested_actions(fast_result, req.message),
            )

        # ── Fast-path: "What changed since previous build" — JQL-based ──
        fast_result = _try_what_changed_fast_path(req.message, conversation_id)
        if fast_result is not None:
            # Save to conversation history so follow-ups have context
            history = _conversations.get(conversation_id, [])
            history.append({"role": "user", "content": req.message})
            history.append({"role": "assistant", "content": fast_result})
            if len(history) > MAX_HISTORY * 2:
                history = history[-(MAX_HISTORY * 2):]
            _conversations[conversation_id] = history

            enriched = enrich_response(
                response_text=fast_result,
                user_message=req.message,
                agent_used="tfs_jira",
                conversation_id=conversation_id,
                tool_calls_made=1,
            )
            return ChatResponse(
                response=enriched,
                agent_used="tfs_jira",
                conversation_id=conversation_id,
                tool_calls_made=1,
                suggested_actions=get_suggested_actions(fast_result, req.message),
            )

        # ── Fast-path: Build/BOTW queries bypass LLM ──
        fast_result = _try_build_fast_path(req.message)
        if fast_result is not None:
            enriched = enrich_response(
                response_text=fast_result,
                user_message=req.message,
                agent_used="tfs_jira",
                conversation_id=conversation_id,
                tool_calls_made=1,
            )
            return ChatResponse(
                response=enriched,
                agent_used="tfs_jira",
                conversation_id=conversation_id,
                tool_calls_made=1,
                suggested_actions=get_suggested_actions(fast_result, req.message),
            )

        # ── Fast-path: ProGet package queries bypass LLM ──
        fast_result = _try_proget_fast_path(req.message)
        if fast_result is not None:
            enriched = enrich_response(
                response_text=fast_result,
                user_message=req.message,
                agent_used="tfs_jira",
                conversation_id=conversation_id,
                tool_calls_made=1,
            )
            return ChatResponse(
                response=enriched,
                agent_used="tfs_jira",
                conversation_id=conversation_id,
                tool_calls_made=1,
                suggested_actions=get_suggested_actions(fast_result, req.message),
            )

        # ── Fast-path: Branch compare queries bypass LLM ──
        fast_result = _try_branch_compare_fast_path(req.message)
        if fast_result is not None:
            enriched = enrich_response(
                response_text=fast_result,
                user_message=req.message,
                agent_used="tfs_jira",
                conversation_id=conversation_id,
                tool_calls_made=1,
            )
            return ChatResponse(
                response=enriched,
                agent_used="tfs_jira",
                conversation_id=conversation_id,
                tool_calls_made=1,
                suggested_actions=get_suggested_actions(fast_result, req.message),
            )

        # Track conversation for tip frequency
        track_conversation_start(conversation_id)

        # Route to agent
        agent_name = route(req.message)
        agent_module = get_agent_module(agent_name)
        logger.info(f"Routed to: {agent_name}")

        # Get conversation history
        history = _conversations.get(conversation_id, [])

        # Execute agent with timeout protection
        # Copilot Studio connector times out at ~120s; we cap at RESPONSE_TIMEOUT
        def _run_agent():
            return execute(
                agent_name=agent_name,
                system_prompt=agent_module.SYSTEM_PROMPT,
                user_message=req.message,
                conversation_history=history if history else None,
            )

        future = _executor.submit(_run_agent)
        try:
            result = future.result(timeout=RESPONSE_TIMEOUT)
        except FuturesTimeoutError:
            logger.warning(f"Chat [{conversation_id[:8]}] timed out after {RESPONSE_TIMEOUT}s")
            # Don't cancel — let it finish in background so user can retrieve later
            _background_tasks[conversation_id] = {
                "future": future,
                "agent": agent_name,
                "message": req.message,
                "started_at": time.time() - RESPONSE_TIMEOUT,
            }
            # Clean up stale tasks
            cutoff = time.time() - _BACKGROUND_TASK_TTL
            for cid in list(_background_tasks):
                if _background_tasks[cid]["started_at"] < cutoff:
                    _background_tasks.pop(cid, None)

            return ChatResponse(
                response=(
                    f"⏳ This is taking longer than expected ({RESPONSE_TIMEOUT}s). "
                    f"The task is still running in the background.\n\n"
                    f"Come back and say **\"check task status\"** to get the result "
                    f"when it's done.\n\n"
                    f"*(I can't notify you — you need to ask me.)*"
                ),
                agent_used=agent_name,
                conversation_id=conversation_id,
                tool_calls_made=0,
                suggested_actions=STANDARD_ACTIONS,
            )

        # Update conversation history
        history.append({"role": "user", "content": req.message})
        history.append({"role": "assistant", "content": result["response"]})
        # Sliding window
        if len(history) > MAX_HISTORY * 2:
            history = history[-(MAX_HISTORY * 2):]
        _conversations[conversation_id] = history

        # ── UX: Enrich response with follow-ups and tips ──
        enriched = enrich_response(
            response_text=result["response"],
            user_message=req.message,
            agent_used=result["agent_used"],
            conversation_id=conversation_id,
            tool_calls_made=result["tool_calls_made"],
        )

        return ChatResponse(
            response=enriched,
            agent_used=result["agent_used"],
            conversation_id=conversation_id,
            tool_calls_made=result["tool_calls_made"],
            suggested_actions=get_suggested_actions(enriched, req.message),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat endpoint error: {e}")
        logger.error(traceback.format_exc())
        return ChatResponse(
            response=f"Sorry, an error occurred while processing your request: {type(e).__name__}: {str(e)[:300]}",
            agent_used="error",
            conversation_id=conversation_id,
            tool_calls_made=0,
            suggested_actions=STANDARD_ACTIONS,
        )


@app.post("/analyze")
def analyze(req: AnalyzeRequest, x_api_key: str | None = Header(None)):
    _verify_api_key(x_api_key)

    logger.info(f"Direct analyze: {req.jira_key}")

    # Fast path — directly call build_analysis_bundle, no LLM
    from tool_registry import get_tool_callable
    func = get_tool_callable("get_analysis_bundle")
    result = func(req.jira_key)

    if isinstance(result, str):
        result = json.loads(result)

    return {"jira_key": req.jira_key, "analysis": result}


# ── Static report serving (so reports are accessible via the dev tunnel) ──
import os
import hashlib
from fastapi.responses import FileResponse

_MARKDOWN_ANALYSIS_ROOT = r"C:\Users\rchakraborty\AppData\Roaming\Code\User\prompts\agents\Markdown_Analysis"
_REPORT_DIRS = [
    os.path.join(_MARKDOWN_ANALYSIS_ROOT, "code-reviews"),
    os.path.join(_MARKDOWN_ANALYSIS_ROOT, "build-failure-reports"),
    os.path.join(_MARKDOWN_ANALYSIS_ROOT, "repo-reports"),
    _MARKDOWN_ANALYSIS_ROOT,
]

# Report tokens: short-lived HMAC tokens so report URLs work in browser without API key.
# Token = HMAC-SHA256(API_KEY, filename)[:16] — valid as long as the API key stays the same.
# Not time-limited (reports are internal dev artifacts, not public), but unguessable without the key.

import hmac as _hmac

def _generate_report_token(filename: str) -> str:
    """Generate an HMAC-SHA256 token for a report filename."""
    return _hmac.new(
        API_KEY.encode("utf-8"),
        filename.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:16]

def _verify_report_token(filename: str, token: str) -> bool:
    """Verify an HMAC token for a report filename using a constant-time compare."""
    expected = _generate_report_token(filename)
    return _hmac.compare_digest(token, expected)

def get_report_url(report_path: str) -> str:
    """Build a public report URL with an auth token baked in."""
    filename = os.path.basename(report_path)
    token = _generate_report_token(filename)
    return f"{PUBLIC_BASE_URL}/reports/{filename}?token={token}"


@app.get("/reports/{filename}")
async def serve_report(filename: str, token: str = ""):
    """Serve a generated HTML report. Requires a valid token query param."""
    safe_name = os.path.basename(filename)
    if not safe_name.endswith(".html") or safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not token or not _verify_report_token(safe_name, token):
        raise HTTPException(status_code=403, detail="Invalid or missing report token")
    for report_dir in _REPORT_DIRS:
        filepath = os.path.join(report_dir, safe_name)
        if os.path.isfile(filepath):
            return FileResponse(filepath, media_type="text/html")
    raise HTTPException(status_code=404, detail="Report not found")


@app.get("/status")
def status():
    """Health check — tests connectivity to TFS, Jira, GitHub."""
    checks = {}

    # TFS
    try:
        from TFSMCP import tfs_get, TFS_URL
        tfs_get(f"{TFS_URL}/tfvc/changesets?$top=1")
        checks["tfs"] = "ok"
    except Exception as e:
        checks["tfs"] = f"error: {str(e)[:100]}"

    # Jira
    try:
        from TFSMCP import jira_get, JIRA_EMAIL
        if not JIRA_EMAIL:
            raise ValueError("JIRA_EMAIL env var not set")
        jira_get("myself")
        checks["jira"] = "ok"
    except Exception as e:
        checks["jira"] = f"error: {str(e)[:100]}"

    # GitHub
    try:
        from TFSMCP import github_get_json
        github_get_json("user")
        checks["github"] = "ok"
    except Exception as e:
        checks["github"] = f"error: {str(e)[:100]}"

    all_ok = all(v == "ok" for v in checks.values())
    return {"status": "healthy" if all_ok else "degraded", "services": checks}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev only)")
    args = parser.parse_args()
    uvicorn.run("app:app", host=API_HOST, port=API_PORT, reload=args.reload)
