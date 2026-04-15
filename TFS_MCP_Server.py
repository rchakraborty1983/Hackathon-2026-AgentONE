"""
TFS MCP Server — Model Context Protocol wrapper for TFSMCP.py.

Exposes the existing TFS/Jira/GitHub helper functions as MCP tools
so VS Code Copilot can call them directly (no more Invoke-RestMethod).

Start with:
    python TFS_MCP_Server.py
"""

import json
from typing import Optional
from mcp.server.fastmcp import FastMCP

# Import all helpers from the existing FastAPI service
from TFSMCP import (
    # TFS
    fetch_changeset_with_diffs,
    search_shelvesets,
    find_shelvesets_by_jira_key,
    fetch_shelveset_with_diffs,
    post_shelveset_comment,
    get_shelveset_discussion_threads,
    get_shelveset_web_url,
    get_changeset_web_url,
    # Jira
    fetch_jira_context,
    extract_changeset_ids_from_jira,
    jira_search,
    # Analysis
    build_analysis_bundle,
    # Branches
    resolve_branch_path,
    compute_branch_file_diff,
    get_branch_file_content as _get_branch_file_content,
    list_branch_files as _list_branch_files,
    get_branch_changesets,
    ONBASE_BRANCHES,
    DEFAULT_TARGET_BRANCH,
    LOF_HIGH_RISK_PATHS,
    LOF_JQL_FILTER,
    # Builds / Pipelines
    list_pipeline_definitions,
    list_builds as _list_builds,
    get_build_detail,
    get_build_timeline,
    get_build_log as _get_build_log,
    get_build_logs_list,
    get_build_changes,
    get_build_failure_summary,
    search_builds_by_jira_key,
    PIPELINE_FOLDERS,
    # GitHub
    fetch_github_repo,
    list_github_pulls,
    fetch_github_pr_with_diffs,
    fetch_github_pr_files,
    fetch_github_pr_reviews,
    fetch_github_pr_review_comments,
    fetch_github_file_content,
    search_github_prs,
    resolve_github_repo,
    list_github_org_repos,
    search_github_repos,
    list_code_scanning_alerts,
    get_code_scanning_alert,
    list_dependabot_alerts,
    get_dependabot_alert,
    GITHUB_ORG,
    GITHUB_REPO_ALIASES,
)

mcp = FastMCP("TFS-Jira-GitHub MCP Server")


# ───────────────────────────────────────────────
# ANALYSIS BUNDLE
# ───────────────────────────────────────────────

@mcp.tool()
def get_analysis_bundle(jira_key: str) -> str:
    """Get a full analysis bundle for a Jira key: Jira context + all linked TFS changeset code diffs.
    Returns structured data optimized for AI-driven code review and test scenario generation.
    """
    result = build_analysis_bundle(jira_key)
    return json.dumps(result, indent=2, default=str)


# ───────────────────────────────────────────────
# JIRA
# ───────────────────────────────────────────────

@mcp.tool()
def get_jira_context(jira_key: str) -> str:
    """Get structured Jira issue context: summary, description, repro steps,
    testing recommendations, linked issues, and parent epic.
    """
    result = fetch_jira_context(jira_key)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def get_jira_changeset_ids(jira_key: str) -> str:
    """Extract TFS changeset IDs linked to a Jira issue via remote links."""
    result = extract_changeset_ids_from_jira(jira_key)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def search_jira(jql: str = "", filter_id: str = "", max_results: int = 50) -> str:
    """Run a JQL search against Jira. Provide either jql or filter_id.
    Returns structured issue data (key, summary, status, priority, assignee, etc.).
    """
    if filter_id:
        effective_jql = f"filter={filter_id}"
    elif jql:
        effective_jql = jql
    else:
        return json.dumps({"error": "Provide either 'jql' or 'filter_id'."})
    issues = jira_search(effective_jql, max_results)
    return json.dumps({"jql": effective_jql, "issues": issues, "total": len(issues)}, indent=2, default=str)


# ───────────────────────────────────────────────
# TFS CHANGESETS
# ───────────────────────────────────────────────

@mcp.tool()
def get_changeset_diffs(changeset_id: int) -> str:
    """Get code diffs for a TFS changeset. Returns changeset metadata and
    unified diffs for every changed file.
    """
    result = fetch_changeset_with_diffs(changeset_id)
    return json.dumps(result, indent=2, default=str)


# ───────────────────────────────────────────────
# TFS SHELVESETS
# ───────────────────────────────────────────────

@mcp.tool()
def find_shelvesets(
    name: str = "",
    owner: str = "",
    jira_key: str = "",
    top: int = 50,
) -> str:
    """Search TFS shelvesets by exact name, owner, or Jira key.
    Returns matching shelvesets with metadata.
    """
    if jira_key:
        results = find_shelvesets_by_jira_key(jira_key)
    else:
        results = search_shelvesets(
            name=name or None,
            owner=owner or None,
            top=top,
        )
    return json.dumps({"shelvesets": results, "total": len(results)}, indent=2, default=str)


@mcp.tool()
def get_shelveset_diffs(shelveset_name: str, owner: str) -> str:
    """Get full shelveset details including unified diffs for every changed file.
    Owner should be the unique name (e.g. DOMAIN\\user).
    """
    result = fetch_shelveset_with_diffs(shelveset_name, owner)
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def add_shelveset_comment(shelveset_name: str, owner: str, comment: str) -> str:
    """Post a Markdown discussion comment on a TFS shelveset.
    Use this to attach code review summaries directly to the shelveset in TFS.
    """
    result = post_shelveset_comment(shelveset_name, owner, comment)
    web_url = get_shelveset_web_url(shelveset_name, owner)
    return json.dumps({
        "status": "posted",
        "shelveset": shelveset_name,
        "owner": owner,
        "web_url": web_url,
        "thread": result,
    }, indent=2, default=str)


@mcp.tool()
def get_shelveset_comments(shelveset_name: str, owner: str) -> str:
    """Get all discussion threads and comments on a TFS shelveset."""
    threads = get_shelveset_discussion_threads(shelveset_name, owner)
    web_url = get_shelveset_web_url(shelveset_name, owner)
    return json.dumps({
        "shelveset": shelveset_name,
        "owner": owner,
        "web_url": web_url,
        "threads": threads,
        "total": len(threads),
    }, indent=2, default=str)


# ───────────────────────────────────────────────
# BRANCH COMPARISON / LOF
# ───────────────────────────────────────────────

@mcp.tool()
def list_onbase_branches() -> str:
    """List all known OnBase version branches with their TFS paths."""
    return json.dumps({
        "branches": dict(sorted(ONBASE_BRANCHES.items())),
        "default_target": DEFAULT_TARGET_BRANCH,
    }, indent=2)


@mcp.tool()
def get_branch_diff(
    relative_path: str,
    source_version: str,
    target_version: str = "DEV",
    max_lines: int = 500,
) -> str:
    """Diff a file between two OnBase version branches.
    Versions can be labels like '20', '24.1', 'DEV', etc.
    Returns a unified diff.
    """
    source_root = resolve_branch_path(source_version)
    target_root = resolve_branch_path(target_version)
    result = compute_branch_file_diff(relative_path, source_root, target_root, max_lines=max_lines)
    result["source_version"] = source_version
    result["target_version"] = target_version
    result["source_branch"] = source_root
    result["target_branch"] = target_root
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def get_branch_file(
    relative_path: str,
    version: str = "DEV",
    start_line: int = 0,
    end_line: int = 0,
) -> str:
    """Get file content from an OnBase version branch.
    Supports optional start_line/end_line for partial reads (1-based).
    """
    branch_root = resolve_branch_path(version)
    content_text = _get_branch_file_content(branch_root, relative_path)
    if content_text is None:
        return json.dumps({"error": f"File not found: {relative_path} in branch {version}"})
    lines = content_text.splitlines()
    total_lines = len(lines)
    sl = max(1, start_line) if start_line > 0 else 1
    el = min(total_lines, end_line) if end_line > 0 else total_lines
    selected = lines[sl - 1 : el]
    return json.dumps({
        "version": version,
        "relative_path": relative_path,
        "total_lines": total_lines,
        "start_line": sl,
        "end_line": el,
        "content": "\n".join(selected),
    }, indent=2, default=str)


@mcp.tool()
def list_branch_files(
    relative_path: str,
    version: str = "DEV",
    recursion: str = "Full",
) -> str:
    """List files under a folder path within an OnBase version branch.
    Use recursion=OneLevel for shallow listing, Full for recursive.
    """
    branch_root = resolve_branch_path(version)
    full_path = f"{branch_root}/{relative_path}"
    files = _list_branch_files(full_path, recursion)
    return json.dumps({
        "version": version,
        "folder": relative_path,
        "files": files,
        "total_files": len(files),
    }, indent=2, default=str)


@mcp.tool()
def get_branch_history(
    relative_path: str,
    version: str = "DEV",
    from_date: str = "",
    to_date: str = "",
    top: int = 100,
) -> str:
    """Get changeset history for a file/folder within an OnBase version branch.
    Dates should be ISO format (e.g. 2025-01-01).
    """
    branch_root = resolve_branch_path(version)
    full_path = f"{branch_root}/{relative_path}"
    changesets = get_branch_changesets(
        full_path,
        from_date=from_date or None,
        to_date=to_date or None,
        top=top,
    )
    return json.dumps({
        "version": version,
        "path": relative_path,
        "changesets": changesets,
        "total_changesets": len(changesets),
    }, indent=2, default=str)


@mcp.tool()
def run_lof_analysis(
    source_version: str,
    target_version: str = "DEV",
    include_jira: bool = False,
    paths: list[str] = None,
) -> str:
    """Run a Loss of Functionality analysis between two OnBase version branches.
    Compares high-risk WorkView files to identify code divergences that could
    indicate regressions after upgrade. Optionally fetches known LoF bugs from Jira.
    """
    source_root = resolve_branch_path(source_version)
    target_root = resolve_branch_path(target_version)

    scan_paths = paths if paths else LOF_HIGH_RISK_PATHS
    divergences = []

    for rel_path in scan_paths:
        source_folder = f"{source_root}/{rel_path}"
        target_folder = f"{target_root}/{rel_path}"

        try:
            source_files_raw = _list_branch_files(source_folder, "Full")
        except Exception:
            source_files_raw = []
        try:
            target_files_raw = _list_branch_files(target_folder, "Full")
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
                })
            elif in_source and not in_target:
                divergences.append({
                    "relative_path": file_rel,
                    "scan_area": rel_path,
                    "status": "removed_in_target",
                })

    result = {
        "source_version": source_version,
        "target_version": target_version,
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

    return json.dumps(result, indent=2, default=str)


# ───────────────────────────────────────────────
# BUILDS / PIPELINES
# ───────────────────────────────────────────────

@mcp.tool()
def list_pipeline_folder_aliases() -> str:
    """List well-known pipeline folder aliases and their TFS paths."""
    return json.dumps({"folders": PIPELINE_FOLDERS}, indent=2)


@mcp.tool()
def get_pipelines(
    folder: str = "",
    folder_alias: str = "",
    version: str = "",
    name_filter: str = "",
    top: int = 100,
) -> str:
    """List build pipeline definitions in TFS.
    Filter by folder path, folder alias (official_builds, workview_tests,
    dev_official_builds), or name. For workview_tests, use version param
    (e.g. DEV, 25.1) to specify subfolder.
    """
    effective_folder = folder or None
    if folder_alias:
        effective_folder = PIPELINE_FOLDERS.get(folder_alias.lower())
        if not effective_folder:
            return json.dumps({"error": f"Unknown alias '{folder_alias}'. Valid: {list(PIPELINE_FOLDERS.keys())}"})
    if version and effective_folder:
        v = version.strip().upper() if version.strip().upper() == "DEV" else version.strip()
        effective_folder = f"{effective_folder}\\{v}"

    defs = list_pipeline_definitions(
        folder=effective_folder,
        name_filter=name_filter or None,
        top=top,
    )
    return json.dumps({"folder": effective_folder, "definitions": defs, "total": len(defs)}, indent=2, default=str)


@mcp.tool()
def get_builds(
    definition_id: int = 0,
    definition_name: str = "",
    status_filter: str = "",
    result_filter: str = "",
    top: int = 20,
) -> str:
    """List builds with various filters.
    Status: inProgress, completed, cancelling, postponed, notStarted, all.
    Result: succeeded, partiallySucceeded, failed, canceled.
    """
    builds = _list_builds(
        definition_id=definition_id or None,
        definition_name=definition_name or None,
        status_filter=status_filter or None,
        result_filter=result_filter or None,
        top=top,
    )
    return json.dumps({"builds": builds, "total": len(builds)}, indent=2, default=str)


@mcp.tool()
def get_build_info(build_id: int) -> str:
    """Get detailed information about a specific build by ID."""
    return json.dumps(get_build_detail(build_id), indent=2, default=str)


@mcp.tool()
def get_build_failures(build_id: int, include_logs: bool = False, max_log_lines: int = 200) -> str:
    """Get a failure summary for a build: failed tasks, error messages,
    and optionally the tail of each failed task's log.
    """
    return json.dumps(get_build_failure_summary(build_id, include_logs, max_log_lines), indent=2, default=str)


@mcp.tool()
def get_build_log_content(build_id: int, log_id: int, tail: int = 0) -> str:
    """Get the text content of a specific build log.
    Use tail > 0 to get only the last N lines.
    """
    log_text = _get_build_log(build_id, log_id)
    if tail > 0:
        lines = log_text.splitlines()
        if len(lines) > tail:
            return json.dumps({
                "build_id": build_id,
                "log_id": log_id,
                "content": "\n".join(lines[-tail:]),
                "truncated": True,
                "total_lines": len(lines),
            }, indent=2)
    return json.dumps({"build_id": build_id, "log_id": log_id, "content": log_text, "truncated": False}, indent=2)


@mcp.tool()
def get_build_associated_changes(build_id: int) -> str:
    """Get the changesets/commits associated with a build.
    This reveals which Jira key triggered a specific build.
    """
    changes = get_build_changes(build_id)
    return json.dumps({"build_id": build_id, "changes": changes, "total": len(changes)}, indent=2, default=str)


@mcp.tool()
def search_builds_by_jira(
    jira_key: str,
    definition_id: int = 0,
    folder: str = "",
    folder_alias: str = "",
    top: int = 20,
) -> str:
    """Search for builds that reference a Jira key in their changeset comments.
    Optionally narrow by pipeline definition ID or folder.
    """
    effective_folder = folder or None
    if folder_alias:
        effective_folder = PIPELINE_FOLDERS.get(folder_alias.lower())
    builds = search_builds_by_jira_key(
        jira_key,
        definition_id=definition_id or None,
        folder=effective_folder,
    )
    return json.dumps({"jira_key": jira_key, "builds": builds, "total": len(builds)}, indent=2, default=str)


# ───────────────────────────────────────────────
# GITHUB
# ───────────────────────────────────────────────

@mcp.tool()
def github_repo_aliases() -> str:
    """List well-known GitHub repo aliases within the HylandFoundation org."""
    return json.dumps({"org": GITHUB_ORG, "aliases": GITHUB_REPO_ALIASES}, indent=2)


@mcp.tool()
def github_get_repo(owner: str = "", repo: str = "") -> str:
    """Get GitHub repository metadata: description, default branch, language, topics, etc.
    If owner is omitted, defaults to HylandFoundation.
    """
    owner = owner or GITHUB_ORG
    return json.dumps(fetch_github_repo(owner, repo), indent=2, default=str)


@mcp.tool()
def github_list_pulls(
    owner: str = "",
    repo: str = "",
    state: str = "open",
    sort: str = "updated",
    direction: str = "desc",
    per_page: int = 30,
) -> str:
    """List pull requests for a GitHub repo.
    State: open, closed, all. Sort: created, updated, popularity.
    """
    owner = owner or GITHUB_ORG
    pulls = list_github_pulls(owner, repo, state, sort, direction, per_page)
    return json.dumps({"owner": owner, "repo": repo, "pulls": pulls, "total": len(pulls)}, indent=2, default=str)


@mcp.tool()
def github_get_pr_with_diffs(owner: str = "", repo: str = "", pr_number: int = 0) -> str:
    """Get full PR details including metadata, body, merge status, and unified diffs
    for all changed files.
    """
    owner = owner or GITHUB_ORG
    return json.dumps(fetch_github_pr_with_diffs(owner, repo, pr_number), indent=2, default=str)


@mcp.tool()
def github_get_pr_reviews(owner: str = "", repo: str = "", pr_number: int = 0) -> str:
    """Get all reviews on a GitHub PR (reviewer, state, body)."""
    owner = owner or GITHUB_ORG
    reviews = fetch_github_pr_reviews(owner, repo, pr_number)
    return json.dumps({"reviews": reviews, "total": len(reviews)}, indent=2, default=str)


@mcp.tool()
def github_get_pr_comments(owner: str = "", repo: str = "", pr_number: int = 0) -> str:
    """Get all inline code-level review comments on a GitHub PR."""
    owner = owner or GITHUB_ORG
    comments = fetch_github_pr_review_comments(owner, repo, pr_number)
    return json.dumps({"comments": comments, "total": len(comments)}, indent=2, default=str)


@mcp.tool()
def github_get_file(owner: str = "", repo: str = "", path: str = "", ref: str = "") -> str:
    """Get file content from a GitHub repository. Optionally specify ref (branch/tag/SHA)."""
    owner = owner or GITHUB_ORG
    return json.dumps(fetch_github_file_content(owner, repo, path, ref or None), indent=2, default=str)


@mcp.tool()
def github_search_prs(owner: str = "", repo: str = "", query: str = "", per_page: int = 20) -> str:
    """Search PRs in a GitHub repo by keyword."""
    owner = owner or GITHUB_ORG
    results = search_github_prs(owner, repo, query, per_page)
    return json.dumps({"results": results, "total": len(results)}, indent=2, default=str)


@mcp.tool()
def github_list_org_repos(
    org: str = "",
    sort: str = "updated",
    repo_type: str = "all",
    per_page: int = 30,
) -> str:
    """List repositories in the GitHub org (default: HylandFoundation)."""
    repos = list_github_org_repos(org or None, per_page, sort, repo_type)
    return json.dumps({"org": org or GITHUB_ORG, "repos": repos, "total": len(repos)}, indent=2, default=str)


@mcp.tool()
def github_search_repos(query: str, org: str = "", per_page: int = 20) -> str:
    """Search repositories in the GitHub org by keyword."""
    results = search_github_repos(query, org or None, per_page)
    return json.dumps({"results": results, "total": len(results)}, indent=2, default=str)


@mcp.tool()
def github_code_scanning_alerts(
    owner: str = "",
    repo: str = "",
    state: str = "open",
    severity: str = "",
    per_page: int = 30,
) -> str:
    """List code scanning (CodeQL) alerts for a GitHub repo.
    State: open, closed, dismissed, fixed.
    Severity: critical, high, medium, low, warning, note, error.
    """
    owner = owner or GITHUB_ORG
    alerts = list_code_scanning_alerts(owner, repo, state, severity or None, per_page)
    return json.dumps({"alerts": alerts, "total": len(alerts)}, indent=2, default=str)


@mcp.tool()
def github_dependabot_alerts(
    owner: str = "",
    repo: str = "",
    state: str = "open",
    severity: str = "",
    per_page: int = 30,
) -> str:
    """List Dependabot vulnerability alerts for a GitHub repo.
    State: auto_dismissed, dismissed, fixed, open.
    Severity: critical, high, medium, low.
    """
    owner = owner or GITHUB_ORG
    alerts = list_dependabot_alerts(owner, repo, state, severity or None, per_page)
    return json.dumps({"alerts": alerts, "total": len(alerts)}, indent=2, default=str)


# ───────────────────────────────────────────────
# ENTRY POINT
# ───────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
