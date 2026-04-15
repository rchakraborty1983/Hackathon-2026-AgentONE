# BUILD_SKILL — OnBase Build Intelligence

> **Purpose**: Comprehensive knowledge and workflows for answering questions about OnBase builds, BOTW cycles, build schedules, and Jira ↔ Build correlation.
>
> **Used by**: `tfs_jira` and `onbase` agents when handling build-related queries.

---

## Data Sources

| Source | Authority | Tools |
|---|---|---|
| **BuildDirector** (`http://qa-websrvr/BuildDirector`) | Authoritative for build artifacts, versions, BOTW schedule | `bd_*` tools |
| **Jira** (`https://hyland.atlassian.net`) | Authoritative for which builds a card was fixed in | `jira_fixed_in_build`, `jira_cards_in_build`, `search_jira` |
| **TFS Pipelines** | CI/CD pipeline execution logs (NOT product builds) | `get_builds`, `get_build_info` |

**Key distinction**: BuildDirector tracks **OnBase product builds** (official releases, BOTWs). TFS Pipelines track **CI/CD executions** (compile, test, deploy). Always use BuildDirector tools for product build questions.

---

## Concepts

### Build Numbering Format

OnBase builds use a **4-part version**: `{major}.{minor}.{servicePack}.{buildNumber}`

Examples:
- `25.2.12.1000` → version 25.2, service pack 12, build number 1000
- `26.1.0.441` → version 26.1, service pack 0, build number 441
- `24.1.38.500` → version 24.1, service pack 38, build number 500

### Build Of The Week (BOTW)

- A BOTW build always has **build_number = 1000**
- Generated weekly on a fixed schedule per version
- The BOTW cycle follows this pattern:
  1. **Lockdown** (type 0, red) — code freeze period, no checkins
  2. **Build of the Week** (type 1, green) — the BOTW is generated
  3. **Smoke Test** (type 3, blue) — QA validates the build
  4. **Publish Build** (type 3, blue) — build is published for broader use
- Some weeks have **No Build of the Week** (type 2, gray) — holidays or planned gaps

### Version Aliases

| User Says | Resolves To |
|---|---|
| DEV | 26.1 |
| 24, 24.1 | 24.1 |
| 25, 25.1 | 25.1 |
| 25.2 | 25.2 |
| 26, 26.1 | 26.1 |

### Fixed In Build (Jira field)

- Jira field: `customfield_10542` (Short text)
- Contains comma-separated build numbers, e.g. `"26.1.0.441, 26.1.0.434"`
- Manually entered by developers when checking in fixes
- A single card can be fixed in multiple builds (different service releases)

---

## Query Workflows

### Q: "What is the latest build for OnBase 24.1?"

**Tool**: `bd_get_latest_build(version="24.1")`

Returns the most recent successful (complete, non-failed) build.

### Q: "What is the latest BOTW for 25.2?" / "Last BOTW version for 25.2?"

**Tool**: `bd_get_botw_builds(version="25.2", top=1)`

Finds the most recent build with build_number=1000.

Respond with: "The latest BOTW for 25.2 is **{version}**, generated on {date}, status: {status}."

### Q: "When is the next BOTW for 25.1?" / "Next BOTW generation and publish dates?"

**Tool**: `bd_get_build_calendar(version="25.1")`

The response includes:
- `schedule_summary.next_botw_generation` — when the next BOTW will be generated
- `schedule_summary.next_lockdown_start` / `next_lockdown_end` — the lockdown period before BOTW
- `schedule_summary.next_publish_date` — when the BOTW will be published

Respond with:
```
Next BOTW cycle for 25.1:
- Lockdown: {lockdown_start} to {lockdown_end}
- BOTW Generation: {generation_date}
- Publish: {publish_date}
```

### Q: "SBPWC-12657 is fixed in which build?"

**Tool**: `jira_fixed_in_build(jira_key="SBPWC-12657")`

Returns the Fixed In Build field value and parsed build numbers.

Respond with: "{jira_key} ({summary}) is fixed in build(s): **{builds}**"

Optionally follow up with `bd_search_build` for each build to show their status and BuildDirector URL.

### Q: "26.1.0.439 includes which Jira cards?" / "What was fixed in build 26.1.0.439?"

**Tool**: `jira_cards_in_build(build_number="26.1.0.439")`

Runs JQL: `"Fixed In Build[Short text]" ~ "\"26.1.0.439\"" AND NOT project = Documentation`

Respond with a table of cards: key, summary, status, priority, assignee.

### Q: "Show me the build schedule for 24.1"

**Tool**: `bd_get_build_calendar(version="24.1")`

Show the upcoming events in a formatted table or list with dates, event types, and colors.

### Q: "List recent builds for DEV"

**Tool**: `bd_list_builds(version="DEV", top=10)`

Returns the 10 most recent builds with version, status, date, and location.

### Q: "Find build 24.1.38.1000"

**Tool**: `bd_search_build(build_number="24.1.38.1000")`

Returns detailed info for the specific build including status, location, and BuildDirector URL.

---

## Response Formatting Guidelines

### Build Information
Always include:
- Full 4-part version number
- Build status (Complete / Failed / In Progress)
- Date (started/completed)
- BuildDirector URL for direct access

### BOTW Schedule
Present as a formatted cycle:
```
🔒 Lockdown: Apr 13 – Apr 16
🏗️ BOTW Generation: Apr 16
🧪 Smoke Test: Apr 17 – Apr 22
📦 Publish: Apr 22
```

### Jira ↔ Build Correlation
When showing cards in a build, include:
- Jira key (linked)
- Summary
- Status
- Priority

When showing builds for a card:
- Build version
- Build status
- BuildDirector link

---

## Tool Reference

| Tool | Purpose | Key Parameters |
|---|---|---|
| `bd_get_latest_build` | Latest successful build for a version | `version` |
| `bd_list_builds` | List N most recent builds | `version`, `top`, `successful_only` |
| `bd_search_build` | Look up a specific build by full version | `build_number` |
| `bd_list_versions` | List all OnBase versions in BuildDirector | — |
| `bd_version_aliases` | Show version alias mapping | — |
| `bd_get_botw_builds` | Find BOTW builds (build_number=1000) | `version`, `top` |
| `bd_get_build_calendar` | Get build schedule/calendar events | `version` |
| `jira_fixed_in_build` | Get which build(s) a Jira card is fixed in | `jira_key` |
| `jira_cards_in_build` | Find all Jira cards fixed in a build | `build_number`, `max_results` |
