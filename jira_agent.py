"""
Jira-integrated agent. Works on ANY GitHub repo via --repo owner/name.
Reads a real Jira ticket, updates Jira status, adds PR comment automatically.
Usage: python jira_agent.py SCRUM-6 --repo owner/reponame
"""
import os
import sys
import argparse
import tempfile
import shutil
import re
from dotenv import load_dotenv
import anthropic
from github import Github
from jira import JIRA

from agents_md_utils import fetch_agents_md, extract_locked_files
from stages.planner import run_planner
from stages.retriever import run_retriever
from stages.implementer import run_implementer
from stages.verifier import run_verifier
from stages.security import run_security_review
from stages.pr_creator import run_pr_creator

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
JIRA_URL = os.getenv("JIRA_URL")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")


def header(text):
    print(f"\n{'='*60}\n  {text}\n{'='*60}")

def stage(num, name):
    print(f"\n[STAGE {num}] {name}...")

def success(text):
    print(f"  ✅ {text}")

def info(text):
    print(f"  {text}")

def warning(text):
    print(f"  ⚠️  {text}")


def move_jira_status(jira, ticket_id: str, target_name: str) -> bool:
    transitions = jira.transitions(ticket_id)
    for t in transitions:
        if t['name'].lower() == target_name.lower():
            jira.transition_issue(ticket_id, t['id'])
            return True
    available = [t['name'] for t in transitions]
    info(f"  Transition '{target_name}' not found. Available: {available}")
    return False


def add_jira_comment(jira, ticket_id: str, text: str):
    jira.add_comment(ticket_id, text)


def fetch_jira_ticket(jira, ticket_id: str) -> dict:
    issue = jira.issue(ticket_id)
    fields = issue.fields
    description = fields.description or "No description provided."

    acceptance_criteria = []
    in_criteria = False
    for line in description.split("\n"):
        line = line.strip()
        if "acceptance criteria" in line.lower():
            in_criteria = True
            continue
        if in_criteria and line.startswith("-"):
            acceptance_criteria.append(line[1:].strip())
        elif in_criteria and line and not line.startswith("-"):
            in_criteria = False

    if not acceptance_criteria:
        acceptance_criteria = [
            f"Implement: {fields.summary}",
            "All existing tests must pass",
            "Add tests for new functionality",
        ]

    priority = "MEDIUM"
    if hasattr(fields, "priority") and fields.priority:
        priority = fields.priority.name.upper()

    title_slug = re.sub(r"[^a-z0-9]+", "-", fields.summary.lower())[:40].strip("-")

    return {
        "id": ticket_id,
        "title": fields.summary,
        "description": description,
        "acceptance_criteria": acceptance_criteria,
        "priority": priority,
        "reporter": str(fields.reporter) if fields.reporter else "Unknown",
        "labels": [str(l) for l in (fields.labels or [])],
        "branch_name": f"feat/{ticket_id.lower()}-{title_slug}",
        "jira_url": f"{JIRA_URL}/browse/{ticket_id}",
    }


def main():
    parser = argparse.ArgumentParser(description="Jira-to-PR agent - works on any GitHub repo")
    parser.add_argument("ticket_id", help="Jira ticket ID, e.g. SCRUM-6")
    parser.add_argument(
        "--repo", default="neilb3/fastapi-user-service",
        help="Target repo as owner/name, e.g. --repo myorg/myrepo"
    )
    args = parser.parse_args()

    ticket_id = args.ticket_id.upper()
    if "/" not in args.repo:
        print("ERROR: --repo must be in the form owner/repo")
        sys.exit(1)
    github_username, target_repo_name = args.repo.split("/", 1)

    for key, val in [
        ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
        ("GITHUB_TOKEN", GITHUB_TOKEN),
        ("JIRA_URL", JIRA_URL),
        ("JIRA_EMAIL", JIRA_EMAIL),
        ("JIRA_API_TOKEN", JIRA_API_TOKEN),
    ]:
        if not val:
            print(f"ERROR: {key} not found in .env")
            sys.exit(1)

    claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    gh = Github(GITHUB_TOKEN)
    repo = gh.get_repo(f"{github_username}/{target_repo_name}")

    info(f"\n  Connecting to Jira: {JIRA_URL}")
    jira = JIRA(server=JIRA_URL, basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN))

    # Stage 0: Fetch ticket
    stage(0, "Fetching Jira Ticket")
    ticket = fetch_jira_ticket(jira, ticket_id)
    header(f"JIRA TICKET: {ticket['id']}\n  {ticket['title']}\n  TARGET REPO: {args.repo}")
    info(f"Reporter: {ticket['reporter']}")
    info(f"Priority: {ticket['priority']}")
    info(f"URL:      {ticket['jira_url']}")

    add_jira_comment(jira, ticket_id,
        f"Zenarai AI Agent started working on this ticket against {args.repo}. "
        "Planning implementation and retrieving code from GitHub..."
    )
    success("Jira comment added: Agent started")

    # AGENTS.md - optional, works on any repo
    agents_md_content = fetch_agents_md(repo)
    locked_files = extract_locked_files(agents_md_content)
    if agents_md_content:
        success(f"Found AGENTS.md ({len(locked_files)} locked file(s))")
    else:
        info("No AGENTS.md found in this repo - proceeding without repo-specific conventions")

    # Stage 1: Planning
    stage(1, "Planning")
    info("Fetching repo file tree...")
    contents = repo.get_contents("")
    file_tree = []
    stack = list(contents)
    while stack:
        item = stack.pop()
        if item.type == "dir":
            stack.extend(repo.get_contents(item.path))
        else:
            file_tree.append(item.path)
    info(f"Found {len(file_tree)} files in repo")
    info("Claude thinking about implementation plan...")

    plan = run_planner(claude, ticket, file_tree, agents_md_content)
    success("Plan ready")
    info(f"Branch:  {plan.branch_name}")
    info(f"Files:   {', '.join(plan.files_to_modify)}")
    info(f"Summary: {plan.plan_summary[:120]}...")

    # Stage 2: Code Retrieval
    stage(2, "Code Retrieval")
    info("Fetching current file contents from GitHub...")
    code_context = run_retriever(repo, plan)
    success(f"Retrieved {len(code_context)} files")

    full_repo_context = dict(code_context)
    for lf in locked_files:
        if lf not in full_repo_context:
            try:
                f = repo.get_contents(lf)
                full_repo_context[lf] = f.decoded_content.decode("utf-8")
            except Exception:
                pass

    # Stage 3: Implementation
    stage(3, "Implementation")
    info("Claude generating code changes...")
    changes = run_implementer(claude, ticket, plan, code_context, locked_files, agents_md_content)
    success(f"Generated changes for {len(changes.files)} files")
    for filepath in changes.files:
        info(f"Modified: {filepath}")

    # Stage 4: Verification Loop
    stage(4, "Test Execution + Verification Loop")
    info("Cloning repo locally for test execution...")
    local_repo_path = tempfile.mkdtemp(prefix="agent-target-")
    try:
        import subprocess
        clone_url = f"https://{GITHUB_TOKEN}@github.com/{github_username}/{target_repo_name}.git"
        subprocess.run(["git", "clone", clone_url, local_repo_path], capture_output=True, check=True)

        req_path = os.path.join(local_repo_path, "requirements.txt")
        if os.path.exists(req_path):
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_path, "-q"], capture_output=True)

        final_changes, attempts, test_output = run_verifier(claude, changes, local_repo_path)
    finally:
        shutil.rmtree(local_repo_path, ignore_errors=True)

    if attempts == 1:
        success("All tests passed on first attempt")
    else:
        success(f"All tests passed after {attempts} attempts (self-corrected)")

    # Stage 5: Security Review
    stage(5, "Security Review")
    info("Running dedicated security review...")
    security_report = run_security_review(claude, final_changes, full_repo_context, ticket)

    for finding in security_report.findings:
        if finding.severity in ("HIGH", "MEDIUM"):
            warning(f"{finding.severity}: {finding.file} - {finding.issue}")
        else:
            info(f"  {finding.severity}: {finding.file} - {finding.issue}")

    info(f"  {security_report.env_check}")

    verdict = security_report.verdict
    high_findings = [f for f in security_report.findings if f.severity == "HIGH"]

    if verdict == "FAIL":
        print("\n  ❌ Security review FAILED - PR not created")
        add_jira_comment(jira, ticket_id,
            f"FAILED: Security review found HIGH severity issues. PR not created.\n\n"
            + "\n".join(f"- {f.file}: {f.issue}" for f in high_findings)
        )
        sys.exit(1)
    else:
        success(f"Security verdict: {verdict}")

    # Stage 6: PR Creation
    stage(6, "Creating Pull Request")
    pr_result = run_pr_creator(
        gh, repo, plan, final_changes,
        security_report, ticket, attempts, test_output
    )
    success(f"PR #{pr_result.pr_number} created")

    # Move Jira to Done
    info("\n  Updating Jira status → Done...")
    moved = move_jira_status(jira, ticket_id, "Done")
    if moved:
        success("Jira ticket moved to Done")

    findings_summary = "\n".join(
        f"- {f.severity}: {f.file} - {f.issue}"
        for f in security_report.findings
        if f.severity in ("HIGH", "MEDIUM")
    ) or "No HIGH or MEDIUM findings."

    add_jira_comment(jira, ticket_id,
        f"Zenarai AI Agent completed implementation against {args.repo}.\n\n"
        f"Pull Request: {pr_result.pr_url}\n"
        f"Branch: {pr_result.branch_name}\n"
        f"Tests: All passed (attempt {attempts}/3)\n"
        f"Security Verdict: {verdict}\n\n"
        f"Security Findings:\n{findings_summary}\n\n"
        f"Please review the PR and merge when ready."
    )
    success("Jira ticket updated with PR link and security findings")

    header(
        f"DONE\n"
        f"  Jira: {ticket['jira_url']}\n"
        f"  PR:   {pr_result.pr_url}"
    )


if __name__ == "__main__":
    main()
