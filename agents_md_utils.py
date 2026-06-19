"""
Shared helpers for reading a target repo's AGENTS.md, if it has one,
and extracting which files (if any) are marked LOCKED / DO NOT MODIFY.
Works on any repo - AGENTS.md is optional. If absent, the agent
proceeds with no repo-specific conventions and no locked files.
"""
import re


def fetch_agents_md(repo) -> str | None:
    """Returns AGENTS.md content if it exists in the repo root, else None."""
    try:
        f = repo.get_contents("AGENTS.md")
        return f.decoded_content.decode("utf-8")
    except Exception:
        return None


def extract_locked_files(agents_md_content: str | None) -> list[str]:
    """
    Scans AGENTS.md for any line mentioning LOCKED or DO NOT MODIFY,
    and pulls out anything that looks like a file path on that line.
    Works regardless of whether the repo author used a table, a list,
    or plain prose - as long as the path and the keyword are on the same line.
    """
    if not agents_md_content:
        return []
    locked = []
    for line in agents_md_content.split("\n"):
        upper = line.upper()
        if "LOCKED" in upper or "DO NOT MODIFY" in upper:
            paths = re.findall(r'[\w\-/]+\.\w+', line)
            locked.extend(paths)
    return sorted(set(locked))
