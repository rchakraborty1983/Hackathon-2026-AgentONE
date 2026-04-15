"""
UX Enhancements for AgentONE — Tips, suggested prompts, skill menu, and response polishing.

Provides hackathon-grade user experience features:
  - Contextual follow-up suggestions after every response
  - Suggested action buttons (quick replies) for common tasks
  - Bare-command prompting (e.g., "Code review" → ask for Jira key)
  - Rotating tips to teach users about features
  - Rich skill menu with categorized quick-action prompts
  - Welcome/onboarding message for new conversations
  - Response branding with consistent formatting
"""

import random
import re
import time

# ── Feature Tips (shown randomly after responses) ──
_TIPS = [
    "💡 **Tip:** Ask me to *review a shelveset* — I'll generate a full HTML code review report with findings, security checks, and a verdict.",
    "💡 **Tip:** Say `analyze build failure <build_id>` — I'll trace the root cause, analyze logs, and generate a professional failure report.",
    "💡 **Tip:** I can compare code across OnBase branches! Try: *\"Compare WorkViewUtility.cs between DEV and 25.1\"*",
    "💡 **Tip:** Need the latest build? Just ask: *\"What is the latest build for OnBase 25.2?\"*",
    "💡 **Tip:** I can create GitHub PRs end-to-end! Say: *\"Create a branch and PR for SBPWC-12345\"*",
    "💡 **Tip:** Try *\"What cards are in build 441?\"* to see all Jira issues included in a specific build.",
    "💡 **Tip:** Ask *\"Show BOTW schedule\"* to see Build Of The Week history with generation and publish dates.",
    "💡 **Tip:** I can search across Jira with JQL! Try: *\"Find open bugs in WorkView from last 30 days\"*",
    "💡 **Tip:** Say *\"code review SBPWC-12345\"* — I'll fetch the Jira card, pull all changesets, analyze diffs, and deliver an HTML report.",
    "💡 **Tip:** Ask about OnBase modules! Try: *\"How do I configure WorkView Filters?\"* — I'll check the MRG for you.",
    "💡 **Tip:** I remember our conversation! Feel free to ask follow-up questions without repeating context.",
    "💡 **Tip:** Need accessibility audit help? Just describe the scenario — I'll check WCAG guidelines and generate test cases.",
    "💡 **Tip:** Try *\"Show build calendar for 25.2\"* to see upcoming build milestones and lockdown dates.",
    "💡 **Tip:** I can fetch Salesforce support cases! Reference the Support Issue ID from any Jira card.",
    "💡 **Tip:** Say *\"List GitHub code scanning alerts for repo X\"* to check for security vulnerabilities.",
]

# Track which tips were shown per conversation to avoid repeats
_shown_tips: dict[str, set[int]] = {}


def get_random_tip(conversation_id: str = "") -> str:
    """Get a random tip that hasn't been shown in this conversation."""
    shown = _shown_tips.get(conversation_id, set())
    available = [i for i in range(len(_TIPS)) if i not in shown]
    if not available:
        # All shown — reset
        shown.clear()
        available = list(range(len(_TIPS)))

    idx = random.choice(available)
    shown.add(idx)
    _shown_tips[conversation_id] = shown
    return _TIPS[idx]


# ── Suggested Follow-Up Prompts (contextual) ──

_FOLLOWUP_PATTERNS = [
    # (regex pattern on response text, list of suggested follow-ups)
    (r"changeset\s+\d{5,7}", [
        "🔍 Show full diff for this changeset",
        "🎫 What Jira card is this changeset linked to?",
        "🏗️ Which build includes this changeset?",
    ]),
    (r"SBPWC-\d+|CSFMD-\d+|CSFP-\d+", [
        "🔗 Show linked changesets for this card",
        "📝 Kick off a code review for this card",
        "🏗️ What build fixed this card?",
    ]),
    (r"code review.*(done|ready|complete)", [
        "📋 Show the review findings",
        "🛡️ Any security issues flagged?",
        "🧪 Generate test scenarios from this review",
    ]),
    (r"build\s+\d{3,}", [
        "🎫 What cards are in this build?",
        "📊 Show build timeline and tasks",
        "❌ Any failed tests in this build?",
    ]),
    (r"build failure.*(done|ready|report)", [
        "🔬 What was the root cause?",
        "⛓️ Show the failure chain",
        "📈 Similar failures in recent builds?",
    ]),
    (r"latest build.*(\d+\.\d+)", [
        "📅 Show BOTW schedule for this version",
        "🔄 What changed since the previous build?",
        "📆 Show build calendar for this version",
    ]),
    (r"WorkView|workview", [
        "⚙️ How do I configure WorkView Filters?",
        "🌐 Show WorkView REST API endpoints",
        "📦 What attribute types are available?",
    ]),
    (r"pull request|PR #?\d+", [
        "📄 Show the PR diff summary",
        "💬 List review comments on this PR",
        "🛡️ Check code scanning alerts for this repo",
    ]),
    (r"shelveset", [
        "📝 Review this shelveset code",
        "🔀 Compare this shelveset with the base branch",
        "📂 What files are in this shelveset?",
    ]),
]

# Default follow-ups when no pattern matches — rotating pool to avoid repetition
_DEFAULT_FOLLOWUP_POOL = [
    "📚 Product guidance — *\"How do WorkView Filters work?\"*",
    "📖 Confluence assistant — *\"read page Sprint 42 Notes\"*",
    "🔍 Jira assistant — *\"read SBPWC-12345\"*",
    "📝 Kick off a code review — *\"code review SBPWC-12345\"*",
    "🏗️ Check the latest build — *\"latest build for 25.2\"*",
    "📅 See the BOTW schedule — *\"BOTW schedule for 25.2\"*",
    "🔀 Compare branches — *\"compare DEV and 25.1 for WorkViewUtility.cs\"*",
    "⚠️ Analyze a build failure — *\"analyze build failure 812151\"*",
    "📦 Check a package on ProGet — *\"check package Infragistics.WPF on ProGet\"*",
    "🐙 Create a GitHub PR — *\"create PR for SBPWC-12345\"*",
    "🛡️ Scan for vulnerabilities — *\"GHAS alerts for onbase-web-server\"*",
    "🎫 Look up Salesforce case — *\"Salesforce case 02114142\"*",
]

def _get_default_followups() -> list[str]:
    """Return 3 random defaults from the pool so they vary each time."""
    return random.sample(_DEFAULT_FOLLOWUP_POOL, min(3, len(_DEFAULT_FOLLOWUP_POOL)))


def get_suggested_followups(response_text: str, user_message: str = "") -> list[str]:
    """Get contextual follow-up suggestions based on the response content."""
    suggestions = []
    combined = (response_text + " " + user_message).lower()

    for pattern, followups in _FOLLOWUP_PATTERNS:
        if re.search(pattern, response_text, re.IGNORECASE) or re.search(pattern, user_message, re.IGNORECASE):
            suggestions.extend(followups)

    # Deduplicate and limit
    seen = set()
    unique = []
    for s in suggestions:
        key = s.lower()
        if key not in seen:
            seen.add(key)
            unique.append(s)

    if not unique:
        unique = _get_default_followups()

    return unique[:3]


def format_followups(followups: list[str]) -> str:
    """Format follow-up suggestions as a nice markdown block."""
    if not followups:
        return ""
    items = "\n".join(f"  → *{f}*" for f in followups)
    return f"\n\n---\n🔮 **Try next:**\n{items}"


# ── Welcome / Help Message ──

WELCOME_MESSAGE = """\
# 👋 Welcome to Agent ONE!

I'm your **AI-powered DevOps companion** for the OnBase codebase. Here's what I can do:

## 🔧 Core Skills

| Skill | Example | What It Does |
|---|---|---|
| 🔍 **Jira Analysis** | *analyze SBPWC-12345* | Fetches card details, linked changesets, and code diffs |
| 📝 **Code Review** | *code review SBPWC-12345* | Generates an AI-powered HTML code review report |
| 🏗️ **Build Queries** | *latest build for 25.2* | Checks BuildDirector for build status and history |
| ❌ **Failure Analysis** | *analyze build failure 812151* | AI root cause analysis with HTML report |
| 🔀 **Branch Compare** | *compare DEV and 25.1 for file.cs* | Side-by-side code comparison across branches |
| 🐙 **GitHub Ops** | *create PR for SBPWC-12345* | Creates branches, pushes files, opens PRs |
| 📚 **OnBase Docs** | *how to configure WorkView Filters?* | Queries the Module Reference Guide |
| ♿ **Accessibility** | *audit login page accessibility* | WCAG compliance checks and test generation |
| 📋 **Test Generation** | *generate test cases for SBPWC-12345* | Creates comprehensive test scenarios from Jira cards |
| 🏢 **Salesforce Cases** | *get support case 02114142* | Fetches customer support case details |

## ⚡ Quick Actions
Pick one to get started:
- 🔍 *\"What is the latest build for OnBase 25.2?\"*
- 📝 *\"Analyze SBPWC-12761\"*
- 🏗️ *\"Show BOTW schedule\"*
- 📚 *\"How does WorkView handle calculated attributes?\"*

---
💡 *Type **help** anytime to see this menu again.*
"""

SKILL_MENU = """\
## 🎯 Agent ONE — Skill Menu

### 🔍 Jira & Analysis
| Command | Description |
|---|---|
| `analyze <JIRA-KEY>` | Full Jira card analysis with linked changesets |
| `find open bugs in WorkView` | JQL-powered Jira search |
| `show linked cards for <JIRA-KEY>` | Related issues and epics |

### 📝 Code Review
| Command | Description |
|---|---|
| `code review <JIRA-KEY>` | AI-powered code review with HTML report |
| `review shelveset <name>` | Review a TFS shelveset |
| `compare DEV and 25.1 for <file>` | Cross-branch code comparison |

### 🏗️ Builds & Pipelines
| Command | Description |
|---|---|
| `latest build for <version>` | Latest successful build from BuildDirector |
| `show BOTW schedule` | Build Of The Week history |
| `build calendar for <version>` | Upcoming milestones and lockdowns |
| `what cards are in build <number>?` | Jira cards included in a build |
| `analyze build failure <build_id>` | AI root cause analysis for failed builds |

### 🐙 GitHub Operations
| Command | Description |
|---|---|
| `list PRs for <repo>` | Open pull requests |
| `create branch and PR for <JIRA-KEY>` | End-to-end PR creation |
| `code scanning alerts for <repo>` | Security vulnerability scan |

### 📚 Documentation & Knowledge
| Command | Description |
|---|---|
| `how to configure <feature>?` | OnBase Module Reference Guide lookup |
| `WorkView REST API for <endpoint>` | REST API documentation |
| `search Confluence for <topic>` | Internal engineering docs |

### ♿ Accessibility
| Command | Description |
|---|---|
| `accessibility audit for <scenario>` | WCAG compliance analysis |
| `generate accessibility test cases` | A11y test scenarios |

---
💡 *These are examples — I understand natural language, so ask in your own words!*
"""


# ── Response Enrichment ──

def is_welcome_message(user_message: str) -> bool:
    """Check if the user is greeting or asking for help."""
    msg = user_message.strip().lower()
    welcome_patterns = [
        r"^(hi|hello|hey|howdy|hola|greetings)\b",
        r"^(help|menu|skills|commands|what can you do|features|capabilities)\b",
        r"^(get started|start|intro|onboard)\b",
        r"^/?help\b",
        r"^/?skills\b",
        r"^/?menu\b",
    ]
    return any(re.match(p, msg) for p in welcome_patterns)


def is_skill_menu_request(user_message: str) -> bool:
    """Check if the user is asking for the skill/command menu."""
    msg = user_message.strip().lower()
    return any(re.match(p, msg) for p in [
        r"^/?skills\b",
        r"^/?commands\b",
        r"^(show|list)\s+(skills|commands|capabilities|features)",
        r"^what.*(skills|commands|can you do)",
    ])


def enrich_response(
    response_text: str,
    user_message: str,
    agent_used: str,
    conversation_id: str = "",
    tool_calls_made: int = 0,
    show_tip: bool = True,
    show_followups: bool = True,
) -> str:
    """Enrich the agent response with tips, follow-ups, and branding.

    This is the main post-processing function called from app.py.
    """
    parts = [response_text]

    # Add follow-up suggestions
    if show_followups and len(response_text) > 100:
        followups = get_suggested_followups(response_text, user_message)
        parts.append(format_followups(followups))

    # Add a tip (every 3rd response to avoid fatigue)
    if show_tip and _should_show_tip(conversation_id):
        tip = get_random_tip(conversation_id)
        parts.append(f"\n\n{tip}")

    return "".join(parts)


# ── Tip frequency control ──
_response_counts: dict[str, int] = {}
TIP_FREQUENCY = 3  # Show a tip every N responses


def _should_show_tip(conversation_id: str) -> bool:
    """Show a tip every TIP_FREQUENCY responses to avoid fatigue."""
    count = _response_counts.get(conversation_id, 0) + 1
    _response_counts[conversation_id] = count
    return count % TIP_FREQUENCY == 0  # tip on 3rd, 6th, 9th... response


# ── Conversation Stats ──
_conversation_starts: dict[str, float] = {}


def track_conversation_start(conversation_id: str):
    """Track when a conversation started (for analytics/display)."""
    if conversation_id not in _conversation_starts:
        _conversation_starts[conversation_id] = time.time()


def is_new_conversation(conversation_id: str) -> bool:
    """Check if this is a brand new conversation."""
    return conversation_id not in _conversation_starts


# ══════════════════════════════════════════════════════════════════════════════
# Suggested Actions (Quick Reply Buttons)
# ══════════════════════════════════════════════════════════════════════════════

# Standard quick-action categories shown to the user.
# "title" is the button label, "value" is the text sent when clicked.
STANDARD_ACTIONS: list[dict[str, str]] = [
    {"title": "📚 Product Guidance",          "value": "Product guidance"},
    {"title": "📖 Confluence Assistant",      "value": "Confluence assistant"},
    {"title": "🔍 Jira Assistant",            "value": "Jira assistant"},
    {"title": "📝 Code Review",               "value": "Code review"},
    {"title": "🏗️ Build Questions",           "value": "Build query"},
    {"title": "📅 BOTW Schedule",             "value": "BOTW schedule"},
    {"title": "🔀 Branch Compare",            "value": "Branch compare"},
    {"title": "⚠️ Build Failure",             "value": "Build failure analysis"},
    {"title": "📦 Package Check",             "value": "Package check on ProGet"},
    {"title": "🐙 GitHub PR",                 "value": "GitHub PR"},
    {"title": "🛡️ GHAS Alerts",               "value": "GHAS scan alerts"},
    {"title": "🎫 Salesforce Cases",          "value": "Salesforce cases"},
]


def get_suggested_actions(response_text: str, user_message: str) -> list[dict[str, str]]:
    """Return contextual suggested actions based on the response.

    For most responses, return the standard action set.
    For specific contexts, return more targeted actions.
    """
    msg = (response_text + " " + user_message).lower()

    # After a code review result, suggest related actions
    if re.search(r"code review.*(complete|done|ready|report)", msg):
        return [
            {"title": "📝 Another Code Review",       "value": "Code review"},
            {"title": "🔍 Jira Assistant",            "value": "Jira assistant"},
            {"title": "🏗️ Build Questions",           "value": "Build query"},
        ]

    # After build info, suggest related actions
    if re.search(r"build schedule|botw|lockdown|build of the week", msg):
        return [
            {"title": "🏗️ More Build Questions",      "value": "Build query"},
            {"title": "⚠️ Build Failure",             "value": "Build failure analysis"},
            {"title": "📝 Code Review",               "value": "Code review"},
        ]

    # Default: return the full standard set
    return STANDARD_ACTIONS


# ══════════════════════════════════════════════════════════════════════════════
# Bare-Command Prompting (two-step input collection)
# ══════════════════════════════════════════════════════════════════════════════

# Pending action state per conversation.
# Stores: { conversation_id: {"action": "code_review", "prompt": "...", "ts": ...} }
_pending_actions: dict[str, dict] = {}
_PENDING_EXPIRY = 120  # seconds — pending action prompt expires after this

# Bare-command patterns: (regex, action_key, prompt_text, example)
_BARE_COMMANDS: list[tuple[re.Pattern, str, str, str]] = [
    (
        re.compile(r"^product\s*guidance$", re.IGNORECASE),
        "product_guidance",
        "What OnBase topic do you need guidance on? I'll check the MRG, REST API SDK, Confluence, and Salesforce.",
        "WorkView Filters, Workflow Actions, Unity Forms REST API",
    ),
    (
        re.compile(r"^confluence\s*assistant$", re.IGNORECASE),
        "confluence_assistant",
        "What would you like to do with Confluence? Read, create, or update a page?",
        "read page Sprint 42 Notes, create page in WV space, update page with new section",
    ),
    (
        re.compile(r"^jira\s*assistant$", re.IGNORECASE),
        "jira_assistant",
        "What would you like to do with Jira? Read, create, or update a card?",
        "read SBPWC-12345, create card in SBPWC, update SBPWC-12345 status",
    ),
    (
        re.compile(r"^code\s*review$", re.IGNORECASE),
        "code_review",
        "Which Jira card should I review? Drop the card number below.",
        "SBPWC-12345",
    ),
    (
        re.compile(r"^build\s*(?:query|questions?)$", re.IGNORECASE),
        "build_query",
        "Which OnBase version do you want build info for?",
        "25.2, DEV, 24.1",
    ),
    (
        re.compile(r"^botw\s*(?:schedule|dates?)?$", re.IGNORECASE),
        "botw_schedule",
        "Which OnBase version's BOTW schedule do you need?",
        "25.2, DEV, 24.1",
    ),
    (
        re.compile(r"^branch\s*compare$", re.IGNORECASE),
        "branch_compare",
        "What would you like to compare? Provide the file path and branches.",
        "compare DEV and 25.1 for WorkViewUtility.cs",
    ),
    (
        re.compile(r"^build\s*failure\s*(?:analysis)?$", re.IGNORECASE),
        "build_failure",
        "Which build should I analyze? Provide version or BuildDirector ID.",
        "26.1.0.441 or build ID 812151",
    ),
    (
        re.compile(r"^package\s*check\s*(?:on\s*proget)?$", re.IGNORECASE),
        "package_check",
        "Which NuGet/npm package should I look up on ProGet?",
        "Infragistics.WPF or @hyland/ui",
    ),
    (
        re.compile(r"^github\s*pr$", re.IGNORECASE),
        "github_pr",
        "Which Jira card or GitHub repo? I'll create a fresh branch and submit the PR.",
        "create PR for SBPWC-12345, or list PRs for onbase-web-server",
    ),
    (
        re.compile(r"^ghas\s*(?:scan)?\s*alerts?$", re.IGNORECASE),
        "ghas_alerts",
        "Which GitHub repository should I check for security alerts?",
        "onbase-web-server or SBP-WV-onbase-web-server-ng-apps",
    ),
    (
        re.compile(r"^salesforce\s*cases?$", re.IGNORECASE),
        "salesforce_cases",
        "Which Salesforce support case should I look up? Provide a case number or Jira card with Support Issue ID.",
        "02114142 or SBPWC-12345",
    ),
]

# Maps action_key → template for combining with user's parameter
_ACTION_TEMPLATES: dict[str, str] = {
    "product_guidance":   "{input}",
    "confluence_assistant": "{input}",
    "jira_assistant":     "{input}",
    "code_review":        "code review {input}",
    "build_query":        "latest build for {input}",
    "build_failure":      "analyze build failure {input}",
    "botw_schedule":      "show BOTW schedule for {input}",
    "branch_compare":     "{input}",
    "package_check":      "check package {input} on ProGet",
    "github_pr":          "{input}",
    "ghas_alerts":        "list code scanning alerts for {input}",
    "salesforce_cases":   "look up Salesforce support case {input}",
}


def check_bare_command(message: str, conversation_id: str) -> str | None:
    """Check if the message is a bare command that needs a parameter.

    Returns a prompt string if the message matches a bare command,
    or None if it should be processed normally.
    """
    msg = message.strip()
    for pattern, action_key, prompt_text, example in _BARE_COMMANDS:
        if pattern.match(msg):
            _pending_actions[conversation_id] = {
                "action": action_key,
                "prompt": prompt_text,
                "ts": time.time(),
            }
            return (
                f"**{msg}** — got it! 👍\n\n"
                f"{prompt_text}\n\n"
                f"*Examples: {example}*"
            )
    return None


def check_pending_action(message: str, conversation_id: str) -> str | None:
    """Check if there's a pending action awaiting parameter input.

    If yes, combines the pending action with the user's input and returns
    the full command string to be processed. Clears the pending state.
    Returns None if no pending action exists or it expired.
    """
    pending = _pending_actions.get(conversation_id)
    if not pending:
        return None

    # Check expiry
    if time.time() - pending["ts"] > _PENDING_EXPIRY:
        _pending_actions.pop(conversation_id, None)
        return None

    action_key = pending["action"]
    _pending_actions.pop(conversation_id, None)

    template = _ACTION_TEMPLATES.get(action_key, "{input}")
    return template.format(input=message.strip())
