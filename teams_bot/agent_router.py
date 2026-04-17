"""
Agent router — classifies user messages and selects the appropriate agent.

Uses heuristic fast-path for obvious intents, falls back to LLM for ambiguous queries.
"""

import re
from openai import OpenAI
from config import GITHUB_TOKEN, LLM_BASE_URL, LLM_MODEL_MINI

# Import agent metadata
from agents import tfs_jira, onbase, mrg_parser, accessibility

AGENTS = {
    tfs_jira.AGENT_NAME: {"description": tfs_jira.DESCRIPTION, "module": tfs_jira},
    onbase.AGENT_NAME: {"description": onbase.DESCRIPTION, "module": onbase},
    mrg_parser.AGENT_NAME: {"description": mrg_parser.DESCRIPTION, "module": mrg_parser},
    accessibility.AGENT_NAME: {"description": accessibility.DESCRIPTION, "module": accessibility},
}

# Compiled regex patterns for fast-path routing
_JIRA_KEY = re.compile(r"\b[A-Z]{2,10}-\d{3,6}\b")
_TFS_KEYWORDS = re.compile(
    r"\b(changeset|shelveset|code review|build fail|pipeline|build \d|"
    r"pr #?\d|pull request|github pr|code diff|branch diff|lof analysis|"
    r"backport|cherrypick|merge conflict|"
    r"latest build|builddirector|build director|build info|build number|"
    r"onbase build|onbase \d+\.\d+\.\d+|what build|which build|"
    r"build of the week|botw|service build|build status|build version|"
    r"build calendar|build schedule|publish date|lockdown date|"
    r"fixed in.{0,10}build|cards in build|jira.{0,10}build|build.{0,10}jira|"
    r"proget|package.{0,10}version|package.{0,10}check|nuget|"
    r"jenkins|jenkins.{0,10}pipeline|jenkins.{0,10}fail|csp\.jenkins|"
    r"compare.{0,10}branch|compare.{0,10}dev|compare.{0,10}\d{2}\.\d|"
    r"branch.{0,10}diff|branch.{0,10}compare)\b", re.IGNORECASE
)
_MRG_KEYWORDS = re.compile(
    r"\b(mrg|module reference|wcag|accessibility guideline|onbase documentation|"
    r"rest api sdk|configuration guide|how to configure|what is .{3,30} in onbase|"
    r"product operating model|hpom)\b", re.IGNORECASE
)
_A11Y_KEYWORDS = re.compile(
    r"\b(accessibility audit|a11y|wcag compliance|screen reader|keyboard nav|"
    r"aria|contrast ratio|alt text|focus management|section 508)\b", re.IGNORECASE
)


def route(message: str) -> str:
    """Classify a user message and return the agent name to handle it.

    Returns one of: 'tfs_jira', 'onbase', 'mrg_parser', 'accessibility'
    """
    # --- Fast-path heuristics (no LLM call) ---

    # Accessibility keywords are specific enough to route directly
    if _A11Y_KEYWORDS.search(message):
        return "accessibility"

    # MRG / documentation queries
    if _MRG_KEYWORDS.search(message):
        return "mrg_parser"

    # Jira key present → TFS/Jira analysis
    if _JIRA_KEY.search(message):
        return "tfs_jira"

    # TFS/build/GitHub keywords
    if _TFS_KEYWORDS.search(message):
        return "tfs_jira"

    # --- LLM fallback for ambiguous queries ---
    return _llm_route(message)


def _llm_route(message: str) -> str:
    """Use a lightweight LLM call to classify the query."""
    agent_descriptions = "\n".join(
        f"- **{name}**: {info['description']}" for name, info in AGENTS.items()
    )

    client = OpenAI(base_url=LLM_BASE_URL, api_key=GITHUB_TOKEN)
    response = client.chat.completions.create(
        model=LLM_MODEL_MINI,
        temperature=0,
        max_tokens=50,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a query classifier. Given a user message, respond with ONLY the agent name "
                    "that should handle it. Choose from: tfs_jira, onbase, mrg_parser, accessibility.\n\n"
                    f"Agent descriptions:\n{agent_descriptions}\n\n"
                    "Default to 'onbase' if uncertain. Respond with ONLY the agent name, nothing else."
                ),
            },
            {"role": "user", "content": message},
        ],
    )

    agent_name = response.choices[0].message.content.strip().lower()
    # Validate
    if agent_name in AGENTS:
        return agent_name
    return "onbase"  # safe fallback


def get_agent_module(agent_name: str):
    """Return the agent module for a given agent name."""
    return AGENTS[agent_name]["module"]
