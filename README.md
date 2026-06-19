# Jira to PR Agent

An AI agent that receives a Jira ticket and produces a pull request, covering planning,
code retrieval, test execution, verification loops, and security review.

## What It Does

Given a Jira ticket ID and a target GitHub repo, the agent:

1. **Plans** — Claude reads the ticket and the target repo's file tree (plus its
   `AGENTS.md` if one exists) and decides which files need to change
2. **Retrieves code** — pulls the actual current content of those files from GitHub
3. **Implements** — Claude writes complete new versions of the affected files
4. **Verifies** — clones the repo locally, writes the new files, runs the real test
   suite via `pytest`. If tests fail, sends the exact error back to Claude for a fix
   and retries (up to 3 attempts)
5. **Security reviews** — a separate Claude call, with a dedicated security-reviewer
   system prompt grounded in the OWASP API Security Top 10 (2023) and OWASP Top 10
   (2021), reviews the final code and returns severity-rated findings
6. **Opens a PR** — creates a branch, pushes the changes, and opens a pull request with
   the plan summary, test results, and security findings embedded in the description.
   This happens even if the security review returns FAIL — the PR is clearly flagged
   for required human review rather than silently blocked, since an automated agent
   should surface findings to a person rather than make the final merge decision itself

`jira_agent.py` additionally fetches the ticket directly from Jira via the REST API and
updates the ticket's status and comments once the PR is created.

## Works on Any Repo

```bash
python jira_agent.py SCRUM-6 --repo owner/reponame
python agent.py --repo owner/reponame
```

`GITHUB_USERNAME`/`TARGET_REPO` are not hardcoded — the target repo is a runtime
parameter. If the target repo has an `AGENTS.md` file describing its conventions and
marking any files as `LOCKED` / `DO NOT MODIFY`, the agent reads and respects it
dynamically. If no `AGENTS.md` exists, the agent says so plainly and proceeds with
general engineering judgment. This was validated against two structurally unrelated
repos during development: a FastAPI REST service (with an `AGENTS.md`) and a
dependency-free NLP scoring library (with no `AGENTS.md` at all).

## Architecture

```
ticket (Jira or local dict)
    │
    ▼
[1] Planner ──────► Claude call: produces a structured plan
    │                (branch name, files to touch, reasoning)
    ▼
[2] Retriever ────► GitHub API: fetches current file contents
    │                (no Claude call — pure read)
    ▼
[3] Implementer ──► Claude call: writes complete new file contents,
    │                respects any LOCKED files found in AGENTS.md
    ▼
[4] Verifier ─────► Clones repo locally, runs pytest
    │                Fails → Claude patches → retry (max 3x)
    ▼
[5] Security ─────► Separate Claude call, dedicated reviewer role,
    │                checks OWASP API Security Top 10 / OWASP Top 10
    ▼
[6] PR Creator ───► GitHub API: branch, push, open PR
                     (always — FAIL verdict flags the PR, doesn't block it)
                     (Jira variant also closes the ticket on non-FAIL verdicts)
```

Each stage is a separate Python module under `stages/`. The orchestrator (`agent.py`
or `jira_agent.py`) calls them in sequence, passing typed Pydantic objects
(`PlanOutput`, `CodeChanges`, `SecurityReport`, etc — see `models.py`) between stages.

## Design Decisions

- **No agent framework (LangChain, etc).** Every Claude call is explicit, with full
  control over prompts and output parsing. Easier to debug, easier to explain, no
  framework-internal failure modes to dig through.
- **No MCP.** The pipeline is fixed and sequential, not a dynamic tool-discovery
  problem. GitHub and Jira are called directly via their official SDKs (`PyGithub`,
  `jira`).
- **One agent, multiple specialized Claude calls.** Not a multi-agent system. A single
  Python orchestrator makes several sequential Claude calls, each with a different
  system prompt and role (planner, implementer, patcher, security reviewer) — not
  independent agents communicating with each other.
- **XML, not JSON, for code transfer between stages.** JSON broke repeatedly when
  carrying full file contents — Python code full of quotes and escape sequences
  corrupts a JSON string value. Switched to `<file path="...">...</file>` tags, which
  don't require escaping.
- **Security review grounded in OWASP, not an ad-hoc checklist.** Early iterations used
  a checklist written by trial and error against one demo repo, which risked overfitting
  to whatever that repo happened to contain. The current prompt explicitly cites OWASP
  API Security Top 10 (2023) and OWASP Top 10 (2021) categories — a public, independently
  verifiable industry standard — so the review generalizes to any codebase rather than
  pattern-matching on one.
- **Verdict comes directly from the model's own judgment, no hidden override.** An
  earlier version had application code that silently downgraded certain HIGH findings
  to MEDIUM based on keyword matching in the model's free-text explanation. That was
  removed — it's fragile and looks like the system is gaming its own results. The
  current version trusts the `verdict` field the security reviewer returns directly.
- **A FAIL verdict doesn't block the PR — it flags it.** A fully automated system that
  silently refuses to produce output when its own internal judgment is uncertain isn't
  useful to a human reviewer. The PR is always created; on FAIL it's clearly marked
  `[NEEDS REVIEW - SECURITY FAIL]` with a warning banner, and on the Jira side the
  ticket is deliberately left unresolved (not moved to Done) until a human looks at it.

## Files

```
agent.py               Orchestrator using a local/mock ticket
jira_agent.py           Orchestrator that fetches a real ticket from Jira
                        and updates Jira status/comments
ticket.py                Mock ticket definitions (used by agent.py)
models.py                 Pydantic models for inter-stage data
agents_md_utils.py         Reads a target repo's AGENTS.md (if present) and
                            extracts which files are marked LOCKED
stages/
  planner.py              Stage 1
  retriever.py              Stage 2
  implementer.py             Stage 3 (respects locked files dynamically)
  verifier.py                 Stage 4 (test execution + retry loop)
  security.py                   Stage 5 (OWASP-grounded review)
  pr_creator.py                  Stage 6 (incl. PR deduplication logic)
```

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file (never committed — see `.gitignore`):

```
ANTHROPIC_API_KEY=sk-ant-...
GITHUB_TOKEN=ghp_...
JIRA_URL=https://yourorg.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=...
```

Run against a local mock ticket:
```bash
python agent.py --repo owner/reponame
```

Run against a real Jira ticket:
```bash
python jira_agent.py SCRUM-6 --repo owner/reponame
```

## Known Limitations

- Test execution assumes a Python codebase using `pytest`, with dependencies listed in
  `requirements.txt`. Other languages or test frameworks aren't supported yet.
- Code retrieval sends the full file tree to Claude for planning. Works well for small
  to medium repos; a large-scale repo would need embedding-based retrieval to stay
  within context limits.
- Security review breadth is LLM-based and not fully deterministic — re-running the
  same ticket can surface a slightly different set of findings. The verdict logic and
  severity rules are designed to keep the important ones (HIGH in production code)
  consistent, but exhaustiveness on lower-severity findings will vary run to run.
