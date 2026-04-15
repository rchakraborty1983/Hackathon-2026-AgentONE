"""AgentONE configuration — centralized environment and settings."""

import os
import secrets

# --- LLM (GitHub Models API) ---
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
LLM_BASE_URL = "https://models.inference.ai.azure.com"
LLM_MODEL = "gpt-4.1"
LLM_MODEL_MINI = "gpt-4.1-mini"  # Used for lightweight routing
LLM_MAX_TOKENS = 4096
LLM_TEMPERATURE = 0.2

# --- Agent API server ---
API_HOST = os.getenv("AGENTONE_HOST", "0.0.0.0")
API_PORT = int(os.getenv("AGENTONE_PORT", "9100"))
API_KEY = os.getenv("AGENTONE_API_KEY", secrets.token_urlsafe(32))

# --- Public base URL (for report links served via dev tunnel) ---
# Set AGENTONE_PUBLIC_URL env var to override; defaults to the dev tunnel URL.
PUBLIC_BASE_URL = os.getenv(
    "AGENTONE_PUBLIC_URL",
    "https://px2wkg66-9100.use.devtunnels.ms",
).rstrip("/")

# --- Conversation ---
MAX_HISTORY_MESSAGES = 20
MAX_TOOL_ITERATIONS = 10
TOOL_CALL_TIMEOUT = 120  # seconds
