# Agent ONE — VS Code Setup Guide

This guide covers setting up Agent ONE for use **directly in VS Code** via GitHub Copilot agents and MCP tools — no Teams interface needed.

## What You Get

After setup, you'll have these capabilities inside VS Code (via Copilot Chat):

| Capability | How to Use |
|---|---|
| **TFS changesets/shelvesets** | Ask `@TFS_Jira_Analyzer` to fetch diffs, history, branch files |
| **Jira card analysis** | Ask any agent to analyze a Jira key (e.g., "analyze SBPWC-12345") |
| **GitHub PRs & security alerts** | PR diffs, reviews, GHAS/Dependabot alerts |
| **Code reviews** | Full code review with HTML report generation |
| **Branch comparison / LoF analysis** | Compare files across OnBase version branches |
| **Build queries** | Latest builds, BOTW dates, build failures |
| **BuildDirector integration** | Build info, build calendars, cards-in-build |
| **ProGet package search** | Search NuGet packages on Hyland ProGet |
| **Jenkins pipeline analysis** | Analyze Jenkins build failures with TFS cross-references |
| **Confluence search** | Search internal Confluence docs (via Atlassian MCP) |
| **Jira search (JQL)** | Run arbitrary JQL queries |

> **Note**: MRG_Parser (OnBase Module Reference Guide) and Salesforce support case lookups require a Copilot Studio agent with knowledge sources configured. These are cloud services and cannot be self-hosted. You can skip them — the core DevOps features work independently.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| **VS Code** | Latest | With GitHub Copilot extension |
| **GitHub Copilot** | Business/Enterprise | Agent mode and MCP support required |
| **Python** | 3.12+ | For the MCP server backend |
| **Node.js** | 18+ | For the GitHub MCP server (`npx`) |
| **Network access** | — | Must reach TFS server, Jira, GitHub, BuildDirector, ProGet, Jenkins |

---

## Step 1: Clone the Repository

```powershell
git clone https://github.com/rchakraborty1983/Hackathon-2026-AgentONE.git C:\TFS_MCP
cd C:\TFS_MCP
```

You can clone to any location — just update the paths in Steps 3 and 5 accordingly.

---

## Step 2: Create Python Virtual Environment & Install Dependencies

```powershell
cd C:\TFS_MCP
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Verify installation:
```powershell
python -c "from TFSMCP import app; print('Backend OK')"
python -c "from TFS_MCP_Server import mcp; print('MCP Server OK')"
```

---

## Step 3: Set Environment Variables

Set these as **User** environment variables (persist across sessions):

```powershell
# REQUIRED — TFS (on-prem)
[System.Environment]::SetEnvironmentVariable("TFS_PAT", "your-tfs-personal-access-token", "User")

# REQUIRED — Jira (Atlassian Cloud)
[System.Environment]::SetEnvironmentVariable("JIRA_EMAIL", "your.email@hyland.com", "User")
[System.Environment]::SetEnvironmentVariable("JIRA_API_TOKEN", "your-atlassian-api-token", "User")

# REQUIRED — GitHub
[System.Environment]::SetEnvironmentVariable("GITHUB_TOKEN", "ghp_your-github-pat", "User")
```

**Restart VS Code** after setting environment variables (or restart your terminal).

### How to Get Tokens

| Token | Where to Get It |
|---|---|
| **TFS PAT** | TFS Web → User icon → Security → Personal Access Tokens → New Token (scope: Code Read/Write) |
| **Jira API Token** | https://id.atlassian.com/manage-profile/security/api-tokens → Create API token |
| **GitHub PAT** | https://github.com/settings/tokens → Generate new token (classic). Scopes: `repo`, `read:org`, `security_events` |

### Optional Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `GITHUB_ORG` | `HylandFoundation` | Default GitHub organization for queries |
| `JIRA_BASE_URL` | `https://hyland.atlassian.net` | Jira instance URL |
| `GITHUB_BASE_URL` | `https://api.github.com` | GitHub API URL |
| `JENKINS_BASE_URL` | `https://csp.jenkins.hylandqa.net` | Jenkins server URL |
| `API_KEY` | (none) | API key for the FastAPI server (optional auth) |
| `API_HOST` | `127.0.0.1` | Host for FastAPI server |
| `API_PORT` | `9000` | Port for FastAPI server |

---

## Step 4: Configure VS Code MCP Servers

Copy the template to your VS Code user config:

```powershell
$mcpDest = "$env:APPDATA\Code\User\mcp.json"

# If you already have an mcp.json, merge manually. Otherwise:
Copy-Item "C:\TFS_MCP\mcp.json.template" $mcpDest
```

Then **edit** `%APPDATA%\Code\User\mcp.json` and replace `${CLONE_DIR}` with your actual clone path:

```json
{
  "servers": {
    "mcp-atlassian": {
      "url": "https://mcp.atlassian.com/v1/sse",
      "type": "sse"
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "${env:GITHUB_TOKEN}"
      }
    },
    "tfs-jira-github": {
      "type": "stdio",
      "command": "C:\\TFS_MCP\\.venv\\Scripts\\python.exe",
      "args": ["C:\\TFS_MCP\\TFS_MCP_Server.py"]
    }
  }
}
```

> **Important**: The `tfs-jira-github` server paths must point to YOUR clone location's `.venv\Scripts\python.exe` and `TFS_MCP_Server.py`.

### What Each MCP Server Provides

| Server | Tools | Auth |
|---|---|---|
| **tfs-jira-github** | TFS changesets, shelvesets, branches, builds, pipelines, Jira context/search, GitHub PRs, GHAS alerts, BuildDirector, ProGet, Jenkins | Uses `TFS_PAT`, `JIRA_EMAIL`/`JIRA_API_TOKEN`, `GITHUB_TOKEN` env vars |
| **mcp-atlassian** | Confluence search, Jira issue CRUD, Confluence page CRUD | Browser-based OAuth (first use opens Atlassian login) |
| **github** | GitHub issues, PRs, code search, file contents, branches | Uses `GITHUB_TOKEN` env var |

### First-Time Atlassian MCP Auth

The `mcp-atlassian` server uses OAuth. On first use:
1. VS Code will prompt you to authenticate
2. A browser window opens → log in to your Atlassian account
3. Authorize the MCP connection
4. The session persists until you revoke it

---

## Step 5: Install Agent Prompt Files

Copy the agent definitions to your VS Code prompts folder:

```powershell
$promptsDest = "$env:APPDATA\Code\User\prompts"

# Create folder if it doesn't exist
New-Item -ItemType Directory -Path $promptsDest -Force | Out-Null

# Copy agent files
Copy-Item "C:\TFS_MCP\agent-prompts\*.agent.md" $promptsDest -Force

# Copy supporting docs and scripts
Copy-Item "C:\TFS_MCP\agent-prompts\agents" "$promptsDest\agents" -Recurse -Force
```

### Customize Paths for Your Machine

Open `%APPDATA%\Code\User\prompts\onbase.agent.md` and find the **Team Configuration — Environment Paths** table. Update each path to match your machine:

| Variable Name | Default Path | What to Change |
|---|---|---|
| `ONBASE_CODEBASE_ROOT` | `C:\OnBase\Codebase` | Your OnBase source checkout root |
| `ONBASE_NET_ROOT` | `C:\OnBase\Codebase\Core\OnBase.NET` | .NET solution root |
| `TEST_AUTOMATION_ROOT` | `C:\Users\rchakraborty\SBP-WV-...` | Your Selenium test framework path |
| `STUDIO_TEST_AUTOMATION_ROOT` | `C:\GHCP_TestAutomation` | Your FlaUI test framework path |
| `AGENT_ROOT` | `C:\Users\rchakraborty\AppData\...` | Your VS Code prompts\agents path |
| `MARKDOWN_ANALYSIS_ROOT` | `C:\Users\rchakraborty\AppData\...` | Where analysis reports are saved |

> Only update paths you actually use. If you don't have a test automation checkout, leave those paths as-is — the agent will just skip those features.

---

## Step 6: Verify Setup

Restart VS Code, then open Copilot Chat (Ctrl+Shift+I) and test:

### Test MCP Tools
```
@TFS_Jira_Analyzer list the known OnBase branches
```
Expected: Returns a JSON list of TFS branch paths (DEV, 24.1, 25.1, 25.2, etc.)

### Test Jira
```
@TFS_Jira_Analyzer get details of SBPWC-12646
```
Expected: Returns Jira card summary, description, status, assignee

### Test GitHub
```
@TFS_Jira_Analyzer list open PRs for SBP-WV-onbase-web-server-ng-apps
```
Expected: Returns list of open pull requests

### Test OnBase Agent
```
@OnBase What is a WorkView Filter?
```
Expected: Explains WorkView filter concept (will consult MRG if MRG_Parser is available)

---

## Available Agents

After setup, these agents appear in VS Code Copilot Chat:

| Agent | Invoke With | Purpose |
|---|---|---|
| **OnBase** | `@OnBase` | Primary OnBase development agent — WorkView, debugging, architecture, licensing |
| **TFS_Jira_Analyzer** | `@TFS_Jira_Analyzer` | TFS/Jira analysis, code reviews, build queries, GitHub PRs, Jenkins |
| **MRG_Parser** | `@MRG_Parser` | OnBase Module Reference Guide lookups (requires Copilot Studio — see note below) |

### MRG_Parser Limitations

The MRG_Parser agent is a **Copilot Studio cloud agent** that queries:
- OnBase MRG documentation (`support.hyland.com/r/OnBase`)
- OnBase REST API SDK (`sdk.onbase.com/rest/`)
- Salesforce support cases

These knowledge sources are hosted in Copilot Studio and cannot be self-hosted. Without your own Copilot Studio agent configured:
- `@MRG_Parser` queries will not resolve
- `@OnBase` will fall back to codebase search instead of MRG lookups
- All other features (TFS, Jira, GitHub, builds, code reviews) work normally

---

## Troubleshooting

| Problem | Solution |
|---|---|
| MCP server not connecting | Check paths in `mcp.json` match your clone location. Verify Python venv exists. |
| `TFS_PAT` errors | Regenerate PAT in TFS. Ensure scope includes Code Read/Write. |
| Jira 401 errors | Verify `JIRA_EMAIL` and `JIRA_API_TOKEN` are set. Token is NOT your password — generate at Atlassian. |
| GitHub 401 errors | Check `GITHUB_TOKEN` has `repo`, `read:org`, `security_events` scopes. |
| `npx` not found | Install Node.js 18+ and ensure `npx` is on your PATH. |
| Agent not appearing in Copilot Chat | Restart VS Code. Check `.agent.md` files are in `%APPDATA%\Code\User\prompts\`. |
| Atlassian MCP auth loop | Clear cached OAuth: VS Code Command Palette → "MCP: Reset Server" → re-authenticate. |
| BuildDirector timeouts | BuildDirector (`qa-websrvr`) is internal only — must be on Hyland VPN. |
| Jenkins SSL errors | Jenkins uses self-signed cert — handled automatically (`verify=False`). |

---

## Network Requirements

The MCP server connects to these internal/external services:

| Service | URL | Network |
|---|---|---|
| TFS | `http://dev-tfs:8080/tfs/HylandCollection` | Internal (VPN) |
| BuildDirector | `http://qa-websrvr/BuildDirector` | Internal (VPN) |
| ProGet | `https://proget.onbase.net` | Internal (VPN) |
| Jenkins | `https://csp.jenkins.hylandqa.net` | Internal (VPN) |
| Jira | `https://hyland.atlassian.net` | External (Atlassian Cloud) |
| GitHub | `https://api.github.com` | External |
| Confluence | Via Atlassian MCP SSE | External (Atlassian Cloud) |
