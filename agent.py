import os
import sys
import argparse
import tempfile
import shutil
from dotenv import load_dotenv
import anthropic
from github import Github

from ticket import JIRA_TICKET
from agents_md_utils import fetch_agents_md, extract_locked_files
from stages.planner import run_planner
from stages.retriever import run_retriever
from stages.implementer import run_implementer
from stages.verifier import run_verifier
from stages.security import run_security_review
from stages.pr_creator import run_pr_creator


# ── Config ────────────────────────────────────────────────────────────────────
load_dotenv()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

def header(text: str):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")

def stage(num: int, name: str):
    print(f"\n[STAGE {num}] {name}...")

def success(text: str):
    print(f"  ✅ {text}")

def warning(text: str):
    print(f"  ⚠️  {text}")

def info(text: str):
    print(f"  {text}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Jira-to-PR agent - works on any GitHub repo")
    parser.add_argument(
        "--repo", default="neilb3/fastapi-user-service",
        help="Target repo as owner/name, e.g. --repo myorg/myrepo"
    )
    args = parser.parse_args()

    if "/" not in args.repo:
        print("ERROR: --repo must be in the form owner/repo")
        sys.exit(1)
    github_username, target_repo_name = args.repo.split("/", 1)

    ticket = JIRA_TICKET

    header(f"JIRA TICKET: {ticket['id']}\n  {ticket['title']}\n  TARGET REPO: {args.repo}")

    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY not found in .env")
        sys.exit(1)
    if not GITHUB_TOKEN:
        print("ERROR: GITHUB_TOKEN not found in .env")
        sys.exit(1)

    claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    gh = Github(GITHUB_TOKEN)
    repo = gh.get_repo(f"{github_username}/{target_repo_name}")

    # ── AGENTS.md (optional, works on any repo) ──────────────────────────────
    agents_md_content = fetch_agents_md(repo)
    locked_files = extract_locked_files(agents_md_content)
    if agents_md_content:
        success(f"Found AGENTS.md ({len(locked_files)} locked file(s))")
    else:
        info("No AGENTS.md found in this repo - proceeding without repo-specific conventions")

    # ── Stage 1: Planning ─────────────────────────────────────────────────────
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

    success(f"Plan ready")
    info(f"Branch:  {plan.branch_name}")
    info(f"Files:   {', '.join(plan.files_to_modify)}")
    info(f"Summary: {plan.plan_summary[:120]}...")

    # ── Stage 2: Code Retrieval ───────────────────────────────────────────────
    stage(2, "Code Retrieval")
    info("Fetching current file contents from GitHub...")
    code_context = run_retriever(repo, plan)
    success(f"Retrieved {len(code_context)} files")

    # Pull in any locked files too, so security review can still see them
    full_repo_context = dict(code_context)
    for lf in locked_files:
        if lf not in full_repo_context:
            try:
                f = repo.get_contents(lf)
                full_repo_context[lf] = f.decoded_content.decode("utf-8")
            except Exception:
                pass

    # ── Stage 3: Implementation ───────────────────────────────────────────────
    stage(3, "Implementation")
    info("Claude generating code changes...")
    changes = run_implementer(claude, ticket, plan, code_context, locked_files, agents_md_content)
    success(f"Generated changes for {len(changes.files)} files")
    for filepath in changes.files:
        info(f"Modified: {filepath}")

    # ── Stage 4: Verification Loop ────────────────────────────────────────────
    stage(4, "Test Execution + Verification Loop")
    info("Cloning repo locally for test execution...")
    local_repo_path = tempfile.mkdtemp(prefix="agent-target-")
    try:
        import subprocess
        clone_url = f"https://{GITHUB_TOKEN}@github.com/{github_username}/{target_repo_name}.git"
        subprocess.run(
            ["git", "clone", clone_url, local_repo_path],
            capture_output=True, check=True
        )

        req_path = os.path.join(local_repo_path, "requirements.txt")
        if os.path.exists(req_path):
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", req_path, "-q"],
                capture_output=True
            )

        final_changes, attempts, test_output = run_verifier(
            claude, changes, local_repo_path
        )

    finally:
        shutil.rmtree(local_repo_path, ignore_errors=True)

    if attempts == 1:
        success(f"All tests passed on first attempt")
    else:
        success(f"All tests passed after {attempts} attempts (self-corrected)")

    # ── Stage 5: Security Review ──────────────────────────────────────────────
    stage(5, "Security Review")
    info("Running dedicated security review...")
    security_report = run_security_review(claude, final_changes, full_repo_context, ticket)

    for finding in security_report.findings:
        if finding.severity in ("HIGH", "MEDIUM"):
            warning(f"{finding.severity}: {finding.file} - {finding.issue}")
        else:
            info(f"  {finding.severity}: {finding.file} - {finding.issue}")

    info(f"  {security_report.env_check}")

    if security_report.verdict == "FAIL":
        print("\n  ❌ Security review FAILED - PR not created")
        print("  Fix HIGH severity issues in production code before merging")
        sys.exit(1)
    else:
        success(f"Security verdict: {security_report.verdict}")

    # ── Stage 6: PR Creation ──────────────────────────────────────────────────
    stage(6, "Creating Pull Request")
    pr_result = run_pr_creator(
        gh, repo, plan, final_changes,
        security_report, ticket, attempts, test_output
    )

    success(f"PR #{pr_result.pr_number} created")

    header(f"DONE\n  PR: {pr_result.pr_url}")

if __name__ == "__main__":
    main()
