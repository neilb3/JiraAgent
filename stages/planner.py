import anthropic
import json
from models import PlanOutput


SYSTEM_PROMPT = """You are a senior software engineer planning code changes for a Jira ticket.
You will receive a Jira ticket, a repository file tree, and optionally the repo's AGENTS.md
file describing its conventions. Follow AGENTS.md instructions strictly if provided, including
any files marked LOCKED or DO NOT MODIFY - never include those in files_to_modify.

Return ONLY a valid JSON object matching this exact schema - no explanation, no markdown:
{
  "branch_name": "feat/PROJ-XX-short-description",
  "files_to_modify": ["path/to/file1.py", "path/to/file2.py"],
  "plan_summary": "One paragraph describing what changes will be made and why",
  "commit_message": "feat(PROJ-XX): short imperative description",
  "reasoning": "Why these specific files and not others"
}"""

def run_planner(
    client: anthropic.Anthropic,
    ticket: dict,
    file_tree: list[str],
    agents_md_content: str | None = None
) -> PlanOutput:
    ticket_text = f"""
Jira Ticket: {ticket['id']}
Title: {ticket['title']}
Description: {ticket['description']}
Acceptance Criteria:
{chr(10).join(f"- {c}" for c in ticket['acceptance_criteria'])}
Priority: {ticket['priority']}
"""

    file_tree_text = "Repository file tree:\n" + "\n".join(f"  {f}" for f in file_tree)

    agents_section = ""
    if agents_md_content:
        agents_section = f"\n\nRepository conventions (AGENTS.md):\n{agents_md_content}\n"

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"{ticket_text}\n\n{file_tree_text}{agents_section}"
            }
        ]
    )

    raw = response.content[0].text.strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    data = json.loads(raw)

    # Use pinned branch name from ticket if provided - prevents naming drift between runs
    if "branch_name" in ticket:
        data["branch_name"] = ticket["branch_name"]

    return PlanOutput(**data)
