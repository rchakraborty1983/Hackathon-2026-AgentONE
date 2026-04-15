# Comprehensive Code Review Skill

> **Purpose:** This is the single, authoritative instruction document for ALL code reviews performed by the OnBase agent system (TFS_Jira_Analyzer, OnBase agent). It covers every aspect of code review — from context gathering to diff analysis to final verdicts.
>
> **Applies to:** TFS changeset reviews, TFS shelveset reviews, GitHub PR reviews, service release backport reviews, feature branch reviews.

---

## Table of Contents

1. [Mandatory Pre-Review Context Gathering](#1-mandatory-pre-review-context-gathering)
2. [Code Review Checklist](#2-code-review-checklist)
   - 2.1 [Coding Standards](#21-coding-standards)
   - 2.2 [Accessibility Standards](#22-accessibility-standards)
   - 2.3 [Security Standards](#23-security-standards)
   - 2.4 [JavaScript / ES6 Prohibition](#24-javascript--es6-prohibition)
   - 2.5 [Loss of Functionality (LOF) Cross-Branch Analysis](#25-loss-of-functionality-lof-cross-branch-analysis)
   - 2.6 [Behavioral Correctness — Framework Replacement Reviews](#26-behavioral-correctness--framework-replacement-reviews)
3. [Diff Analysis Workflow](#3-diff-analysis-workflow)
4. [Service Release Backport Reviews](#4-service-release-backport-reviews)
5. [Feature Branch Reviews](#5-feature-branch-reviews)
6. [HTML Report Generation](#6-html-report-generation)
7. [Review Quality Standards](#7-review-quality-standards)
8. [Verdict Categories](#8-verdict-categories)

---

## 1. Mandatory Pre-Review Context Gathering

**CRITICAL:** Before reviewing a SINGLE line of code, you MUST gather ALL of the following context. Do NOT skip any step. Each step builds on the previous.

### Step 1 — Jira Card (PRIMARY task definition)

**Tool:** Atlassian MCP (`getJiraIssue`, `searchJiraIssuesUsingJql`, `getJiraIssueRemoteIssueLinks`)

1. **Fetch the primary Jira card** — Retrieve the full card including:
   - Summary, description, acceptance criteria (`customfield_11591`), status, fix version
   - Testing recommendations (`customfield_11816`)
   - Support Issue ID, SF Case Number (`customfield_10602`), customer name (`customfield_11613`)
   - Steps to Recreate (STR) and Expected Result (for bugs)
   - Labels, components, sprint, assignee, reporter

2. **Fetch ALL linked cards** — Use `getJiraIssueRemoteIssueLinks` and check in-line links in the description:
   - **Blocks / is blocked by** — dependencies that affect review scope
   - **Relates to** — sibling changes that provide context
   - **Clones / is cloned from** — original issue with additional context
   - **Parent card** — ALWAYS fetch the parent (epic/story) to understand the broader initiative

3. **Check completed sibling cards** — Search for siblings under the same parent:
   ```
   parent = <PARENT_KEY> AND status = Done ORDER BY resolutiondate DESC
   ```
   Review their summaries and changesets to identify files that have been **added, modified, or deleted** by prior work.

4. **Verify Acceptance Criteria / Expected Result**:
   - For **Stories**: Extract each acceptance criterion and prepare to verify against code changes
   - For **Bugs**: Extract the Expected Result and prepare to verify the fix addresses it

### Step 2 — OnBase MRG + REST API SDK

**Tool:** MRG_Parser subagent

For the module area being changed, consult the MRG to verify:
- The code change aligns with documented MRG behavior
- Any MRG-documented constraints or requirements are respected
- The change doesn't violate any documented architectural patterns
- For API changes: verify request/response contract compliance

### Step 3 — Confluence (design docs, architecture decisions)

**Tool:** Atlassian MCP (`searchConfluenceUsingCql`, `getConfluencePage`)

Search for relevant design docs, ADRs, or architecture notes:
```
space IN (WV, WF, Forms, ONBASE) AND text ~ "<feature or component name>"
```
Check for:
- Architecture Decision Records (ADRs) that constrain implementation choices
- Design documents that specify expected behavior
- Team conventions or patterns documented in Confluence

### Step 4 — Salesforce Cases (customer context)

If the Jira card has a Support Issue ID or SF Case Number:
- Use MRG_Parser's Salesforce MCP tool to retrieve the case
- Note the customer context, original reported issue, and any workarounds communicated
- This context informs whether the fix is adequate for the customer's scenario

### Step 5 — Diff Retrieval

**Tool:** TFS MCP API (for TFS) or GitHub MCP (for GitHub)

Retrieve the actual code changes:
- **TFS Shelvesets**: Use shelveset API to get file diffs
- **TFS Changesets**: Use changeset API to get file diffs
- **GitHub PRs**: Use `get_pull_request_files` to get changed files

### Step 6 — Reference All Sources in Comments

Every review comment MUST cite its source:
- `[Jira: SBPWC-XXXXX]` — when referencing acceptance criteria, STR, or testing recommendations
- `[MRG: <Module>/<Section>]` — when citing MRG documentation
- `[SF: <CaseNumber>]` — when referencing the Salesforce case context
- `[Confluence: <PageTitle>]` — when citing Confluence documentation
- `[Code: <FilePath>#L<line>]` — when referencing existing codebase patterns
- `[ADR: ADR-OB-XXX]` — when citing Architecture Decision Records

---

## 2. Code Review Checklist

### 2.1 Coding Standards

> **Reference:** `{AGENT_ROOT}\docs\code-styles.md`
> **Severity:** Call out deviations in review, but do NOT treat as MUST fix unless egregious.

#### Region Organization
- Regions MUST appear in this order if present:
  1. Nested Classes (`#region [ClassName]`)
  2. Routed Commands (`#region Routed Commands`)
  3. Dependency Properties (`#region Dependency Properties`)
  4. Construction (`#region Construction`)
  5. Fields (`#region Fields`)
  6. Properties (`#region Properties`)
  7. Methods (`#region Methods`)
- A region MUST only be included if it contains at least one member
- Regions MUST be used if a class has members belonging to more than one section type
- Regions MUST NOT be used if a class contains members of only one section type
- Finalizers (`~ClassName`) MUST be placed in the `Construction` region as the last method
- `Dispose` methods MUST be the last methods in the `Methods` region
- Exactly one blank line after `#region` and before `#endregion`
- Do NOT add comments on `#endregion` lines

#### Variable Declarations
- `var` MUST only be used when the type name (excluding namespace) is **16 or more characters** in length
- Types with names of **15 or fewer characters** MUST use the explicit type name
- This rule does NOT apply to fields or properties

#### Member Access
- **Properties**: MUST be prefixed with `this.` (e.g., `this.PropertyName`)
- **Instance methods**: MUST NOT be prefixed with `this.` (e.g., `MethodName()`)
- **Fields**: MUST NOT be prefixed with `this.` (e.g., `fieldName`)
- **Static members**: MUST always use the class name (e.g., `String.Empty`, `Int64.MaxValue`)

#### Expression Syntax
- Lambda expression syntax (`=>`) MUST be used for any method body consisting of a single statement
- When calling a method with **more than 3 arguments**, each argument MUST use named argument syntax

#### General Patterns
- Match the existing code style, exception handling strategy, and DI approach in the file being edited
- Follow established naming conventions in the codebase
- Use `Hyland.Localization.Strings.GetString()` for user-facing text
- Verify proper use of `Guard.VerifyArgumentNotNull` for argument validation

### 2.2 Accessibility Standards

> **Reference:** `C:\Users\rchakraborty\AppData\Roaming\Code\User\prompts\agents\docs\OnBase_WorkView_Accessibility_Guidelines.md`
>
> **MANDATORY:** Before reviewing any UI-facing change, read the comprehensive accessibility guidelines file above. It consolidates all OnBase accessibility patterns, WCAG requirements, and Hyland standards into a single reference.
>
> **When to apply:** ALL code reviews for UI-facing changes (WPF, HTML, JavaScript, Angular, Razor). Skip for backend-only / data-layer changes with no UI impact.

#### Accessibility Review Workflow
1. Read `OnBase_WorkView_Accessibility_Guidelines.md` for the full checklist and patterns
2. Apply all applicable guidelines from that file to the code under review
3. Flag any violations with the specific guideline reference from the file

### 2.3 Security Standards

> **References:**
> - [ADR-OB-014: Input Validation Requirement](https://hyland.atlassian.net/wiki/spaces/ONBASE/pages/2999451824/ADR-OB-014+Input+Validation+Requirement) — NIST 800-53 rev.5 Moderate SI-10 compliance
> - [Test Case Guidance — Input Validation](https://hyland.atlassian.net/wiki/spaces/AT/pages/401113971/Test+Case+Guidance#Input-Validation) — Detailed input validation test patterns
> - [PD 6.1 - Security Remediation Timelines](https://hyland.atlassian.net/wiki/spaces/GH/pages/251069865/PD+6.1+-+Hyland+Application+Security+Remediation+Timelines+Standard) — Severity-based remediation SLAs
> - OWASP Top 10 (2021)

#### ADR-OB-014 Input Validation Requirements (Mandatory)
All OnBase modules MUST perform appropriate input validation. Per the ADR:
- **Convert to strongly-typed data types as early as possible** when reading input (e.g., string→number early, reject if invalid)
- **Check ranges and size** of input for expected values; error/reject if outside range
- **Prefer allowlisting over denylisting** — confirm valid/expected input rather than blocking "bad" input (attacks can bypass common denylist checks)
- **Validate at system boundaries** — user input, API input, command-line input

#### OWASP Top 10 Checks
For every code review, explicitly check for:

| Vulnerability | What to Look For |
|---|---|
| **A01 - Broken Access Control** | Missing authorization checks, direct object references without validation, path traversal |
| **A02 - Cryptographic Failures** | Hardcoded secrets, weak algorithms, sensitive data in logs/URLs |
| **A03 - Injection** | SQL injection (string concatenation in queries), XSS (unencoded output), command injection, LDAP injection |
| **A04 - Insecure Design** | Missing rate limiting, no brute-force protection, no input size limits |
| **A05 - Security Misconfiguration** | Debug mode enabled, default credentials, unnecessary features enabled |
| **A06 - Vulnerable Components** | Outdated NuGet/npm packages, known CVEs in dependencies |
| **A07 - Auth Failures** | Weak password policies, missing session timeout, credential exposure |
| **A08 - Data Integrity Failures** | Deserialization without validation, unsigned updates, CI/CD pipeline trust |
| **A09 - Logging Failures** | Missing audit logs for security events, sensitive data in logs |
| **A10 - SSRF** | User-controlled URLs without validation, internal network access via URL parameters |

#### SQL Injection Prevention
- **NEVER** use string concatenation to build SQL queries
- **ALWAYS** use parameterized queries or stored procedures
- Flag any occurrence of `String.Format`, `$""`, or `+` used to build SQL strings
- Verify use of `IQueryDataAccess` patterns in OnBase data layer

#### Cross-Site Scripting (XSS) Prevention
- All user input displayed in HTML MUST be properly encoded
- Use framework-provided encoding (Razor auto-encoding, `HttpUtility.HtmlEncode`, etc.)
- Check for `innerHTML`, `document.write`, `eval()`, and jQuery `.html()` usage
- Verify Content-Security-Policy headers for web responses

#### Path Traversal Prevention
- Validate file paths against known-good directories
- Use `Path.Combine` with validation, not string concatenation
- Check for `../` or `..\\` in user-supplied paths

#### Input Validation Test Patterns (from Test Case Guidance)
When reviewing input handling, verify these categories are addressed:

| Input Type | Validations |
|---|---|
| **Text/String** | Max length, min length, special characters (`~!@#$%^&*()_+=-`), XSS payloads (`<script>...</script>`), required field (null/empty/space-only), HTML injection |
| **Numeric** | Min/max value, negative values, zero, boundary values (max-1, max, max+1), type validation (integer vs float) |
| **Date** | Valid/invalid formats, boundary dates, leap year, future/past limits, leading zeros, separators |
| **File Upload** | File type validation, file size limits, filename sanitization, content-type verification |

#### Security Remediation Timelines (PD 6.1)
If a security vulnerability is found during code review, classify and escalate per these SLAs:

| Severity | RELEASED Software Remediation | UNRELEASED Software |
|---|---|---|
| Critical | 15 calendar days | Prior to Release |
| High | 30 calendar days | Prior to Release |
| Medium | 90 calendar days | Prior to Release |
| Low | 180 calendar days | 180 calendar days |

#### Security Review Checklist Items
- [ ] No SQL injection vectors (all queries parameterized)
- [ ] No XSS vectors (all output encoded, no `innerHTML`/`eval()` with user data)
- [ ] No path traversal (file paths validated against allowed directories)
- [ ] No hardcoded credentials or secrets
- [ ] Input validation at all system boundaries (UI, API, CLI)
- [ ] Allowlisting preferred over denylisting for input validation
- [ ] Strongly-typed conversion as early as possible
- [ ] Sensitive data not logged or exposed in error messages
- [ ] Authorization checks present for all protected operations
- [ ] Session management follows secure practices

### 2.4 JavaScript / ES6 Prohibition

> **Rule:** There MUST NOT be any occurrence of ES6 (ECMAScript 2015+) syntax in the codebase.
> **Severity:** ❌ CRITICAL — REJECT if ES6 code is found.

#### What Constitutes ES6+ Code (Flag ALL of these)

| ES6+ Feature | Example | Correct Alternative |
|---|---|---|
| `let` / `const` | `let x = 5;` | `var x = 5;` |
| Arrow functions | `(x) => x + 1` | `function(x) { return x + 1; }` |
| Template literals | `` `Hello ${name}` `` | `"Hello " + name` |
| Destructuring | `const { a, b } = obj;` | `var a = obj.a; var b = obj.b;` |
| Default parameters | `function f(x = 5)` | `function f(x) { x = x \|\| 5; }` |
| Rest/spread | `...args` | `Array.prototype.slice.call(arguments)` |
| `class` keyword | `class Foo {}` | `function Foo() {}` or prototype pattern |
| `import` / `export` | `import x from 'y'` | `require()` or script tags |
| `Promise` / `async`/`await` | `async function f()` | Callback or jQuery Deferred patterns |
| `Map` / `Set` / `Symbol` | `new Map()` | Plain objects/arrays |
| `for...of` loops | `for (let x of arr)` | `for (var i = 0; i < arr.length; i++)` |
| `Object.assign` | `Object.assign({}, a)` | jQuery `$.extend({}, a)` or manual copy |
| Optional chaining | `obj?.prop` | `obj && obj.prop` |
| Nullish coalescing | `x ?? y` | `x != null ? x : y` |

#### ES6 Review Checklist Items
- [ ] No `let` or `const` declarations — only `var`
- [ ] No arrow functions (`=>`)
- [ ] No template literals (backtick strings)
- [ ] No destructuring assignments
- [ ] No `class` keyword usage
- [ ] No `import`/`export` statements
- [ ] No `Promise`, `async`, `await`
- [ ] No `for...of` loops
- [ ] No optional chaining (`?.`) or nullish coalescing (`??`)
- [ ] No spread/rest operators (`...`)

### 2.5 Loss of Functionality (LOF) Cross-Branch Analysis

> **CRITICAL:** For EVERY code review, the diff MUST be compared not just with the target branch, but also with the **latest 3 service release branches** to identify Loss of Functionality scenarios.
>
> **Severity:** ❌ CRITICAL — Any LOF finding must be flagged and escalated.

#### Mandatory Branch Comparison Matrix

For every file changed in the shelveset/changeset/PR, retrieve and compare the file from:

| Branch | TFS Path Prefix | Purpose |
|---|---|---|
| **DEV** | `$/OnBase/DEV/` | Current development (baseline for new features) |
| **25.2** | `$/OnBase/25.2/` | Latest service release |
| **25.1** | `$/OnBase/25.1/` | Previous service release |
| **24.1** | `$/OnBase/24.1/` | Oldest active service release |

> **Note:** Update the service release branches above as new releases ship and old ones go out of support.

#### LOF Analysis Workflow

**For TFS (shelvesets/changesets):**

1. **Get the incoming change** — Retrieve the modified file from the shelveset/changeset
2. **Get each service release branch version** — For EVERY changed file:
   ```
   GET http://localhost:9000/file-content?path=$/OnBase/25.2/Core/OnBase.NET/{filepath}
   GET http://localhost:9000/file-content?path=$/OnBase/25.1/Core/OnBase.NET/{filepath}
   GET http://localhost:9000/file-content?path=$/OnBase/24.1/Core/OnBase.NET/{filepath}
   ```
3. **Compare ENTIRE FILES** — Not just changed lines. Look for:
   - Methods/properties that exist in a service release but are **removed** in the incoming change
   - Behavior changes that could break existing functionality
   - Configuration options or parameters that are removed or renamed
   - Default value changes that could affect existing deployments
   - Exception handling changes that alter error behavior

4. **Document findings in LOF Detection Table:**

   | Service Release | File | LOF Risk | Details |
   |---|---|---|---|
   | 25.2 | `SomeFile.cs` | ⚠️ Method `GetLegacyData()` removed | Present in 25.2 line 245, absent in incoming change |
   | 25.1 | `SomeFile.cs` | ✅ No LOF | All 25.1 functionality preserved |
   | 24.1 | `SomeFile.cs` | ❌ Default changed | `MaxRetries` default changed from 3 to 5; affects 24.1 deployments |

**For GitHub (PRs):**

1. **Get the PR diff** — Use `get_pull_request_files` to identify changed files
2. **Get release branch versions** — For each changed file, retrieve from the latest 3 release branches/tags
3. **Compare ENTIRE FILES** — Same analysis as TFS
4. **Document findings** in the same LOF Detection Table format

#### What Constitutes a LOF

- **Method/property removal** — A public/protected method or property present in any service release is removed
- **Signature change** — Method parameters changed in a way that breaks callers
- **Behavioral change** — Same method name but different behavior (e.g., returns different type, throws different exception)
- **Default value change** — Configuration defaults changed that affect existing deployments
- **Feature removal** — UI elements, config options, or capabilities removed
- **API contract break** — REST/SOA endpoint signature or response format changed
- **Database schema change** — Column removal, type change, or constraint change that breaks existing data

#### LOF Review Checklist Items
- [ ] All changed files compared with DEV branch
- [ ] All changed files compared with 25.2 service release
- [ ] All changed files compared with 25.1 service release
- [ ] All changed files compared with 24.1 service release
- [ ] No public/protected methods removed without documented justification
- [ ] No behavioral changes to existing methods without documented justification
- [ ] No default value changes that could affect existing deployments
- [ ] No API contract breaks (REST endpoints, SOA methods)
- [ ] LOF Detection Table included in review report

### 2.6 Behavioral Correctness — Framework Replacement Reviews

> **When to apply:** Any code review where a framework or library (AutoMapper, Entity Framework projection, ORM mapper, serializer, etc.) is being replaced with hand-written code.
> **Severity:** CRITICAL — these bugs are **silent** (no exception, no log, no error in response) and can only be detected by code review or functional testing.

#### Deferred Execution / Lazy Evaluation

When a framework mapper (e.g., AutoMapper) is replaced with manual LINQ `.Select()`:

- **AutoMapper always materializes** — `IMapper.Map<T>()` returns concrete `List<T>` or arrays. Items are stable objects.
- **LINQ `.Select()` is lazy** — Returns `IEnumerable<T>` backed by deferred execution. Each enumeration creates **new object instances**.

**The critical check:** After the mapper returns, does the calling code **mutate** the mapped objects?

If YES → the collection MUST be materialized with `.ToList()` or `.ToArray()`. Without materialization:
1. Calling code iterates the `IEnumerable<T>`, creates objects, mutates them (sets properties)
2. Serializer (ASP.NET JSON serializer) re-enumerates the same `IEnumerable<T>`
3. `.Select()` runs again → **new objects** created → previous mutations **silently lost**
4. API response contains `null` / default values where populated values were expected

**Review workflow:**
1. For each mapping method in the diff, check the **return type** of each collection property:
   - `IEnumerable<T>` from `.Select()` → **DANGER** — lazy, not materialized
   - `List<T>` from `.Select().ToList()` → **SAFE** — materialized on creation
   - `T[]` from `.Select().ToArray()` → **SAFE** — materialized on creation
2. For each lazy collection found, **read the calling method** (the consumer of the mapper output)
3. If the calling code does ANY of these to the mapped items, flag as **CRITICAL BUG**:
   - Sets properties on mapped objects (e.g., `item.Field.DataSource = ...`)
   - Passes mapped objects to methods that modify them (e.g., `populateVisualizationType(item, ...)`)
   - Indexes into the collection by position alongside a parallel source collection
   - Stores mapped objects in a dictionary/cache and expects mutations to persist

#### Behavioral Correctness Checklist Items
- [ ] All LINQ `.Select()` projections checked for materialization (`.ToList()` / `.ToArray()`)
- [ ] Calling methods traced for post-mapping mutations on returned objects
- [ ] No lazy `IEnumerable<T>` returned where stable object references are required
- [ ] Factory methods checked: if return type changed from `List<T>` / `T[]` to `IEnumerable<T>`, all callers verified
- [ ] Collection properties in API response models are concrete types (`List<T>`, `T[]`), not `IEnumerable<T>`

---

## 3. Diff Analysis Workflow

### For TFS Shelvesets

```
Step 1: GET http://localhost:9000/shelvesets?owner=<owner>&name=<name>
Step 2: For each file in shelveset, GET the diff content
Step 3: GET baseline from target branch (before change)
Step 4: GET DEV branch version for cross-reference
Step 5: GET 25.2, 25.1, 24.1 versions for LOF analysis
Step 6: Perform line-by-line review with syntax highlighting
Step 7: Apply all checklist items (Sections 2.1–2.6)
Step 8: Generate HTML report (Section 6)
```

### For TFS Changesets

```
Step 1: GET http://localhost:9000/changeset/{id} for metadata
Step 2: GET http://localhost:9000/changeset-files/{id} for file diffs
Step 3: GET baseline (previous version) for each file
Step 4: GET DEV branch version for cross-reference
Step 5: GET 25.2, 25.1, 24.1 versions for LOF analysis
Step 6: Perform line-by-line review
Step 7: Apply all checklist items (Sections 2.1–2.6)
Step 8: Generate HTML report (Section 6)
```

### For GitHub PRs

```
Step 1: Use get_pull_request to get PR metadata
Step 2: Use get_pull_request_files to get changed files
Step 3: Use get_file_contents for base and head versions
Step 4: GET release branch versions for LOF analysis
Step 5: Perform line-by-line review
Step 6: Apply all checklist items (Sections 2.1–2.6)
Step 7: Generate HTML report (Section 6)
```

---

## 4. Service Release Backport Reviews

> **When:** Reviewing shelvesets/changesets that backport changes from DEV to a service release branch (24.1, 25.1, 25.2).

### Mandatory Three-Way Comparison

For every file in the backport:

**Step 1: Get the Baseline (Service Release BEFORE backport)**
```
GET http://localhost:9000/file-content?path=$/OnBase/{version}/Core/OnBase.NET/{filepath}
```

**Step 2: Get the Backport (Service Release AFTER backport)**
```
# Shelveset or changeset content
```

**Step 3: Get the Reference (DEV — original implementation)**
```
GET http://localhost:9000/file-content?path=$/OnBase/DEV/Core/OnBase.NET/{filepath}
```

**Step 4: Three-Way Analysis Table**

| Change | Baseline → Backport | DEV | Expected? | Verdict |
|--------|---------------------|-----|-----------|---------|
| Security filtering code added | ✅ New code | ✅ Exists | ✅ Yes | Expected from original card |
| `#if NETFRAMEWORK` blocks | ❌ Did not exist | ✅ Exists | ❌ No | Should be REMOVED for service release |
| Using statements reordered | ⚠️ Changed | Different | ⚠️ Maybe | Flag for review |

### What to Flag in Backport Reviews

**❌ CRITICAL (REJECT until fixed):**
- Preprocessor directives inappropriate for target branch (e.g., `#if NETFRAMEWORK` in 24.1)
- Multi-targeting in project files when service release is single-target
- Code from unrelated Jira cards or uncommitted work
- Security vulnerabilities or coding standard violations
- Merge artifacts or conflict markers

**⚠️ WARNINGS (investigate before approval):**
- Using statement changes (may be IDE auto-fixes)
- Comment or whitespace changes
- Variable renames not in original implementation
- Additional error handling not in DEV

**✅ EXPECTED:**
- All code from the original Jira card implementation
- Preprocessor directives REMOVED when backporting from multi-targeted DEV to single-target SR
- Project file changes matching original implementation (without multi-targeting)

### Backport Verification Checklist
- [ ] Fetched all three versions (Baseline, Backport, DEV) for EVERY file
- [ ] Compared FULL file contents, not just diffs
- [ ] Identified ALL differences between Baseline → Backport
- [ ] Each difference is verified as expected OR flagged
- [ ] Checked project files (.csproj) for unintended multi-targeting
- [ ] Searched for preprocessor directives (NETFRAMEWORK, DEBUG)
- [ ] Documented ALL findings in comprehensive HTML report
- [ ] Provided CLEAR verdict (APPROVE / CONDITIONAL / REJECT)

---

## 5. Feature Branch Reviews

> **When:** Reviewing code in a feature branch (e.g., `$/OnBase/FEAT/CefsharpToWebView`, GitHub feature branches).
> **Key difference:** Feature branches are net-new development. Do NOT compare with DEV, service release branches, or local workspace. The feature branch IS the source of truth.

### Feature Branch Review Workflow

1. **Identify the feature branch** — Confirm the TFS path or GitHub branch name
2. **Get shelveset/changeset diffs WITHIN the feature branch only**
3. **Review for:**
   - Code quality and coding standards (Section 2.1)
   - Accessibility standards if UI changes (Section 2.2)
   - Security standards (Section 2.3)
   - ES6 prohibition for JavaScript (Section 2.4)
   - **Skip LOF analysis against DEV/service releases** — this is net-new work
4. **Additional feature-branch checks:**
   - Architecture alignment with the feature design (check Confluence)
   - Consistency with other changes in the same feature branch
   - Integration points with existing code documented

---

## 6. HTML Report Generation

### Required Output

For ALL code reviews, generate a professional HTML report saved to:
```
{MARKDOWN_ANALYSIS_ROOT}\code-reviews\<JIRA_KEY>_CodeReview.html
```

### Report Sections

1. **Header** — Jira key, summary, type (Bug/Story), status, assignee, reporter, sprint, review date
2. **Quick Stats** — Files changed, lines added, lines removed
3. **References** — All linked Jira issues, Salesforce cases, Confluence pages, shelveset/changeset links
   - **Shelveset URL**: `http://build-tfs:8080/tfs/HylandCollection/OnBase/_versionControl/shelveset?ss=<NAME>%3B<OWNER>` (URL-encode backslashes as `%5C`, semicolons as `%3B`, spaces as `%20`)
   - **Changeset URL**: `http://build-tfs:8080/tfs/HylandCollection/OnBase/_versionControl/changeset/<ID>`
   - **IMPORTANT**: Host is `build-tfs`, NOT `dev-tfs`
4. **Salesforce Case Context** (if applicable) — Customer, original issue, workarounds
5. **Issue Summary** — Plain-English summary of the Jira card
6. **Code Changes Analysis** — Rendered diff with syntax highlighting for each file
7. **Line-by-Line Review Comments** — Categorized as:
   - ✅ **Approve** (green) — Correct patterns, good practices
   - ⚠️ **Warning** (yellow) — Minor concerns, suggestions
   - ❌ **Issue** (red) — Problems that must be fixed before merge
   - ℹ️ **Info** (blue) — Contextual notes, MRG references
8. **Checklist Results** — Summary of all checklist items from Section 2:
   - 2.1 Coding Standards — pass/fail items
   - 2.2 Accessibility Standards — pass/fail/N/A items
   - 2.3 Security Standards — pass/fail items
   - 2.4 ES6 Prohibition — pass/fail items
   - 2.5 LOF Cross-Branch Analysis — pass/fail items with detection table
9. **Root Cause** (for bugs) — What caused the original issue
10. **Risk Assessment** — Table: scope, regression risk, cross-cutting impact, performance, security
11. **Acceptance Criteria Verification** — Each criterion marked Met / Not Met / Partial
12. **Suggested Test Scenarios** — Table: scenario, precondition, steps, expected result
13. **Gaps Identified** — Discrepancies between Jira requirements and actual code changes
14. **MRG & Documentation Cross-Reference** — Relevant MRG findings and documentation impact
15. **LOF Detection Table** — Cross-branch comparison results (Section 2.5)
16. **Verdict** — APPROVE / APPROVE WITH MINOR COMMENTS / REQUEST CHANGES with clear rationale

---

## 7. Review Quality Standards

### Code Quality
- Verify the change follows established codebase patterns (check similar code in the repo)
- Verify exception handling is appropriate and doesn't swallow important errors
- Flag any potential performance concerns (N+1 queries, missing caching, etc.)
- Check that variable/method names match codebase conventions
- Note if unit tests are missing when the DoD checklist requires them

### Documentation Quality
- XML comments on public APIs
- Update to existing documentation if behavior changes
- Inline comments for complex logic

### Testing Quality
- Unit tests for business logic changes
- Integration tests for data access changes
- UI tests for client-facing changes
- Negative test cases for input validation

---

## 8. Verdict Categories

| Verdict | When to Use |
|---|---|
| **✅ APPROVE** | All checklist items pass, no issues found, code is production-ready |
| **⚠️ APPROVE WITH MINOR COMMENTS** | Minor style/suggestion items that don't block merge, no security/LOF issues |
| **❌ REQUEST CHANGES** | Any of: security vulnerability, LOF detected, ES6 code found, broken acceptance criteria, critical coding standard violations |

### Escalation Rules
- **Security vulnerability found** → Flag with PD 6.1 severity classification and remediation timeline
- **LOF detected** → Flag with affected service release branches and customer impact assessment
- **ES6 code found** → REJECT with specific line numbers and required corrections
- **Accessibility violation** → Flag with WCAG criterion reference and remediation guidance

---

## MRG Upgrade Considerations Lookup

**For ANY upgrade-related request** (LoF analysis, version comparison, upgrade risk assessment, branch diff analysis between versions), you MUST look up the **Upgrade Considerations** section in the OnBase Module Reference Guide (MRG) for **both the source and target versions**.

This is **in addition to** any other MRG lookups or code analysis — never skip this step for upgrade-related work.

### When to Trigger
- User mentions upgrading from one OnBase version to another
- LoF analysis between two versions
- Branch comparison or divergence analysis across versions
- Any request involving version migration or compatibility
