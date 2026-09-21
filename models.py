from pydantic import BaseModel
from typing import Optional


class PlanOutput(BaseModel):
    branch_name: str
    files_to_modify: list[str]
    plan_summary: str
    commit_message: str
    reasoning: str


class CodeChanges(BaseModel):
    files: dict[str, str]  # filename -> new full content

class SecurityFinding(BaseModel):
    severity: str  # HIGH, MEDIUM, LOW, INFO
    file: str
    issue: str
    recommendation: str


class SecurityReport(BaseModel):
    findings: list[SecurityFinding]
    verdict: str  # PASS, PASS with warnings, FAIL
    env_check: str  # confirms no .env in changeset

class PRResult(BaseModel):
    pr_url: str
    branch_name: str
    pr_number: int
