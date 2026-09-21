import anthropic
import subprocess
import tempfile
import shutil
import re
import os
from models import CodeChanges

MAX_RETRIES = 3


PATCH_SYSTEM_PROMPT = """You are a senior software engineer fixing failing tests.
You will receive the code you wrote and the exact pytest error output.

Return the corrected complete file contents using this EXACT format with XML tags.
No explanation before or after. Start immediately with the first <file> tag.

<file path="routes/users.py">
complete corrected file content here
</file>

Include ALL files from the original changeset, not just the ones you changed.

For rate limit tests always use EXACTLY this mock pattern:
  def mock_check(request, endpoint, limit_provider):
      raise RateLimitExceeded("100 per 1 minute")
  with patch.object(app.state.limiter, "_check_request_limit", side_effect=mock_check):
      response = client.get("/api/users", headers=AUTH_HEADERS)
      assert response.status_code == 429

The mock_check function MUST have exactly three arguments: request, endpoint, limit_provider."""

def run_verifier(
    client: anthropic.Anthropic,
    changes: CodeChanges,
    repo_path: str
) -> tuple[CodeChanges, int, str]:

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n    Attempt {attempt}/{MAX_RETRIES} - writing files and running pytest...")

        temp_dir = tempfile.mkdtemp()
        try:
            shutil.copytree(repo_path, temp_dir, dirs_exist_ok=True)

            for filepath, content in changes.files.items():
                full_path = os.path.join(temp_dir, filepath)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)

            result = subprocess.run(
                ["python", "-m", "pytest", "-v", "--tb=short"],
                capture_output=True,
                text=True,
                cwd=temp_dir
            )

            test_output = result.stdout + result.stderr
            passed = result.returncode == 0

            if passed:
                print(f"    Tests passed on attempt {attempt}")
                print(f"    {_extract_summary(test_output)}")
                return changes, attempt, test_output
            else:
                print(f"    Tests FAILED on attempt {attempt}")
                print(f"    {_extract_summary(test_output)}")

                if attempt < MAX_RETRIES:
                    print(f"    Sending error to Claude for patch...")
                    new_changes = _patch_with_claude(client, changes, test_output)
                    # Merge: keep all existing files, override with patched ones
                    merged = dict(changes.files)
                    merged.update(new_changes.files)
                    changes = CodeChanges(files=merged)
                else:
                    raise RuntimeError(
                        f"Tests still failing after {MAX_RETRIES} attempts.\n"
                        f"Last error:\n{test_output[-2000:]}"
                    )

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    raise RuntimeError("Verification loop exited unexpectedly")


def _patch_with_claude(
    client: anthropic.Anthropic,
    changes: CodeChanges,
    error_output: str
) -> CodeChanges:

    files_text = "\n\n".join(
        f"--- {path} ---\n{content}"
        for path, content in changes.files.items()
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        system=PATCH_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Here is the code I wrote:\n\n{files_text}\n\n"
                    f"Here is the pytest error:\n\n{error_output[-3000:]}\n\n"
                    "Fix the code so all tests pass. "
                    "Return ALL files using <file path='...'> XML tags. "
                    "Start with the first <file> tag immediately."
                )
            }
        ]
    )

    raw = response.content[0].text.strip()

    pattern = r'<file path=["\']([^"\']+)["\']>\n?(.*?)\n?</file>'
    matches = re.findall(pattern, raw, re.DOTALL)

    if not matches:
        raise ValueError(f"Patch returned no file blocks. Raw:\n{raw[:500]}")

    files = {}
    for filepath, content in matches:
        files[filepath.strip()] = content.strip()

    return CodeChanges(files=files)


def _extract_summary(output: str) -> str:
    for line in reversed(output.splitlines()):
        line = line.strip()
        if "passed" in line or "failed" in line or "error" in line:
            return line
    return "No summary found"
