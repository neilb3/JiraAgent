from github import Repository
from models import PlanOutput

def run_retriever(repo: Repository, plan: PlanOutput) -> dict[str, str]:
    """
    Fetch actual file contents from GitHub for every file in the plan.
    No Claude call - this is pure GitHub API retrieval.
    Returns dict of {filepath: current_content}
    """
    code_context = {}

    for filepath in plan.files_to_modify:
        try:
            file_obj = repo.get_contents(filepath)
            content = file_obj.decoded_content.decode("utf-8")
            code_context[filepath] = content
            print(f"    Fetched: {filepath} ({len(content)} chars)")
        except Exception as e:
            # File may not exist yet (agent will create it)
            print(f"    Note: {filepath} not found in repo (will be created)")
            code_context[filepath] = ""

    return code_context
