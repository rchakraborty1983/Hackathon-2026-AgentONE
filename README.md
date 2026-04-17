# Agent ONE — Hyland Hackathon 2026

**Agent ONE** is an AI-powered DevOps assistant for OnBase engineering teams, deployed as a Microsoft Teams bot via Copilot Studio. It provides a single conversational interface for TFS, Jira, GitHub, build systems, Salesforce, ProGet, Confluence, and OnBase documentation.

## Architecture

```
Microsoft Teams
    ↓
Copilot Studio (Agent ONE / MRG_Parser)    ← copilot-studio/
    ↓ HTTP POST /chat
FastAPI Server (Agent Router)              ← teams_bot/app.py
    ↓
Agent Executor                             ← teams_bot/agent_executor.py
    ↓
TFS MCP Backend                            ← TFSMCP.py + TFS_MCP_Server.py
    ↓
TFS REST API | Jira API | GitHub API | BuildDirector | ProGet | Confluence | Salesforce
```

## Components

### Backend Server (`TFSMCP.py`, `TFS_MCP_Server.py`)
- **TFSMCP.py** — Core backend with all API integrations (TFS, Jira, GitHub, BuildDirector, ProGet, Confluence, Salesforce)
- **TFS_MCP_Server.py** — MCP (Model Context Protocol) server exposing TFS tools for VS Code agents
- **watchdog.ps1** — Process monitor that auto-restarts crashed services

### Teams Bot (`teams_bot/`)
- **app.py** — FastAPI server with `/chat` endpoint, fast-path routing, and conversation management
- **agent_router.py** — Routes user messages to the correct agent (DevOps, MRG, Jira, Salesforce, etc.)
- **agent_executor.py** — Executes agent logic via OpenAI GPT-5 with tool calling
- **tool_registry.py** — Defines all available tools and their schemas for the LLM
- **ux_enhancements.py** — Response formatting, suggested actions, adaptive cards
- **config.py** — Environment configuration
- **openapi.json** — OpenAPI spec for Copilot Studio HTTP connector
- **copilot_studio_instructions.txt** — Instructions pasted into Copilot Studio agent

### Copilot Studio Agent (`copilot-studio/`)
Exported source of the Copilot Studio agent (Agent ONE), which also serves as the **MRG_Parser** agent for VS Code.
- **settings.mcs.yml** — Agent configuration (orchestration, AI settings, recognizer)
- **actions/** — Tool connectors (AgentONE HTTP action, Atlassian Rovo MCP, Salesforce MCP)
- **knowledge/** — Knowledge sources (OnBase MRG, REST API SDK, Hyland Connect, WCAG)
- **topics/** — System topics (Greeting, Fallback, Conversation Start, etc.)

### VS Code Agent Prompts (`agent-prompts/`)
GitHub Copilot custom agent instructions used in VS Code for OnBase development:
- **onbase.agent.md** — Primary OnBase development agent (WorkView, debugging, architecture)
- **TFS_Jira_Analyzer.agent.md** — TFS/Jira analysis, code reviews, build queries
- **MRG_Parser.agent.md** — Module Reference Guide documentation retrieval
- **agents/docs/** — Supporting documentation (code styles, code review skill, WorkView guides)
- **agents/scripts/** — Utility scripts (Hyland Connect forum fetcher)

### IIS Configuration (`iis/`)
- **web.config** — IIS reverse proxy configuration for hosting the FastAPI server

## Skills

| Skill | Command Examples |
|---|---|
| Jira Assistant | "analyze SBPWC-12345", "get details of CSFMD-10783" |
| Code Review | "code review SBPWC-12614", "review status SBPWC-12614" |
| Build Queries | "latest build for 25.2", "what is the next BOTW date?" |
| Branch Compare | "compare DEV and 25.1 for WorkViewUtility.cs" |
| Build Failure | "analyze build failure for 25.2" |
| GitHub PR | "create PR for SBPWC-12345" |
| GHAS Alerts | "show Critical GHAS alerts for HylandFoundation org" |
| Package Check | "latest cefsharp package on ProGet" |
| Salesforce Cases | "top 5 customer issue trends for OnBase" |
| Product Guidance | "How to create a Unity form from WorkView object?" |
| Confluence | "tell me about Hyland's Product Operating Model" |

## Setup

### Option A: VS Code Only (Recommended for developers)

Use Agent ONE directly in VS Code via GitHub Copilot agents and MCP tools — no Teams bot needed.

**See [SETUP.md](SETUP.md) for the full step-by-step guide.**

Quick summary:
```powershell
git clone https://github.com/rchakraborty1983/Hackathon-2026-AgentONE.git C:\TFS_MCP
cd C:\TFS_MCP
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# Set env vars: TFS_PAT, JIRA_EMAIL, JIRA_API_TOKEN, GITHUB_TOKEN
# Copy mcp.json.template → %APPDATA%\Code\User\mcp.json (update paths)
# Copy agent-prompts/*.agent.md → %APPDATA%\Code\User\prompts\
# Restart VS Code → use @OnBase or @TFS_Jira_Analyzer in Copilot Chat
```

### Option B: Teams Bot (Full deployment with Copilot Studio)

For the Teams conversational interface, additional setup is required:

**Prerequisites:**
- Everything from Option A
- Azure OpenAI or OpenAI API key (GPT-5)
- Copilot Studio license
- Dev tunnel or public HTTPS endpoint

```powershell
# 1. Start TFS MCP Server (port 9000)
python TFSMCP.py

# 2. Start Agent ONE FastAPI server (port 9100)
cd teams_bot
python app.py

# 3. Start dev tunnel (for Copilot Studio → local server)
devtunnel host --port 9100

# 4. Or use watchdog to manage all services
powershell -File watchdog.ps1
```

**Copilot Studio Setup:**
1. Import `copilot-studio/` into Copilot Studio
2. Configure the HTTP action with your dev tunnel URL
3. Paste `teams_bot/copilot_studio_instructions.txt` into the Instructions field
4. Disable "Conversational boosting" topic and "Allow ungrounded responses"
5. Publish to Teams

## Key Configuration

| Setting | Purpose |
|---|---|
| Conversational boosting | **OFF** — prevents duplicate responses |
| Allow ungrounded responses | **OFF** — prevents LLM generating own answers alongside tool |
| Orchestration | **Yes** (dynamic) — needed for automatic tool routing |
| GenerativeAIRecognizer | Routes queries to the DevOps tool |

## Team

Built by the **OnBase WorkView SBP team** for the Hyland Hackathon 2026.
  - `extract_docx.py` - Python utility scripts
  - `branch_check.ps1` - Branch validation scripts

## 🔧 Usage by Agents

### OnBase Agent
- Delegates all script file creation to TFS_Jira_Analyzer
- Does NOT create files in C:\temp
- Requests TFS_Jira_Analyzer to output to C:\TFS_MCP

### TFS_Jira_Analyzer Agent
- **Primary responsibility**: Create all analysis files in C:\TFS_MCP
- Organizes files into appropriate subdirectories
- Uses descriptive filenames with Jira keys and context

## 📝 File Naming Conventions

**Good Examples:**
- `SBPWC-12704_changeset_405153_analysis.json`
- `WorkView_AttributesPage_shelveset_diff.cs`
- `build_429_failures_report.html`
- `cef_upgrade_impact_analysis.py`

**Bad Examples (Avoid):**
- `temp.py`
- `output.txt`
- `file1.cs`
- `data.json`

## 🧹 Cleanup Policy

- Keep analysis files for active Jira cards
- Archive or delete files after Jira cards are closed and verified
- Review directory size monthly
- Move historical analysis to archive if needed

## 📅 Migration from C:\temp

**Date**: April 3, 2026

All analysis files previously created in C:\temp have been migrated to C:\TFS_MCP with proper organization:
- Changeset files → changesets/
- Build files → builds/
- Diff files → diffs/
- Reports → reports/
- Scripts → scripts/

## 🔗 Related Documentation

- Agent Configuration: `C:\Users\rchakraborty\AppData\Roaming\Code\User\prompts\onbase.agent.md`
- TFS Analyzer Config: `C:\Users\rchakraborty\AppData\Roaming\Code\User\prompts\TFS_Jira_Analyzer.agent.md`
- User Memory: `/memories/onbase-impact-analysis.md`

## 📌 Quick Reference

| Task | Output Location |
|------|----------------|
| Changeset analysis | C:\TFS_MCP\changesets\ |
| Build analysis | C:\TFS_MCP\builds\ |
| Code diffs | C:\TFS_MCP\diffs\ |
| HTML reports | C:\TFS_MCP\reports\ |
| Python/PS scripts | C:\TFS_MCP\scripts\ |

---

**Last Updated**: April 3, 2026  
**Maintained by**: OnBase & TFS_Jira_Analyzer agents
