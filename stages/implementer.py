import anthropic
import re
from models import PlanOutput, CodeChanges


BASE_SYSTEM_PROMPT = """You are a senior software engineer implementing code changes.
You will receive a Jira ticket, an implementation plan, and the current content of files to modify.
Optionally you will also receive the repo's AGENTS.md conventions - follow them strictly.

Return the complete new content for every file using this EXACT format with XML tags.
No explanation before or after. Start immediately with the first <file> tag.

<file path="routes/users.py">
complete file content here
</file>
<file path="tests/test_users.py">
complete file content here
</file>

Rules:
- Return COMPLETE file content for every file, not diffs
- Follow the existing code style exactly
- Never include secrets, API keys, or .env references in code"""


def run_implementer(
    client: anthropic.Anthropic,
    ticket: dict,
    plan: PlanOutput,
    code_context: dict[str, str],
    locked_files: list[str] | None = None,
    agents_md_content: str | None = None,
) -> CodeChanges:

    locked_files = locked_files or []

    # Build the locked-file rule dynamically - works for any repo's AGENTS.md
    locked_note = ""
    if locked_files:
        locked_note = (
            "\n\nABSOLUTE RULE: The following files are LOCKED and must NEVER be modified, "
            "even if they appear in the plan's files_to_modify list. Skip them entirely, "
            "do not include them in your response:\n"
            + "\n".join(f"- {f}" for f in locked_files)
        )

    agents_section = ""
    if agents_md_content:
        agents_section = f"\n\nRepository conventions (AGENTS.md):\n{agents_md_content}\n"

    system_prompt = BASE_SYSTEM_PROMPT + locked_note

    # Strip locked files out of context before they ever reach Claude
    filtered_context = {
        path: content
        for path, content in code_context.items()
        if path not in locked_files
    }

    ticket_text = f"""
Jira Ticket: {ticket['id']} - {ticket['title']}
Description: {ticket['description']}
Acceptance Criteria:
{chr(10).join(f"- {c}" for c in ticket['acceptance_criteria'])}
"""

    plan_text = f"""
Implementation Plan:
Branch: {plan.branch_name}
Summary: {plan.plan_summary}
Files to modify: {', '.join(f for f in plan.files_to_modify if f not in locked_files)}
Reasoning: {plan.reasoning}
"""

    files_text = "\n\n".join(
        f"--- CURRENT CONTENT OF {path} ---\n{content if content else '(new file)'}"
        for path, content in filtered_context.items()
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": (
                    f"{ticket_text}\n{plan_text}\n\n"
                    f"Current file contents:\n{files_text}{agents_section}\n\n"
                    "Return the complete new file contents using <file path='...'> XML tags. "
                    "Start with the first <file> tag immediately."
                )
            }
        ]
    )

    raw = response.content[0].text.strip()

    pattern = r'<file path=["\']([^"\']+)["\']>\n?(.*?)\n?</file>'
    matches = re.findall(pattern, raw, re.DOTALL)

    if not matches:
        raise ValueError(
            f"Implementer returned no file blocks. Raw response:\n{raw[:500]}"
        )

    files = {}
    for filepath, content in matches:
        path = filepath.strip()
        # Second layer of protection - never accept a locked file even if returned
        if path in locked_files:
            print(f"    Skipped: {path} (locked)")
            continue
        files[path] = content.strip()

    return CodeChanges(files=files)
