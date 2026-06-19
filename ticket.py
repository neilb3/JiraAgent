# Switch between tickets by changing ACTIVE_TICKET
# PROJ-42: Add rate limiting (main demo ticket)
# PROJ-43: Fix test credentials with conftest.py
# PROJ-44: Replace token state with JWT auth

ACTIVE_TICKET = "PROJ-42"

TICKETS = {
    "PROJ-42": {
        "id": "PROJ-42",
        "title": "Add rate limiting to /api/users endpoint",
        "description": (
            "The /api/users endpoint is receiving high-volume requests causing "
            "performance degradation and occasional service outages. "
            "Implement rate limiting configurable via RATE_LIMIT_PER_MINUTE "
            "environment variable so different environments can use different limits."
        ),
        "acceptance_criteria": [
            "Rate limit GET /api/users using RATE_LIMIT_PER_MINUTE env var (default 100/minute)",
            "Return HTTP 429 Too Many Requests when limit is exceeded",
            "Include Retry-After header in 429 response",
            "Add unit tests verifying 429 response by making real requests",
            "Tests must set RATE_LIMIT_PER_MINUTE=3 so limit triggers without 100 requests",
            "Do not break existing passing tests",
        ],
        "priority": "HIGH",
        "reporter": "Preeti Awasthi",
        "labels": ["performance", "security", "api"],
        "branch_name": "feat/PROJ-42-rate-limiting",
    },

    "PROJ-43": {
        "id": "PROJ-43",
        "title": "Fix test credential handling using conftest.py",
        "description": (
            "Security review of PROJ-42 flagged hardcoded API tokens in tests/test_users.py. "
            "Tokens are set directly via os.environ in the test file which is committed "
            "to source control. Fix by moving test environment setup to conftest.py "
            "so no credentials appear in the test source file itself."
        ),
        "acceptance_criteria": [
            "Create tests/conftest.py that sets API_TOKENS, TOKEN_EXPIRY_SECONDS and RATE_LIMIT_PER_MINUTE before app import",
            "Remove all os.environ assignments from tests/test_users.py",
            "tests/test_users.py should contain only imports and test functions",
            "All 8 existing tests must still pass",
            "No token values should appear anywhere in tests/test_users.py",
        ],
        "priority": "MEDIUM",
        "reporter": "Security Review Bot",
        "labels": ["security", "testing", "credentials"],
        "branch_name": "feat/PROJ-43-fix-test-credentials",
    },

    "PROJ-44": {
        "id": "PROJ-44",
        "title": "Replace in-process token state with JWT authentication",
        "description": (
            "Security review flagged that token expiry is tracked in an in-process "
            "dictionary which resets on every server restart and fails in multi-worker "
            "deployments. Replace with JWT tokens so expiry is baked into the token "
            "itself and works across workers and restarts without server-side state."
        ),
        "acceptance_criteria": [
            "Replace static token lookup in utils/auth.py with JWT verification using python-jose",
            "JWT tokens must include exp claim for expiry enforcement",
            "Expiry must work correctly across server restarts without in-process state",
            "Add generate_token() utility function for creating test JWTs",
            "Update tests/conftest.py to generate valid JWT tokens",
            "All existing endpoint tests must still pass",
            "Token expiry test must verify expired token returns 401",
        ],
        "priority": "HIGH",
        "reporter": "Security Review Bot",
        "labels": ["security", "authentication", "jwt"],
        "branch_name": "feat/PROJ-44-jwt-authentication",
    },
}

JIRA_TICKET = TICKETS[ACTIVE_TICKET]
