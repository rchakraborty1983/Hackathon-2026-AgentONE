# Copilot Studio — Quick Reply Buttons Setup Guide

## Overview

The AgentONE server now returns `suggested_actions` in every HTTP response. These are quick-reply button definitions that Copilot Studio can render as clickable suggestion pills (like "Approved", "Thanks", "Ok" in Teams).

## What the Server Returns

Every `/chat` response now includes:

```json
{
  "response": "...main text...",
  "agent_used": "tfs_jira",
  "conversation_id": "...",
  "tool_calls_made": 1,
  "suggested_actions": [
    {"title": "📋 Code Review", "value": "Code review"},
    {"title": "🔍 Analyze Jira Card", "value": "Analyze Jira card"},
    {"title": "🏗️ Build Query", "value": "Build query"},
    {"title": "⚠️ Build Failure Analysis", "value": "Build failure analysis"},
    {"title": "📚 Knowledge Query", "value": "Knowledge query"},
    {"title": "📅 BOTW Schedule", "value": "BOTW schedule"},
    {"title": "🔀 Branch Compare", "value": "Branch compare"},
    {"title": "🐙 GitHub PR", "value": "GitHub PR"}
  ]
}
```

When a user clicks a button (e.g., "📋 Code Review"), the `value` text ("Code review") is sent as the next message. The server handles the rest — it prompts for any missing parameters (e.g., "Which Jira card?").

## Copilot Studio Configuration Steps

### Step 1: Update the HTTP Action Response Schema

1. Open your Copilot Studio agent → **Topics** → find the topic that calls the **"Chat with OnBase DevOps Agent"** HTTP action
2. Click on the HTTP action node
3. In the **Response** section, update the JSON schema to include `suggested_actions`:

```json
{
  "type": "object",
  "properties": {
    "response": { "type": "string" },
    "agent_used": { "type": "string" },
    "conversation_id": { "type": "string" },
    "tool_calls_made": { "type": "integer" },
    "suggested_actions": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "title": { "type": "string" },
          "value": { "type": "string" }
        }
      }
    }
  }
}
```

4. Save. Now `Topic.HTTPResponse.suggested_actions` is available as a variable.

### Step 2: Add Quick Reply Buttons After the Response Message

1. After the HTTP action node, you should have a **Message** node that displays `{Topic.HTTPResponse.response}`
2. Click on the Message node → expand **Options** → **Quick replies**
3. You have two approaches:

#### Option A: Static Quick Replies (Simplest)

Add these as static quick reply options:
- `📋 Code Review`
- `🔍 Analyze Jira Card`
- `🏗️ Build Query`
- `⚠️ Build Failure Analysis`
- `📚 Knowledge Query`
- `📅 BOTW Schedule`

When a user clicks one, it sends that text as their next message.

#### Option B: Dynamic Quick Replies from Server Response (Advanced)

Use a **Power Fx** expression to set quick replies dynamically from the `suggested_actions` array:

1. In the Quick replies section, use **Formula** mode
2. Set: `ForAll(Topic.HTTPResponse.suggested_actions, ThisRecord.title)`
3. This dynamically populates buttons from whatever the server returns

### Step 3: Handle the Two-Step Flow

When a user clicks a quick reply (e.g., "📋 Code Review"), the server recognizes it as a bare command and responds with:

```
Code review — got it! 👍

Which Jira card should I review? Please provide the card number.

Examples: SBPWC-12345
```

The user then types the card number. The server combines it into the full command ("code review SBPWC-12345") automatically via `conversation_id`. **No extra Copilot Studio configuration needed** — just ensure the conversation loops back to the HTTP action.

### Step 4: Ensure Conversation Loop

The topic flow should loop:

```
User message → HTTP Action → Show Response + Quick Replies → User clicks/types → HTTP Action → ...
```

In Copilot Studio:
1. After the Message node with Quick Replies, the topic should **redirect** back to the beginning (the HTTP action call)
2. This ensures continuous conversation with the agent
3. The `conversation_id` parameter should persist across the loop

### Step 5: Republish

1. **Paste the updated instructions** from `copilot_studio_instructions.txt` into the agent's Instructions field
2. **Save** and **Publish** the agent

## How the Two-Step Flow Works (Server Side)

The server handles parameterized commands in two steps:

| Step | User Sends | Server Does | Response |
|------|-----------|------------|----------|
| 1 | "Code review" | Stores pending action `code_review` | "Which Jira card?" |
| 2 | "SBPWC-10443" | Combines: `code review SBPWC-10443` | Starts code review |

Supported bare commands:
| Button | Prompts For | Combined Command |
|--------|------------|-----------------|
| Code Review | Jira card # | `code review {key}` |
| Analyze Jira Card | Jira card # | `analyze {key}` |
| Build Query | Version | `latest build for {version}` |
| Build Failure Analysis | Build ID | `analyze build failure {id}` |
| Knowledge Query | Topic | `{topic}` (direct query) |
| BOTW Schedule | Version | `show BOTW schedule for {version}` |
| Branch Compare | Details | `{details}` (direct query) |
| GitHub PR | Details | `{details}` (direct query) |

The pending action expires after 120 seconds. If the user types something else or the timeout passes, the pending state is cleared.

## Testing

Test the flow locally before publishing:

```powershell
# Step 1: Send bare command
$body = '{"message":"Code review","conversation_id":"test-1"}'
Invoke-RestMethod -Uri "http://localhost:9100/chat" -Method POST -Body $body -ContentType "application/json" -Headers @{"x-api-key"="..."}
# Response: "Which Jira card should I review?"

# Step 2: Send parameter (same conversation_id!)
$body = '{"message":"SBPWC-10443","conversation_id":"test-1"}'
Invoke-RestMethod -Uri "http://localhost:9100/chat" -Method POST -Body $body -ContentType "application/json" -Headers @{"x-api-key"="..."}
# Response: "Code review for SBPWC-10443 has been successfully initiated..."
```
