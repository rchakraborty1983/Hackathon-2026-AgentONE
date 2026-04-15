"""
Agent executor — runs the tool-calling loop between the LLM and TFSMCP.py functions.

Sends the agent's system prompt + conversation history + tool definitions to the LLM,
executes any tool calls the LLM requests, and loops until a final text response.

Token budget management:
  GitHub Models free tier has an 8000 INPUT token limit for ALL models (gpt-4.1, gpt-4o, etc.).
  Tool schemas alone consumed ~3700 tokens (42 tools).
  Fix: Dynamic tool selection reduces to ~800 tokens (5-10 tools).
  Uses gpt-4.1-mini for the tool loop (faster, same limits).
  Aggressive result truncation + history compression between iterations.

Timing budget:
  Copilot Studio connector ACTUALLY times out at ~30 seconds (despite displaying "240000 seconds").
  We target total execution under 25 seconds.
  Per-tool-call timeout: 12 seconds (prevents TFS API slow responses from blocking).
  If total elapsed > 18s, force the LLM to synthesize an answer without more tool calls.
"""

import json
import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from openai import OpenAI, APIStatusError
from config import GITHUB_TOKEN, LLM_BASE_URL, LLM_MODEL_MINI, LLM_MAX_TOKENS, LLM_TEMPERATURE, MAX_TOOL_ITERATIONS
from tool_registry import get_tools_for_query, get_tool_callable

logger = logging.getLogger("agentone.executor")

# ── Token budget constants ──
# GitHub Models free tier: 8000 input tokens for ALL models
# With dynamic tool selection (~800 tokens for 5-10 tools):
#   System prompt: ~750 tokens (3000 chars)
#   Tool schemas: ~800 tokens (5-10 tools)
#   Overhead: ~200 tokens (message framing)
#   Available for content: ~6250 tokens = ~25000 chars
INPUT_TOKEN_LIMIT = 8000
TOOL_SCHEMA_BUDGET = 1000  # expected tokens for dynamically selected tools
SYSTEM_PROMPT_BUDGET = 750
OVERHEAD = 250
AVAILABLE_TOKENS = INPUT_TOKEN_LIMIT - TOOL_SCHEMA_BUDGET - SYSTEM_PROMPT_BUDGET - OVERHEAD  # ~6000
CHARS_PER_TOKEN = 4
MAX_CONTENT_CHARS = AVAILABLE_TOKENS * CHARS_PER_TOKEN  # ~24000 chars

# Per-result caps
MAX_TOOL_RESULT_CHARS = 3000   # ~750 tokens per tool result (was 6000)
MAX_SYSTEM_PROMPT_CHARS = 3000  # ~750 tokens

# History compression: after each iteration, old tool results shrink to this
COMPRESSED_RESULT_CHARS = 800  # ~200 tokens per old result

# Rate limit retry
MAX_429_RETRIES = 2
RETRY_BACKOFF_BASE = 5  # seconds

# ── Timing budget constants ──
# Copilot Studio connector actually times out at ~30 seconds (not 240s as displayed).
# We must return a response within ~25 seconds from request start.
TOOL_CALL_TIMEOUT = 12     # max seconds per individual tool call
TOTAL_TIME_BUDGET = 22     # after this many seconds, stop calling tools and synthesize
FORCE_ANSWER_BUDGET = 17   # if elapsed > this, tell LLM to prefer answering over calling tools

# Heavy tools removed — code reviews are now async (start_code_review returns in ~1s)
# _HEAVY_TOOL_TIMEOUT and _HEAVY_TOOLS no longer needed

# Thread pool for tool call timeouts
_tool_executor = ThreadPoolExecutor(max_workers=2)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    if not text:
        return 0
    return len(text) // CHARS_PER_TOKEN + 1


def _estimate_messages_tokens(messages: list) -> int:
    """Estimate total tokens across all messages."""
    total = 0
    for msg in messages:
        if isinstance(msg, dict):
            content = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls")
        else:
            content = getattr(msg, "content", "") or ""
            tool_calls = getattr(msg, "tool_calls", None)
        if isinstance(content, str):
            total += _estimate_tokens(content)
        if tool_calls:
            total += len(tool_calls) * 50  # tool call overhead
    return total


def _truncate_system_prompt(prompt: str) -> str:
    """Truncate system prompt to fit within budget."""
    if len(prompt) <= MAX_SYSTEM_PROMPT_CHARS:
        return prompt
    logger.info(f"Truncating system prompt from {len(prompt)} to {MAX_SYSTEM_PROMPT_CHARS} chars")
    return prompt[:MAX_SYSTEM_PROMPT_CHARS] + "\n[System prompt truncated]"


def _truncate_tool_result(result: str, fn_name: str) -> str:
    """Truncate a tool result to fit within per-result budget."""
    limit = MAX_TOOL_RESULT_CHARS
    if len(result) <= limit:
        return result
    logger.info(f"Truncating {fn_name} result from {len(result)} to {limit} chars")
    return result[:limit] + f"\n[truncated from {len(result)} chars]"


def _compress_old_tool_results(messages: list) -> list:
    """Compress older tool results to save tokens for the next iteration.

    Keeps the most recent tool result at full size, compresses older ones.
    This prevents token accumulation across multi-tool-call iterations.
    """
    # Find all tool-result message indices
    tool_indices = [i for i, m in enumerate(messages)
                    if isinstance(m, dict) and m.get("role") == "tool"]

    if len(tool_indices) <= 1:
        return messages  # nothing to compress

    # Compress all except the last tool result
    for idx in tool_indices[:-1]:
        content = messages[idx].get("content", "")
        if isinstance(content, str) and len(content) > COMPRESSED_RESULT_CHARS:
            messages[idx] = {
                **messages[idx],
                "content": content[:COMPRESSED_RESULT_CHARS] + "\n[compressed — older result]",
            }

    return messages


def _trim_messages_to_budget(messages: list) -> list:
    """Trim messages to stay under the token budget."""
    est = _estimate_messages_tokens(messages)
    if est <= AVAILABLE_TOKENS:
        return messages

    logger.warning(f"Messages estimated at {est} tokens (limit {AVAILABLE_TOKENS}), trimming...")

    # Step 1: Compress all tool results aggressively
    for i, msg in enumerate(messages):
        if isinstance(msg, dict) and msg.get("role") == "tool":
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > 1500:
                messages[i] = {**msg, "content": content[:1500] + "\n[trimmed to fit budget]"}

    est = _estimate_messages_tokens(messages)
    if est <= AVAILABLE_TOKENS:
        return messages

    # Step 2: Drop old history, keep system + last 4 messages
    if len(messages) > 5:
        kept = [messages[0]] + messages[-4:]
        logger.warning(f"Dropped {len(messages) - 5} older messages to fit budget")
        return kept

    return messages


def _call_llm_with_retry(client, model, messages, tools, temperature, max_tokens):
    """Call the LLM with 429 rate-limit retry and exponential backoff."""
    retries_429 = 0
    while True:
        try:
            return client.chat.completions.create(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                messages=messages,
                tools=tools if tools else None,
            )
        except APIStatusError as e:
            if e.status_code == 429 and retries_429 < MAX_429_RETRIES:
                retries_429 += 1
                wait = RETRY_BACKOFF_BASE * (2 ** (retries_429 - 1))  # 5s, 10s
                logger.warning(f"429 rate limit hit, waiting {wait}s (retry {retries_429}/{MAX_429_RETRIES})")
                time.sleep(wait)
            else:
                raise


def execute(
    agent_name: str,
    system_prompt: str,
    user_message: str,
    conversation_history: list[dict] | None = None,
) -> dict:
    """Run the agent executor loop.

    Uses gpt-4.1-mini for the tool-calling loop (faster, same token limits).
    Dynamic tool selection keeps schema tokens low.
    Enforces per-tool and total timing budgets.
    """
    start_time = time.monotonic()
    client = OpenAI(base_url=LLM_BASE_URL, api_key=GITHUB_TOKEN, max_retries=0)

    # Dynamic tool selection — 5-10 tools instead of 42
    tools = get_tools_for_query(agent_name, user_message)
    tools_token_est = sum(len(json.dumps(t)) for t in tools) // CHARS_PER_TOKEN
    logger.info(f"Tool schema tokens: ~{tools_token_est} ({len(tools)} tools)")

    # Truncate system prompt
    trimmed_system = _truncate_system_prompt(system_prompt)

    # Build message list
    messages = [{"role": "system", "content": trimmed_system}]
    if conversation_history:
        # Only keep last 2 exchanges from history to save tokens
        messages.extend(conversation_history[-4:])
    messages.append({"role": "user", "content": user_message})

    total_tool_calls = 0

    for iteration in range(MAX_TOOL_ITERATIONS):
        elapsed = time.monotonic() - start_time

        # Time budget check — if we're running out of time, force a final answer
        if elapsed > TOTAL_TIME_BUDGET:
            logger.warning(f"Total time budget exceeded ({elapsed:.1f}s > {TOTAL_TIME_BUDGET}s), "
                          f"forcing final answer")
            messages.append({"role": "user", "content":
                "[SYSTEM: Time limit reached. Provide your best answer NOW based on the data "
                "you already have. Do NOT call any more tools.]"})
            try:
                response = _call_llm_with_retry(
                    client, LLM_MODEL_MINI, messages, None,  # No tools = force text answer
                    LLM_TEMPERATURE, LLM_MAX_TOKENS,
                )
                return {
                    "response": response.choices[0].message.content or
                                "I gathered some data but ran out of time to fully analyze it. "
                                "Please try a more specific question.",
                    "agent_used": agent_name,
                    "tool_calls_made": total_tool_calls,
                }
            except Exception:
                return {
                    "response": "The request took too long. Please try a more specific question.",
                    "agent_used": agent_name,
                    "tool_calls_made": total_tool_calls,
                }

        # Compress old tool results + trim to budget
        messages = _compress_old_tool_results(messages)
        messages = _trim_messages_to_budget(messages)
        est_tokens = _estimate_messages_tokens(messages) + tools_token_est
        logger.info(f"Iter {iteration + 1}/{MAX_TOOL_ITERATIONS}, msgs={len(messages)}, "
                     f"est_tokens={est_tokens}, elapsed={elapsed:.1f}s")

        # If running low on time, tell LLM to prefer answering over calling tools
        effective_tools = tools
        if elapsed > FORCE_ANSWER_BUDGET:
            logger.info(f"Approaching time limit ({elapsed:.1f}s), hinting LLM to answer directly")
            # Add a nudge but still allow tool calls as last resort
            messages_for_call = messages.copy()
            messages_for_call.append({"role": "user", "content":
                "[SYSTEM: Running low on time. Provide your answer now if possible, "
                "or make only ONE quick tool call.]"})
        else:
            messages_for_call = messages

        try:
            response = _call_llm_with_retry(
                client, LLM_MODEL_MINI, messages_for_call, effective_tools,
                LLM_TEMPERATURE, LLM_MAX_TOKENS,
            )
        except APIStatusError as e:
            if e.status_code == 413:
                logger.warning(f"413 at iteration {iteration + 1}, resetting to minimal context")
                messages = [
                    {"role": "system", "content": trimmed_system},
                    {"role": "user", "content": user_message +
                     "\n\n[Previous tool calls exceeded token limit. Provide a concise answer "
                     "or call only the single most important tool.]"},
                ]
                try:
                    response = _call_llm_with_retry(
                        client, LLM_MODEL_MINI, messages, tools,
                        LLM_TEMPERATURE, LLM_MAX_TOKENS,
                    )
                except APIStatusError:
                    return {
                        "response": "The request exceeded the AI model's token limit. "
                                    "Please try a shorter or more specific question.",
                        "agent_used": agent_name,
                        "tool_calls_made": total_tool_calls,
                    }
            elif e.status_code == 429:
                # If we already have tool results, synthesize an answer from them
                # instead of returning a generic "rate-limited" message
                last_tool_content = None
                for msg in reversed(messages):
                    if isinstance(msg, dict) and msg.get("role") == "tool":
                        last_tool_content = msg.get("content", "")
                        break
                if last_tool_content and total_tool_calls > 0:
                    logger.warning(f"429 after {total_tool_calls} tool calls; synthesizing from last result")
                    # Parse the tool result and present it directly
                    try:
                        tool_data = json.loads(last_tool_content)
                        summary_parts = []
                        if "message" in tool_data:
                            summary_parts.append(tool_data["message"])
                        if "report_url_hint" in tool_data:
                            summary_parts.append(tool_data["report_url_hint"])
                        if summary_parts:
                            return {
                                "response": " ".join(summary_parts),
                                "agent_used": agent_name,
                                "tool_calls_made": total_tool_calls,
                            }
                    except (json.JSONDecodeError, TypeError):
                        pass
                    return {
                        "response": f"Here are the results from the last tool call:\n\n{last_tool_content[:2000]}",
                        "agent_used": agent_name,
                        "tool_calls_made": total_tool_calls,
                    }
                return {
                    "response": "The AI service is temporarily rate-limited. "
                                "Please wait a minute and try again.",
                    "agent_used": agent_name,
                    "tool_calls_made": total_tool_calls,
                }
            else:
                raise

        choice = response.choices[0]

        # Final text response — done
        if choice.finish_reason == "stop" or not choice.message.tool_calls:
            return {
                "response": choice.message.content or "",
                "agent_used": agent_name,
                "tool_calls_made": total_tool_calls,
            }

        # Process tool calls
        assistant_msg = choice.message
        messages.append(assistant_msg)

        for tool_call in assistant_msg.tool_calls:
            fn_name = tool_call.function.name
            total_tool_calls += 1

            try:
                fn_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                fn_args = {}

            logger.info(f"Tool call: {fn_name}({fn_args})")

            # Execute tool with timeout (heavy tools get extended timeout)
            try:
                func = get_tool_callable(fn_name)
                future = _tool_executor.submit(func, **fn_args)
                try:
                    result = future.result(timeout=TOOL_CALL_TIMEOUT)
                except FuturesTimeoutError:
                    logger.warning(f"Tool {fn_name} timed out after {TOOL_CALL_TIMEOUT}s")
                    result = json.dumps({
                        "timeout": True,
                        "message": f"Tool '{fn_name}' took longer than {TOOL_CALL_TIMEOUT}s. "
                                   "The data source is slow. Please provide your best answer "
                                   "based on what you already know, or suggest the user try again.",
                    })

                if not isinstance(result, str):
                    result = json.dumps(result, indent=2, default=str)

                # Truncate to per-result budget
                result = _truncate_tool_result(result, fn_name)

            except FuturesTimeoutError:
                pass  # already handled above
            except Exception as e:
                logger.error(f"Tool {fn_name} failed: {e}")
                result = json.dumps({"error": str(e), "traceback": traceback.format_exc()[-300:]})

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

    # Max iterations reached
    return {
        "response": f"Reached maximum iterations ({MAX_TOOL_ITERATIONS}). "
                    f"Completed {total_tool_calls} tool calls. Please try a more specific question.",
        "agent_used": agent_name,
        "tool_calls_made": total_tool_calls,
    }
