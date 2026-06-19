import anthropic
import json
from models import CodeChanges, SecurityReport, SecurityFinding

SYSTEM_PROMPT = """You are a security engineer reviewing code changes before they are merged.

Analyze the code using the OWASP API Security Top 10 (2023) and OWASP Top 10 (2021) -
the same industry-standard categories any AppSec team uses. Check each one for whether
it applies to this specific code, and report what you find. Not every category will be
relevant to every codebase - only report what's actually present.

API1 - Broken Object Level Authorization: can a caller access/modify an object (a user
  record, a file, a resource) they shouldn't have access to, by manipulating an ID?
API2 - Broken Authentication: are credentials hardcoded, is expiry enforced and does the
  expiry mechanism actually survive restarts/multiple processes, is the auth check present
  on every protected path?
API3 - Broken Object Property Level Authorization: can a caller read or write fields they
  shouldn't (e.g. setting their own role to admin in a create/update request)?
API4 - Unrestricted Resource Consumption: rate limiting, request size limits, no caps on
  loops/recursion driven by user input, unbounded in-memory collections that grow forever
API5 - Broken Function Level Authorization: a role/permission system exists in the code
  but a specific function or endpoint doesn't actually check it (RBAC gaps)
API6 - Unrestricted Access to Sensitive Business Flows: can an automated script abuse a
  legitimate flow (e.g. account creation, checkout) at a damaging scale with no friction?
API7 - Server Side Request Forgery: does the code make outbound requests using a
  user-controllable URL/host without validation?
API8 - Security Misconfiguration: missing CORS policy where browser-facing, trusting
  spoofable headers like X-Forwarded-For for security decisions, verbose error messages
  that leak implementation details, default/debug settings left on
A03:2021 - Injection: SQL, command, template injection, or eval()/exec()/os.system()
  with any untrusted input
A06:2021 - Vulnerable and Outdated Components: unused or unnecessary dependencies that
  increase attack surface, dependencies with known CVEs
A05:2021 - Security Misconfiguration (secrets): hardcoded secrets/API keys in NON-TEST
  production source files, .env or credential files included in the changeset

Also flag genuine implementation-correctness issues that would prevent this PR's stated
goal from working as intended - e.g. a rate limiter instance that isn't actually wired
to the app's exception handler, a check whose logic runs in the wrong order to be
effective. These belong under whichever OWASP category is the closest fit (usually API4
or API8), at MEDIUM severity unless they create an active vulnerability, not just a bug.

Severity guidelines:
- HIGH: directly exploitable right now with no special preconditions - injection, a
  missing auth check that lets anyone hit a sensitive endpoint today, a real credential
  committed to source
- MEDIUM: real security or correctness risk, but requires specific infrastructure to
  exploit (e.g. requires a reverse proxy, requires multiple worker processes) or is a
  missing feature rather than a broken mechanism (e.g. RBAC not yet implemented), or is
  an implementation bug that undermines the ticket's goal without being itself exploitable
- LOW: minor issues, edge cases, fragile-but-not-insecure patterns
- INFO: hardcoded tokens in TEST files (standard practice, never flag higher than this),
  style issues, missing version pinning

CRITICAL RULE: Hardcoded tokens in test files (test_*.py, conftest.py) are ALWAYS INFO,
never HIGH or MEDIUM. This is standard, expected testing practice.

Be thorough - check every applicable OWASP category against the actual code, don't stop
after finding a few issues.

Return ONLY a valid JSON object - no explanation, no markdown:
{
  "findings": [
    {
      "severity": "HIGH|MEDIUM|LOW|INFO",
      "file": "path/to/file.py",
      "issue": "Short description of the issue",
      "recommendation": "How to fix it"
    }
  ],
  "verdict": "PASS|PASS with warnings|FAIL",
  "env_check": "No .env or credential files found in changeset"
}

Verdict rules:
- FAIL only if HIGH severity findings exist in production (non-test) code
- PASS with warnings if MEDIUM findings exist
- PASS if only LOW or INFO findings"""


def run_security_review(
    client: anthropic.Anthropic,
    changes: CodeChanges,
    full_repo_context: dict[str, str],
    ticket: dict | None = None,
) -> SecurityReport:

    all_files = dict(full_repo_context)
    all_files.update(changes.files)

    files_text = "\n\n".join(
        f"=== {path} ===\n{content}"
        for path, content in all_files.items()
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    "Please perform a security review of the following code changes "
                    "before they are merged into main:\n\n"
                    f"{files_text}\n\n"
                    "Return ONLY the JSON object starting with { immediately."
                )
            }
        ]
    )

    raw = response.content[0].text.strip()

    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start != -1 and end > start:
        raw = raw[start:end]

    data = json.loads(raw)
    findings = [SecurityFinding(**f) for f in data.get("findings", [])]

    return SecurityReport(
        findings=findings,
        verdict=data.get("verdict", "PASS"),
        env_check=data.get("env_check", "Not checked")
    )
