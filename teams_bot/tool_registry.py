"""
Tool registry — auto-generates OpenAI function-calling schemas from TFSMCP.py.

Imports every public function from TFS_MCP_Server.py (which wraps TFSMCP.py),
inspects signatures and docstrings, and produces tool definitions compatible
with the OpenAI chat completions API.
"""

import inspect
import json
import logging
import sys
from typing import get_type_hints

logger = logging.getLogger("agentone.tools")

# Make TFSMCP importable
sys.path.insert(0, "C:\\TFS_MCP")

# Import the MCP tool functions — these already wrap TFSMCP.py and return JSON strings.
# We import them and strip the @mcp.tool() decorator layer by calling them directly.
from TFSMCP import (
    # Analysis
    build_analysis_bundle,
    # Code Review (async pipeline)
    start_code_review,
    get_code_review_status,
    # Jira
    fetch_jira_context,
    extract_changeset_ids_from_jira,
    jira_search,
    # TFS Changesets
    fetch_changeset_with_diffs,
    fetch_changeset_summary,
    # TFS Shelvesets
    search_shelvesets,
    find_shelvesets_by_jira_key,
    fetch_shelveset_with_diffs,
    fetch_shelveset_summary,
    post_shelveset_comment,
    get_shelveset_discussion_threads,
    # Branches
    resolve_branch_path,
    compute_branch_file_diff,
    get_branch_file_content,
    list_branch_files as _list_branch_files_raw,
    get_branch_changesets,
    ONBASE_BRANCHES,
    DEFAULT_TARGET_BRANCH,
    # Builds / Pipelines
    list_pipeline_definitions,
    list_builds,
    get_build_detail,
    get_build_failure_summary,
    get_build_log,
    get_build_logs_list,
    get_build_changes,
    search_builds_by_jira_key,
    PIPELINE_FOLDERS,
    # BuildDirector
    bd_list_versions,
    bd_list_builds,
    bd_get_latest_build,
    bd_search_build,
    bd_get_botw_builds,
    bd_get_build_calendar,
    BUILDDIRECTOR_VERSION_MAP,
    # Jira ↔ Build correlation
    jira_get_fixed_in_build,
    jira_cards_in_build,
    # GitHub
    fetch_github_repo,
    list_github_pulls,
    fetch_github_pr_with_diffs,
    fetch_github_pr_reviews,
    fetch_github_pr_review_comments,
    fetch_github_file_content,
    search_github_prs,
    list_github_org_repos,
    search_github_repos,
    list_code_scanning_alerts,
    list_dependabot_alerts,
    resolve_github_repo,
    # GitHub write
    create_github_branch,
    create_or_update_github_file,
    push_github_files,
    create_github_pull_request,
    # GHAS Fix + PR (async pipeline)
    start_ghas_fix_pr,
    get_ghas_fix_pr_status,
    # Build Failure Analysis (async pipeline)
    start_build_failure_analysis,
    get_build_failure_analysis_status,
    get_build_timeline,
    GITHUB_ORG,
    GITHUB_REPO_ALIASES,
    # ProGet
    proget_search_packages,
    # Jenkins
    jenkins_get_run,
    jenkins_get_failure_log,
    jenkins_analyze_failure,
)

# ── Python type → JSON Schema type mapping ──
_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
    type(None): "null",
}


def _python_type_to_json(py_type) -> str:
    origin = getattr(py_type, "__origin__", None)
    if origin is list:
        return "array"
    return _TYPE_MAP.get(py_type, "string")


# ── Tool definitions ──
# Each entry: (name, callable, description, parameters_override)
# parameters_override is optional — when provided it replaces auto-generated params.

TOOL_DEFINITIONS: list[dict] = []
_TOOL_CALLABLES: dict[str, callable] = {}


def _register(name: str, func: callable, description: str, params: dict | None = None):
    """Register a tool with its callable and OpenAI-compatible schema."""
    _TOOL_CALLABLES[name] = func

    if params is None:
        # Auto-generate from signature
        sig = inspect.signature(func)
        properties = {}
        required = []
        for pname, param in sig.parameters.items():
            ptype = param.annotation if param.annotation != inspect.Parameter.empty else str
            prop = {"type": _python_type_to_json(ptype)}
            if param.default != inspect.Parameter.empty:
                prop["default"] = param.default
            else:
                required.append(pname)
            properties[pname] = prop
        params = {"type": "object", "properties": properties}
        if required:
            params["required"] = required

    TOOL_DEFINITIONS.append({
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": params,
        },
    })


def get_tool_callable(name: str) -> callable:
    return _TOOL_CALLABLES[name]


def get_tools_for_agent(agent_name: str) -> list[dict]:
    """Return the subset of tool definitions for a specific agent."""
    allowed = AGENT_TOOL_MAP.get(agent_name, set())
    if not allowed:
        return TOOL_DEFINITIONS  # fallback: all tools
    return [t for t in TOOL_DEFINITIONS if t["function"]["name"] in allowed]


# ── Dynamic tool selection (keyword-based) ──
# Reduces tool schema token cost from ~3700 to ~800 by sending only relevant tools.
# This is CRITICAL for GitHub Models free tier (8000 input token limit).

TOOL_GROUPS = {
    "core_jira": {"get_jira_context", "get_jira_changeset_ids", "search_jira"},
    "changeset": {"get_changeset_summary"},            # Fast: metadata + file list only
    "changeset_diffs": {"get_changeset_diffs"},        # Slow: full diffs — only when explicitly needed
    "shelveset": {"find_shelvesets", "get_shelveset_summary", "add_shelveset_comment", "get_shelveset_comments"},
    "shelveset_diffs": {"get_shelveset_diffs"},      # Slow: full diffs — only when explicitly needed
    "branch": {"get_branch_file", "get_branch_diff", "list_branch_files", "get_branch_history", "list_onbase_branches"},
    "build_tfs": {"get_pipelines", "get_builds", "get_build_info", "get_build_failures",
                  "get_build_associated_changes", "search_builds_by_jira", "list_pipeline_folder_aliases"},
    "build_director": {"bd_list_versions", "bd_list_builds", "bd_get_latest_build", "bd_search_build",
                       "bd_version_aliases", "bd_get_botw_builds", "bd_get_build_calendar"},
    "jira_build": {"jira_fixed_in_build", "jira_cards_in_build"},
    "github_core": {"github_get_repo", "github_list_pulls", "github_get_pr_with_diffs",
                    "github_get_file", "github_search_prs"},
    "github_review": {"github_get_pr_reviews", "github_get_pr_comments"},
    "github_org": {"github_list_org_repos", "github_search_repos", "github_repo_aliases",
                   "github_code_scanning_alerts", "github_dependabot_alerts"},
    "github_write": {"github_create_branch", "github_create_or_update_file",
                     "github_push_files", "github_create_pr"},
    "ghas_fix": {"start_ghas_fix_pr", "get_ghas_fix_pr_status"},
    "analysis": {"get_analysis_bundle"},
    "code_review": {"start_code_review", "get_code_review_status"},
    "build_failure_analysis": {"start_build_failure_analysis", "get_build_failure_analysis_status",
                               "get_build_timeline", "get_build_log_content"},
    "proget": {"proget_search_packages"},
    "jenkins": {"jenkins_get_run", "jenkins_get_failure_log", "jenkins_analyze_failure"},
}

# Map keywords in user query → which tool groups to include
_KEYWORD_GROUPS = {
    # Jira-related (uses fast changeset summary, not full diffs)
    "sbpwc": ["core_jira", "changeset"],
    "csfmd": ["core_jira", "changeset"],
    "csfp": ["core_jira", "changeset"],
    "nile": ["core_jira", "changeset"],
    "jira": ["core_jira"],
    "card": ["core_jira"],
    # Changeset — fast summary by default
    "changeset": ["core_jira", "changeset"],
    "code change": ["core_jira", "changeset"],
    # Full diffs — only when user explicitly requests line-by-line
    "diff": ["changeset", "changeset_diffs", "shelveset_diffs", "branch"],
    "line by line": ["changeset_diffs", "shelveset_diffs"],
    "full diff": ["changeset_diffs", "shelveset_diffs"],
    "unified diff": ["changeset_diffs", "shelveset_diffs"],
    # Shelveset
    "shelveset": ["core_jira", "shelveset"],
    "shelve": ["core_jira", "shelveset"],
    # Builds
    "botw": ["build_director"],
    "build of the week": ["build_director"],
    "latest build": ["build_director"],
    "build calendar": ["build_director"],
    "build schedule": ["build_director"],
    "build": ["build_director", "jira_build"],
    "pipeline": ["build_tfs", "build_failure_analysis"],
    "fixed in": ["jira_build", "core_jira"],
    "cards in build": ["jira_build"],
    "failure": ["build_tfs", "build_failure_analysis"],
    "failed": ["build_tfs", "build_failure_analysis"],
    "root cause": ["build_failure_analysis", "build_tfs"],
    "analyze failure": ["build_failure_analysis"],
    "analyze build": ["build_failure_analysis", "build_tfs"],
    "build failure": ["build_failure_analysis", "build_tfs"],
    "pipeline failure": ["build_failure_analysis", "build_tfs"],
    "failure analysis": ["build_failure_analysis"],
    "failure analysis status": ["build_failure_analysis"],
    "build log": ["build_failure_analysis", "build_tfs"],
    "build timeline": ["build_failure_analysis", "build_tfs"],
    # Branch
    "branch": ["branch", "github_write"],
    "file content": ["branch"],
    # GitHub
    "github": ["github_core", "github_write"],
    "pull request": ["github_core", "github_review", "github_write"],
    "pr review": ["github_core", "github_review"],
    "pr": ["github_core", "github_write"],
    "create branch": ["github_write", "github_core"],
    "create pr": ["github_write", "github_core"],
    "push file": ["github_write", "github_core"],
    "submit pr": ["github_write", "github_core"],
    "make changes": ["github_write", "github_core"],
    "repo": ["github_core", "github_org"],
    "dependabot": ["github_org"],
    "scanning": ["github_org", "ghas_fix"],
    "ghas": ["github_org", "ghas_fix"],
    "fix alert": ["ghas_fix", "github_write"],
    "fix security": ["ghas_fix", "github_write"],
    "fix vulnerability": ["ghas_fix", "github_write"],
    # ProGet
    "proget": ["proget"],
    "jenkins": ["jenkins"],
    "jenkins pipeline": ["jenkins"],
    "jenkins failure": ["jenkins"],
    "csp.jenkins": ["jenkins"],
    "jenkins.hylandqa": ["jenkins"],
    "package": ["proget"],
    "nuget": ["proget"],
    "cefsharp": ["proget"],
    "infragistics": ["proget"],
    # Complex queries
    "analyze": ["core_jira", "changeset", "analysis"],
    "review": ["core_jira", "changeset", "shelveset", "code_review"],
    "code review": ["code_review", "core_jira"],
    "review status": ["code_review"],
    "code review status": ["code_review"],
    "is the review done": ["code_review"],
    "html report": ["code_review", "core_jira"],
    "explain": ["core_jira", "changeset"],
    "fetch": ["core_jira", "changeset"],
}

# Maximum tools to send even after filtering
MAX_TOOLS_PER_QUERY = 20


def get_tools_for_query(agent_name: str, user_message: str) -> list[dict]:
    """Dynamically select tools based on user query keywords.

    Instead of sending all 42 tools (~3700 tokens), this sends only 5-15
    relevant tools (~400-1300 tokens), freeing up ~2500 tokens for content.
    """
    allowed_by_agent = AGENT_TOOL_MAP.get(agent_name, set())
    if not allowed_by_agent:
        allowed_by_agent = {t["function"]["name"] for t in TOOL_DEFINITIONS}

    msg_lower = user_message.lower()
    selected_groups: set[str] = set()

    for keyword, groups in _KEYWORD_GROUPS.items():
        if keyword in msg_lower:
            selected_groups.update(groups)

    # If no keywords matched, include core_jira + changeset as default
    if not selected_groups:
        selected_groups = {"core_jira", "changeset", "shelveset", "build_director"}

    # Collect tool names from selected groups
    selected_tools: set[str] = set()
    for group in selected_groups:
        selected_tools.update(TOOL_GROUPS.get(group, set()))

    # Intersect with agent's allowed tools
    selected_tools &= allowed_by_agent

    # Build filtered list
    filtered = [t for t in TOOL_DEFINITIONS if t["function"]["name"] in selected_tools]

    # Safety cap — prioritize tools from the most specifically-matched groups first
    if len(filtered) > MAX_TOOLS_PER_QUERY:
        # Score each tool: tools in more specific (fewer-member) groups get higher priority
        tool_priority: dict[str, int] = {}
        for group in selected_groups:
            group_tools = TOOL_GROUPS.get(group, set())
            for tn in group_tools:
                if tn in selected_tools:
                    # Smaller group = more specific = higher priority (lower score)
                    tool_priority[tn] = min(tool_priority.get(tn, 999), len(group_tools))
        filtered.sort(key=lambda t: tool_priority.get(t["function"]["name"], 999))
        filtered = filtered[:MAX_TOOLS_PER_QUERY]

    logger.info(f"Dynamic tool selection: {len(filtered)} tools for query "
                f"(groups: {sorted(selected_groups)}, agent: {agent_name})")
    return filtered


# ──────────────────────────────────────────────
# REGISTER ALL TOOLS
# ──────────────────────────────────────────────

# --- Analysis ---
_register("get_analysis_bundle", build_analysis_bundle,
          "Get a full analysis bundle for a Jira key: Jira context + all linked TFS changeset code diffs.",
          {"type": "object", "properties": {"jira_key": {"type": "string"}}, "required": ["jira_key"]})

# --- Jira ---
_register("get_jira_context", fetch_jira_context,
          "Get structured Jira issue context: summary, description, repro steps, testing recommendations, linked issues, and parent epic.",
          {"type": "object", "properties": {"jira_key": {"type": "string"}}, "required": ["jira_key"]})

_register("get_jira_changeset_ids", extract_changeset_ids_from_jira,
          "Extract TFS changeset IDs linked to a Jira issue via remote links.",
          {"type": "object", "properties": {"jira_key": {"type": "string"}}, "required": ["jira_key"]})

_register("search_jira", jira_search,
          "Run a JQL search against Jira. Returns structured issue data.",
          {"type": "object", "properties": {"jql": {"type": "string"}, "max_results": {"type": "integer", "default": 50}}, "required": ["jql"]})

# --- TFS Changesets ---
_register("get_changeset_summary", fetch_changeset_summary,
          "Get changeset metadata (author, date, comment, list of changed files) WITHOUT code diffs. Fast — use this first. Returns file paths and change types but no diff content.",
          {"type": "object", "properties": {"changeset_id": {"type": "integer"}}, "required": ["changeset_id"]})

_register("get_changeset_diffs", fetch_changeset_with_diffs,
          "Get FULL code diffs for a TFS changeset including unified diffs for every changed file. SLOW (20-30s) — only use if the user explicitly needs to see line-by-line code changes. Prefer get_changeset_summary for most queries.",
          {"type": "object", "properties": {"changeset_id": {"type": "integer"}}, "required": ["changeset_id"]})

# --- TFS Shelvesets ---
def _find_shelvesets_wrapper(name: str = "", owner: str = "", jira_key: str = "", top: int = 50):
    if jira_key:
        return find_shelvesets_by_jira_key(jira_key)
    return search_shelvesets(name=name or None, owner=owner or None, top=top)

_register("find_shelvesets", _find_shelvesets_wrapper,
          "Search TFS shelvesets by name, owner, or Jira key.",
          {"type": "object", "properties": {
              "name": {"type": "string", "default": ""},
              "owner": {"type": "string", "default": ""},
              "jira_key": {"type": "string", "default": ""},
              "top": {"type": "integer", "default": 50},
          }})

_register("get_shelveset_summary", fetch_shelveset_summary,
          "Get shelveset metadata (owner, date, comment, work items, list of changed files) WITHOUT code diffs. Fast — use this first.",
          {"type": "object", "properties": {
              "shelveset_name": {"type": "string"},
              "owner": {"type": "string"},
          }, "required": ["shelveset_name", "owner"]})

_register("get_shelveset_diffs", fetch_shelveset_with_diffs,
          "Get FULL code diffs for a TFS shelveset including unified diffs for every changed file. SLOW — only use if user explicitly needs line-by-line code changes. Prefer get_shelveset_summary for most queries.",
          {"type": "object", "properties": {
              "shelveset_name": {"type": "string"},
              "owner": {"type": "string"},
          }, "required": ["shelveset_name", "owner"]})

_register("add_shelveset_comment", post_shelveset_comment,
          "Post a discussion comment on a TFS shelveset.",
          {"type": "object", "properties": {
              "shelveset_name": {"type": "string"},
              "owner": {"type": "string"},
              "comment_text": {"type": "string"},
          }, "required": ["shelveset_name", "owner", "comment_text"]})

_register("get_shelveset_comments", get_shelveset_discussion_threads,
          "Get all discussion threads and comments on a TFS shelveset.",
          {"type": "object", "properties": {
              "shelveset_name": {"type": "string"},
              "owner": {"type": "string"},
          }, "required": ["shelveset_name", "owner"]})

# --- Branches ---
def _get_branch_file(relative_path: str, version: str = "DEV", start_line: int = 0, end_line: int = 0):
    branch_root = resolve_branch_path(version)
    content = get_branch_file_content(branch_root, relative_path)
    if content is None:
        return {"error": f"File not found: {relative_path} in branch {version}"}
    lines = content.splitlines()
    total = len(lines)
    sl = max(1, start_line) if start_line > 0 else 1
    el = min(total, end_line) if end_line > 0 else total
    return {"version": version, "relative_path": relative_path, "total_lines": total,
            "start_line": sl, "end_line": el, "content": "\n".join(lines[sl - 1:el])}

_register("get_branch_file", _get_branch_file,
          "Get file content from an OnBase version branch. Supports optional line range.",
          {"type": "object", "properties": {
              "relative_path": {"type": "string"},
              "version": {"type": "string", "default": "DEV"},
              "start_line": {"type": "integer", "default": 0},
              "end_line": {"type": "integer", "default": 0},
          }, "required": ["relative_path"]})

def _get_branch_diff(relative_path: str, source_version: str, target_version: str = "DEV", max_lines: int = 500):
    src = resolve_branch_path(source_version)
    tgt = resolve_branch_path(target_version)
    result = compute_branch_file_diff(relative_path, src, tgt, max_lines=max_lines)
    result["source_version"] = source_version
    result["target_version"] = target_version
    return result

_register("get_branch_diff", _get_branch_diff,
          "Diff a file between two OnBase version branches (e.g. 24.1 vs DEV).",
          {"type": "object", "properties": {
              "relative_path": {"type": "string"},
              "source_version": {"type": "string"},
              "target_version": {"type": "string", "default": "DEV"},
              "max_lines": {"type": "integer", "default": 500},
          }, "required": ["relative_path", "source_version"]})

def _list_branch_files(relative_path: str, version: str = "DEV", recursion: str = "Full"):
    branch_root = resolve_branch_path(version)
    full_path = f"{branch_root}/{relative_path}"
    files = _list_branch_files_raw(full_path, recursion)
    return {"version": version, "folder": relative_path, "files": files, "total_files": len(files)}

_register("list_branch_files", _list_branch_files,
          "List files under a folder path within an OnBase version branch.",
          {"type": "object", "properties": {
              "relative_path": {"type": "string"},
              "version": {"type": "string", "default": "DEV"},
              "recursion": {"type": "string", "default": "Full"},
          }, "required": ["relative_path"]})

def _get_branch_history(relative_path: str, version: str = "DEV", from_date: str = "", to_date: str = "", top: int = 100):
    branch_root = resolve_branch_path(version)
    full_path = f"{branch_root}/{relative_path}"
    changesets = get_branch_changesets(full_path, from_date=from_date or None, to_date=to_date or None, top=top)
    return {"version": version, "path": relative_path, "changesets": changesets, "total": len(changesets)}

_register("get_branch_history", _get_branch_history,
          "Get changeset history for a file/folder within an OnBase version branch.",
          {"type": "object", "properties": {
              "relative_path": {"type": "string"},
              "version": {"type": "string", "default": "DEV"},
              "from_date": {"type": "string", "default": ""},
              "to_date": {"type": "string", "default": ""},
              "top": {"type": "integer", "default": 100},
          }, "required": ["relative_path"]})

_register("list_onbase_branches", lambda: {"branches": dict(sorted(ONBASE_BRANCHES.items())), "default_target": DEFAULT_TARGET_BRANCH},
          "List all known OnBase version branches with their TFS paths.",
          {"type": "object", "properties": {}})

# --- Builds / Pipelines ---
_register("list_pipeline_folder_aliases", lambda: {"folders": PIPELINE_FOLDERS},
          "List well-known pipeline folder aliases and their TFS paths.",
          {"type": "object", "properties": {}})

def _get_pipelines(folder: str = "", folder_alias: str = "", version: str = "", name_filter: str = "", top: int = 100):
    effective_folder = folder or None
    if folder_alias:
        effective_folder = PIPELINE_FOLDERS.get(folder_alias.lower())
        if not effective_folder:
            return {"error": f"Unknown alias '{folder_alias}'. Valid: {list(PIPELINE_FOLDERS.keys())}"}
    if version and effective_folder:
        v = version.strip().upper() if version.strip().upper() == "DEV" else version.strip()
        effective_folder = f"{effective_folder}\\{v}"
    defs = list_pipeline_definitions(folder=effective_folder, name_filter=name_filter or None, top=top)
    return {"folder": effective_folder, "definitions": defs, "total": len(defs)}

_register("get_pipelines", _get_pipelines,
          "List build pipeline definitions. Filter by folder path, alias, or name.",
          {"type": "object", "properties": {
              "folder": {"type": "string", "default": ""},
              "folder_alias": {"type": "string", "default": ""},
              "version": {"type": "string", "default": ""},
              "name_filter": {"type": "string", "default": ""},
              "top": {"type": "integer", "default": 100},
          }})

def _get_builds(definition_id: int = 0, definition_name: str = "", status_filter: str = "", result_filter: str = "", top: int = 20):
    builds_list = list_builds(
        definition_id=definition_id or None, definition_name=definition_name or None,
        status_filter=status_filter or None, result_filter=result_filter or None, top=top)
    return {"builds": builds_list, "total": len(builds_list)}

_register("get_builds", _get_builds,
          "List builds with filters. Status: inProgress/completed/all. Result: succeeded/failed/canceled.",
          {"type": "object", "properties": {
              "definition_id": {"type": "integer", "default": 0},
              "definition_name": {"type": "string", "default": ""},
              "status_filter": {"type": "string", "default": ""},
              "result_filter": {"type": "string", "default": ""},
              "top": {"type": "integer", "default": 20},
          }})

_register("get_build_info", get_build_detail,
          "Get detailed information about a specific build by ID.",
          {"type": "object", "properties": {"build_id": {"type": "integer"}}, "required": ["build_id"]})

_register("get_build_failures", get_build_failure_summary,
          "Get failure summary for a build: failed tasks, error messages, and optionally log tails.",
          {"type": "object", "properties": {
              "build_id": {"type": "integer"},
              "include_logs": {"type": "boolean", "default": False},
              "max_log_lines": {"type": "integer", "default": 200},
          }, "required": ["build_id"]})

_register("get_build_associated_changes", get_build_changes,
          "Get the changesets/commits associated with a build.",
          {"type": "object", "properties": {"build_id": {"type": "integer"}}, "required": ["build_id"]})

def _search_builds_by_jira(jira_key: str, definition_id: int = 0, folder: str = "", folder_alias: str = "", top: int = 20):
    effective_folder = folder or None
    if folder_alias:
        effective_folder = PIPELINE_FOLDERS.get(folder_alias.lower())
    builds_list = search_builds_by_jira_key(jira_key, definition_id=definition_id or None, folder=effective_folder)
    return {"jira_key": jira_key, "builds": builds_list, "total": len(builds_list)}

_register("search_builds_by_jira", _search_builds_by_jira,
          "Search for builds that reference a Jira key in their changeset comments.",
          {"type": "object", "properties": {
              "jira_key": {"type": "string"},
              "definition_id": {"type": "integer", "default": 0},
              "folder": {"type": "string", "default": ""},
              "folder_alias": {"type": "string", "default": ""},
              "top": {"type": "integer", "default": 20},
          }, "required": ["jira_key"]})

# --- BuildDirector ---
_register("bd_list_versions", bd_list_versions,
          "List all OnBase versions available in BuildDirector with their latest service pack.",
          {"type": "object", "properties": {}})

def _bd_list_builds(version: str, top: int = 10, successful_only: bool = False):
    return bd_list_builds(version, top=top, successful_only=successful_only)

_register("bd_list_builds", _bd_list_builds,
          "List builds for an OnBase version from BuildDirector. Accepts versions like '24.1', '25.2', 'DEV', or full build number '24.1.38.1000'. Returns most recent first.",
          {"type": "object", "properties": {
              "version": {"type": "string", "description": "OnBase version (e.g. '24.1', '25.2', 'DEV', '26.1')"},
              "top": {"type": "integer", "default": 10},
              "successful_only": {"type": "boolean", "default": False},
          }, "required": ["version"]})

_register("bd_get_latest_build", bd_get_latest_build,
          "Get the latest successful build for an OnBase version from BuildDirector. Use this to answer 'what is the latest build for OnBase X.Y'.",
          {"type": "object", "properties": {
              "version": {"type": "string", "description": "OnBase version (e.g. '24.1', '25.2', 'DEV')"},
          }, "required": ["version"]})

def _bd_search_build(build_number: str):
    return bd_search_build(build_number)

_register("bd_search_build", _bd_search_build,
          "Search for a specific OnBase build by full version number (e.g. '24.1.38.1000') in BuildDirector.",
          {"type": "object", "properties": {
              "build_number": {"type": "string", "description": "Full 4-part build number like '24.1.38.1000'"},
          }, "required": ["build_number"]})

_register("bd_version_aliases", lambda: {"aliases": BUILDDIRECTOR_VERSION_MAP},
          "List BuildDirector version aliases (maps user-friendly names like 'DEV' to BuildDirector version keys).",
          {"type": "object", "properties": {}})

def _bd_get_botw_builds(version: str, top: int = 5):
    return bd_get_botw_builds(version, top=top)

_register("bd_get_botw_builds", _bd_get_botw_builds,
          "Get the most recent Build Of The Week (BOTW) builds for an OnBase version. BOTW builds have build_number=1000. Use to answer 'last BOTW for 25.2' or 'recent BOTWs for DEV'.",
          {"type": "object", "properties": {
              "version": {"type": "string", "description": "OnBase version (e.g. '24.1', '25.2', 'DEV')"},
              "top": {"type": "integer", "default": 5, "description": "Max number of BOTW builds to return"},
          }, "required": ["version"]})

_register("bd_get_build_calendar", bd_get_build_calendar,
          "Get the build schedule/calendar for an OnBase version. Returns BOTH the current BOTW cycle (most recent BOTW + its publish date, even if BOTW generation is in the past) AND the next future BOTW cycle. Use to answer 'when is the BOTW published' or 'next publish date for 24.1'. The schedule_summary contains current_botw_cycle and next_botw_cycle.",
          {"type": "object", "properties": {
              "version": {"type": "string", "description": "OnBase version (e.g. '24.1', '25.2', 'DEV', '25.1')"},
          }, "required": ["version"]})

_register("jira_fixed_in_build", jira_get_fixed_in_build,
          "Get the 'Fixed in Build' field for a Jira issue. Returns which build(s) a Jira card was fixed in. Use to answer 'SBPWC-12657 is fixed in which build'.",
          {"type": "object", "properties": {
              "jira_key": {"type": "string", "description": "Jira issue key (e.g. 'SBPWC-12657')"},
          }, "required": ["jira_key"]})

def _jira_cards_in_build(build_number: str, max_results: int = 50):
    return jira_cards_in_build(build_number, max_results=max_results)

_register("jira_cards_in_build", _jira_cards_in_build,
          "Find all Jira cards fixed in a specific build. Searches the 'Fixed In Build' field. Use to answer '26.1.0.439 includes which Jira cards'.",
          {"type": "object", "properties": {
              "build_number": {"type": "string", "description": "Full or partial build number (e.g. '26.1.0.439')"},
              "max_results": {"type": "integer", "default": 50},
          }, "required": ["build_number"]})

# --- Code Review (async pipeline) ---
_register("start_code_review", start_code_review,
          "Start a code review for a Jira card IN THE BACKGROUND. Returns immediately (~1s). "
          "The review (Jira fetch, TFS diffs, LLM analysis, HTML report) runs asynchronously and "
          "takes ~45-60 seconds. Returns progress phase and percentage. After calling this, tell "
          "the user to ask for the code review status in about a minute. Report is saved locally. "
          "Use for 'code review', 'review code', or 'review SBPWC-XXXXX'.",
          {"type": "object", "properties": {
              "jira_key": {"type": "string", "description": "Jira issue key (e.g. 'SBPWC-12770')"},
          }, "required": ["jira_key"]})

_register("get_code_review_status", get_code_review_status,
          "Check the status of a background code review. Returns progress info (phase, percentage) "
          "if still running, or full results (summary, verdict, findings, local report path) when "
          "done. Status is disk-persisted and survives server restarts. Use when user asks "
          "'code review status', 'is the review done', or 'check review for SBPWC-XXXXX'.",
          {"type": "object", "properties": {
              "jira_key": {"type": "string", "description": "Jira issue key (e.g. 'SBPWC-12770')"},
          }, "required": ["jira_key"]})

# --- Build Failure Analysis (async pipeline) ---
_register("start_build_failure_analysis", start_build_failure_analysis,
          "Start a build failure analysis IN THE BACKGROUND. Returns immediately (~1s). "
          "Analyzes a failed TFS build: fetches build details, failed task logs, associated "
          "changesets, optionally Jira context, runs AI root cause analysis, and generates "
          "an HTML report. Takes ~30-45 seconds. Returns progress phase and percentage. "
          "After calling this, tell the user to ask for the build failure analysis status "
          "in about 30 seconds. Use for 'analyze build failure', 'why did build X fail', "
          "'pipeline failure analysis', or 'root cause for build 812151'.",
          {"type": "object", "properties": {
              "build_id": {"type": "integer", "description": "TFS build ID to analyze"},
              "jira_key": {"type": "string", "default": "", "description": "Optional Jira key for context (e.g. OBAPISERV-441)"},
          }, "required": ["build_id"]})

_register("get_build_failure_analysis_status", get_build_failure_analysis_status,
          "Check the status of a background build failure analysis. Returns progress info "
          "if still running, or full results (root cause, severity, report path) when done. "
          "Status is disk-persisted and survives server restarts. Use when user asks "
          "'build failure analysis status', 'is the analysis done', or 'check build X'.",
          {"type": "object", "properties": {
              "build_id": {"type": "integer", "description": "TFS build ID"},
          }, "required": ["build_id"]})

_register("get_build_timeline", get_build_timeline,
          "Get build timeline showing each phase, job, and task with results. "
          "Use to understand which step failed and the execution order.",
          {"type": "object", "properties": {
              "build_id": {"type": "integer"},
          }, "required": ["build_id"]})

def _get_build_log_content(build_id: int, log_id: int, tail: int = 0):
    log_text = get_build_log(build_id, log_id)
    if tail > 0:
        lines = log_text.splitlines()
        if len(lines) > tail:
            return {"content": "\n".join(lines[-tail:]), "truncated": True, "total_lines": len(lines), "lines_shown": tail}
    return {"content": log_text, "truncated": False}

_register("get_build_log_content", _get_build_log_content,
          "Get the text content of a specific build log by log_id. "
          "Use tail parameter to get only the last N lines. "
          "Find log_ids from get_build_failures or get_build_timeline.",
          {"type": "object", "properties": {
              "build_id": {"type": "integer"},
              "log_id": {"type": "integer"},
              "tail": {"type": "integer", "default": 0, "description": "Return only last N lines (0=all)"},
          }, "required": ["build_id", "log_id"]})

# --- GitHub ---
_register("github_repo_aliases", lambda: {"org": GITHUB_ORG, "aliases": GITHUB_REPO_ALIASES},
          "List well-known GitHub repo aliases within the HylandFoundation org.",
          {"type": "object", "properties": {}})

def _github_get_repo(owner: str = "", repo: str = ""):
    return fetch_github_repo(owner or GITHUB_ORG, repo)

_register("github_get_repo", _github_get_repo,
          "Get GitHub repository metadata.",
          {"type": "object", "properties": {
              "owner": {"type": "string", "default": ""},
              "repo": {"type": "string"},
          }, "required": ["repo"]})

def _github_list_pulls(owner: str = "", repo: str = "", state: str = "open", sort: str = "updated", direction: str = "desc", per_page: int = 30):
    return list_github_pulls(owner or GITHUB_ORG, repo, state, sort, direction, per_page)

_register("github_list_pulls", _github_list_pulls,
          "List pull requests for a GitHub repo.",
          {"type": "object", "properties": {
              "owner": {"type": "string", "default": ""},
              "repo": {"type": "string"},
              "state": {"type": "string", "default": "open"},
              "sort": {"type": "string", "default": "updated"},
              "per_page": {"type": "integer", "default": 30},
          }, "required": ["repo"]})

def _github_get_pr_with_diffs(owner: str = "", repo: str = "", pr_number: int = 0):
    return fetch_github_pr_with_diffs(owner or GITHUB_ORG, repo, pr_number)

_register("github_get_pr_with_diffs", _github_get_pr_with_diffs,
          "Get full PR details including diffs for all changed files.",
          {"type": "object", "properties": {
              "owner": {"type": "string", "default": ""},
              "repo": {"type": "string"},
              "pr_number": {"type": "integer"},
          }, "required": ["repo", "pr_number"]})

def _github_get_pr_reviews(owner: str = "", repo: str = "", pr_number: int = 0):
    return fetch_github_pr_reviews(owner or GITHUB_ORG, repo, pr_number)

_register("github_get_pr_reviews", _github_get_pr_reviews,
          "Get all reviews on a GitHub PR.",
          {"type": "object", "properties": {
              "owner": {"type": "string", "default": ""},
              "repo": {"type": "string"},
              "pr_number": {"type": "integer"},
          }, "required": ["repo", "pr_number"]})

def _github_get_pr_comments(owner: str = "", repo: str = "", pr_number: int = 0):
    return fetch_github_pr_review_comments(owner or GITHUB_ORG, repo, pr_number)

_register("github_get_pr_comments", _github_get_pr_comments,
          "Get inline code-level review comments on a GitHub PR.",
          {"type": "object", "properties": {
              "owner": {"type": "string", "default": ""},
              "repo": {"type": "string"},
              "pr_number": {"type": "integer"},
          }, "required": ["repo", "pr_number"]})

def _github_get_file(owner: str = "", repo: str = "", path: str = "", ref: str = ""):
    return fetch_github_file_content(owner or GITHUB_ORG, repo, path, ref or None)

_register("github_get_file", _github_get_file,
          "Get file content from a GitHub repository.",
          {"type": "object", "properties": {
              "owner": {"type": "string", "default": ""},
              "repo": {"type": "string"},
              "path": {"type": "string"},
              "ref": {"type": "string", "default": ""},
          }, "required": ["repo", "path"]})

def _github_search_prs(owner: str = "", repo: str = "", query: str = "", per_page: int = 20):
    return search_github_prs(owner or GITHUB_ORG, repo, query, per_page)

_register("github_search_prs", _github_search_prs,
          "Search PRs in a GitHub repo by keyword.",
          {"type": "object", "properties": {
              "owner": {"type": "string", "default": ""},
              "repo": {"type": "string"},
              "query": {"type": "string"},
              "per_page": {"type": "integer", "default": 20},
          }, "required": ["repo", "query"]})

def _github_list_org_repos(org: str = "", sort: str = "updated", repo_type: str = "all", per_page: int = 30):
    return list_github_org_repos(org or None, per_page, sort, repo_type)

_register("github_list_org_repos", _github_list_org_repos,
          "List repositories in the GitHub org (default: HylandFoundation).",
          {"type": "object", "properties": {
              "org": {"type": "string", "default": ""},
              "sort": {"type": "string", "default": "updated"},
              "per_page": {"type": "integer", "default": 30},
          }})

def _github_search_repos(query: str, org: str = "", per_page: int = 20):
    return search_github_repos(query, org or None, per_page)

_register("github_search_repos", _github_search_repos,
          "Search repositories in the GitHub org by keyword.",
          {"type": "object", "properties": {
              "query": {"type": "string"},
              "org": {"type": "string", "default": ""},
              "per_page": {"type": "integer", "default": 20},
          }, "required": ["query"]})

def _github_code_scanning_alerts(owner: str = "", repo: str = "", state: str = "open", severity: str = "", per_page: int = 30):
    return list_code_scanning_alerts(owner or GITHUB_ORG, repo, state, severity or None, per_page)

_register("github_code_scanning_alerts", _github_code_scanning_alerts,
          "List CodeQL code scanning alerts for a GitHub repo.",
          {"type": "object", "properties": {
              "owner": {"type": "string", "default": ""},
              "repo": {"type": "string"},
              "state": {"type": "string", "default": "open"},
              "severity": {"type": "string", "default": ""},
              "per_page": {"type": "integer", "default": 30},
          }, "required": ["repo"]})

def _github_dependabot_alerts(owner: str = "", repo: str = "", state: str = "open", severity: str = "", per_page: int = 30):
    return list_dependabot_alerts(owner or GITHUB_ORG, repo, state, severity or None, per_page)

_register("github_dependabot_alerts", _github_dependabot_alerts,
          "List Dependabot vulnerability alerts for a GitHub repo.",
          {"type": "object", "properties": {
              "owner": {"type": "string", "default": ""},
              "repo": {"type": "string"},
              "state": {"type": "string", "default": "open"},
              "severity": {"type": "string", "default": ""},
              "per_page": {"type": "integer", "default": 30},
          }, "required": ["repo"]})


# --- GitHub Write ---

def _github_create_branch(owner: str = "", repo: str = "", branch: str = "", from_branch: str = ""):
    return create_github_branch(owner or GITHUB_ORG, repo, branch, from_branch)

_register("github_create_branch", _github_create_branch,
          "Create a new branch in a GitHub repo. Always create a branch before pushing changes or creating a PR.",
          {"type": "object", "properties": {
              "owner": {"type": "string", "default": "", "description": "Repo owner (org). Defaults to HylandFoundation."},
              "repo": {"type": "string", "description": "Repository name."},
              "branch": {"type": "string", "description": "New branch name (e.g. feature/my-fix)."},
              "from_branch": {"type": "string", "default": "", "description": "Source branch. Defaults to repo default branch."},
          }, "required": ["repo", "branch"]})

def _github_create_or_update_file(owner: str = "", repo: str = "", path: str = "", content: str = "", message: str = "", branch: str = "", sha: str = ""):
    return create_or_update_github_file(owner or GITHUB_ORG, repo, path, content, message, branch, sha)

_register("github_create_or_update_file", _github_create_or_update_file,
          "Create or update a single file in a GitHub repo on a specific branch.",
          {"type": "object", "properties": {
              "owner": {"type": "string", "default": ""},
              "repo": {"type": "string"},
              "path": {"type": "string", "description": "File path in repo (e.g. src/app/config.ts)."},
              "content": {"type": "string", "description": "File content (plain text)."},
              "message": {"type": "string", "description": "Commit message."},
              "branch": {"type": "string", "description": "Target branch."},
              "sha": {"type": "string", "default": "", "description": "File SHA (required for updates, omit for new files)."},
          }, "required": ["repo", "path", "content", "message", "branch"]})

def _github_push_files(owner: str = "", repo: str = "", branch: str = "", files: list = None, message: str = ""):
    return push_github_files(owner or GITHUB_ORG, repo, branch, files or [], message)

_register("github_push_files", _github_push_files,
          "Push multiple files in a single commit to a GitHub branch (atomic multi-file change).",
          {"type": "object", "properties": {
              "owner": {"type": "string", "default": ""},
              "repo": {"type": "string"},
              "branch": {"type": "string", "description": "Target branch."},
              "files": {"type": "array", "items": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}, "description": "List of files with path and content."},
              "message": {"type": "string", "description": "Commit message."},
          }, "required": ["repo", "branch", "files", "message"]})

def _github_create_pr(owner: str = "", repo: str = "", title: str = "", head: str = "", base: str = "", body: str = "", draft: bool = False):
    return create_github_pull_request(owner or GITHUB_ORG, repo, title, head, base, body, draft)

_register("github_create_pr", _github_create_pr,
          "Create a pull request in a GitHub repo. Always create a branch and push changes first.",
          {"type": "object", "properties": {
              "owner": {"type": "string", "default": ""},
              "repo": {"type": "string"},
              "title": {"type": "string", "description": "PR title."},
              "head": {"type": "string", "description": "Branch with changes."},
              "base": {"type": "string", "default": "", "description": "Target branch. Defaults to repo default."},
              "body": {"type": "string", "default": "", "description": "PR description (markdown)."},
              "draft": {"type": "boolean", "default": False, "description": "Create as draft PR."},
          }, "required": ["repo", "title", "head"]})

# --- GHAS Fix + PR (async pipeline) ---
_register("start_ghas_fix_pr", start_ghas_fix_pr,
          "Start a GHAS code scanning alert fix + PR creation IN THE BACKGROUND. Returns immediately (~1s). "
          "Fetches alert details, retrieves the affected file, generates an AI fix, creates a branch, "
          "pushes the fix, and opens a pull request. Takes ~20-30 seconds. "
          "Use for 'fix alert #747 and create PR', 'fix GHAS alert 12', or 'fix security alert and open PR'.",
          {"type": "object", "properties": {
              "owner": {"type": "string", "description": "Repository owner (org or user)."},
              "repo": {"type": "string", "description": "Repository name."},
              "alert_number": {"type": "integer", "description": "Code scanning alert number to fix."},
          }, "required": ["owner", "repo", "alert_number"]})

_register("get_ghas_fix_pr_status", get_ghas_fix_pr_status,
          "Check the status of a background GHAS fix + PR creation job. Returns progress info "
          "if still running, or full results (PR URL, branch, commit) when done. "
          "Use when user asks 'PR status for alert #747', 'is the fix done', or 'check PR status'.",
          {"type": "object", "properties": {
              "owner": {"type": "string", "description": "Repository owner."},
              "repo": {"type": "string", "description": "Repository name."},
              "alert_number": {"type": "integer", "description": "Code scanning alert number."},
          }, "required": ["owner", "repo", "alert_number"]})

# --- ProGet ---
_register("proget_search_packages", proget_search_packages,
          "Search ProGet for NuGet/npm packages by name. Returns package versions sorted by date with the latest/highest version identified.",
          {"type": "object", "properties": {
              "package_name": {"type": "string", "description": "Package name to search (e.g. 'cefsharp', 'Infragistics.WPF', '@hyland/ui')."},
              "feed": {"type": "string", "default": "", "description": "Optional feed filter: 'NuGet', 'TestFeed', or '' for all."},
              "top": {"type": "integer", "default": 10, "description": "Max results to return."},
          }, "required": ["package_name"]})

# --- Jenkins ---
_register("jenkins_get_run", jenkins_get_run,
          "Get Jenkins pipeline run details from a Blue Ocean or classic URL. "
          "Returns: run metadata (result, state, duration, commit), all stage results, and failed stages. "
          "Use when user pastes a Jenkins URL or asks about a Jenkins pipeline run.",
          {"type": "object", "properties": {
              "url": {"type": "string", "description": "Jenkins Blue Ocean or classic URL for a pipeline run."},
          }, "required": ["url"]})

_register("jenkins_get_failure_log", jenkins_get_failure_log,
          "Get detailed failure logs for a Jenkins pipeline run. "
          "Fetches logs for specific stage or auto-detects all failed stages. "
          "Returns: failure logs, failed steps with individual logs, error messages.",
          {"type": "object", "properties": {
              "url": {"type": "string", "description": "Jenkins Blue Ocean or classic URL."},
              "node_id": {"type": "string", "default": "", "description": "Optional specific stage node ID. If empty, auto-detects failed stages."},
          }, "required": ["url"]})

_register("jenkins_analyze_failure", jenkins_analyze_failure,
          "Comprehensive Jenkins pipeline failure analysis. "
          "Combines run metadata, all stage results, failure logs, failed steps, "
          "TFS build cross-references, and error summaries into a complete report. "
          "Use when user asks to analyze or investigate a Jenkins pipeline failure.",
          {"type": "object", "properties": {
              "url": {"type": "string", "description": "Jenkins Blue Ocean or classic URL for a pipeline run."},
          }, "required": ["url"]})


# ── Agent ↔ Tool mapping ──
AGENT_TOOL_MAP = {
    "tfs_jira": {
        "get_analysis_bundle", "start_code_review", "get_code_review_status",
        "get_jira_context", "get_jira_changeset_ids", "search_jira",
        "get_changeset_summary", "get_changeset_diffs", "find_shelvesets", "get_shelveset_summary", "get_shelveset_diffs",
        "add_shelveset_comment", "get_shelveset_comments",
        "get_branch_file", "get_branch_diff", "list_branch_files", "get_branch_history",
        "list_onbase_branches",
        "list_pipeline_folder_aliases", "get_pipelines", "get_builds", "get_build_info",
        "get_build_failures", "get_build_associated_changes", "search_builds_by_jira",
        "bd_list_versions", "bd_list_builds", "bd_get_latest_build", "bd_search_build", "bd_version_aliases",
        "bd_get_botw_builds", "bd_get_build_calendar",
        "jira_fixed_in_build", "jira_cards_in_build",
        "github_repo_aliases", "github_get_repo", "github_list_pulls",
        "github_get_pr_with_diffs", "github_get_pr_reviews", "github_get_pr_comments",
        "github_get_file", "github_search_prs", "github_list_org_repos", "github_search_repos",
        "github_code_scanning_alerts", "github_dependabot_alerts",
        "github_create_branch", "github_create_or_update_file", "github_push_files", "github_create_pr",
        "start_ghas_fix_pr", "get_ghas_fix_pr_status",
        "start_build_failure_analysis", "get_build_failure_analysis_status",
        "get_build_timeline", "get_build_log_content",
        "proget_search_packages",
        "jenkins_get_run", "jenkins_get_failure_log", "jenkins_analyze_failure",
    },
    "onbase": {
        "get_analysis_bundle", "start_code_review", "get_code_review_status",
        "get_jira_context", "get_jira_changeset_ids", "search_jira",
        "get_changeset_summary", "get_changeset_diffs", "find_shelvesets", "get_shelveset_summary", "get_shelveset_diffs",
        "get_branch_file", "get_branch_diff", "list_branch_files", "get_branch_history",
        "list_onbase_branches",
        "get_pipelines", "get_builds", "get_build_info", "get_build_failures",
        "search_builds_by_jira",
        "bd_list_versions", "bd_list_builds", "bd_get_latest_build", "bd_search_build", "bd_version_aliases",
        "bd_get_botw_builds", "bd_get_build_calendar",
        "jira_fixed_in_build", "jira_cards_in_build",
        "github_get_repo", "github_get_file", "github_search_prs",
        "github_list_pulls", "github_get_pr_with_diffs",
        "start_build_failure_analysis", "get_build_failure_analysis_status",
        "get_build_timeline", "get_build_log_content",
        "proget_search_packages",
        "jenkins_get_run", "jenkins_get_failure_log", "jenkins_analyze_failure",
    },
    "mrg_parser": {
        "get_jira_context", "search_jira",
        "get_branch_file", "list_branch_files",
    },
    "accessibility": {
        "get_jira_context", "search_jira",
        "get_branch_file", "get_branch_diff", "list_branch_files",
        "github_get_file", "github_get_pr_with_diffs",
    },
}


# ── Self-test ──
if __name__ == "__main__":
    print(f"Registered {len(TOOL_DEFINITIONS)} tools:")
    for t in TOOL_DEFINITIONS:
        fn = t["function"]
        params = list(fn["parameters"].get("properties", {}).keys())
        print(f"  {fn['name']:40s} params={params}")
    print(f"\nAgent tool counts:")
    for agent, tools in AGENT_TOOL_MAP.items():
        print(f"  {agent:20s} → {len(tools)} tools")
