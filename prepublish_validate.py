"""Pre-publication leak guard. Fails if the repo holds anything but allowlisted,
clean files - in the working tree or anywhere in Git history.

Defense in depth only; it does not replace reading every file before a push.
"""
from pathlib import Path
import re
import subprocess
import sys

ALLOWED = {
    "README.md", "AGENTS.md", "targets.json", "qa_prod_agent.py",
    "prepublish_validate.py",
    ".github/workflows/canary.yml", ".github/workflows/checks.yml",
}
REQUIRED = {"qa_prod_agent.py", "targets.json", ".github/workflows/checks.yml"}
CODE_FILES = {"qa_prod_agent.py", "targets.json",
              ".github/workflows/canary.yml", ".github/workflows/checks.yml"}
DENIED = (
    "secrets.", "service_role", "supabase", "private_key", "github_token",
    "gh_token", "protection_bypass", "upload-artifact", "actions/cache",
    "pull_request", "id-token", "workflow_run", "repository_dispatch",
    "ssh-key", "storage_state", "screenshot(", "record_video", "record_har",
    "self-hosted", "authorization", "set-cookie", "password",
)
TOKEN_SHAPES = (
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{18,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"sb_(?:secret|publishable)_[A-Za-z0-9_-]{10,}"),
)
SELF = "prepublish_validate.py"  # holds the patterns themselves


def inspect_content(rel, data):
    faults = []
    text = data.decode("utf-8", "replace")
    if rel in CODE_FILES and any(t in text.lower() for t in DENIED):
        faults.append("forbidden_content")
    if rel != SELF and any(p.search(text) for p in TOKEN_SHAPES):
        faults.append("token_shape")
    if len(data) > 150_000:
        faults.append("oversized_file")
    return faults


def inspect_history(root):
    if not (root / ".git").exists():
        return []
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, timeout=30, check=True).stdout
    faults = []
    try:
        commits = git("rev-list", "--all").decode().split()
        if len(commits) > 400:
            return ["history_too_large_for_guard"]
        for commit in commits:
            for name in git("ls-tree", "-r", "--name-only", commit).decode().splitlines():
                if name not in ALLOWED:
                    faults.append("unexpected_historical_file")
                    continue
                faults.extend(inspect_content(name, git("show", f"{commit}:{name}")))
    except Exception:
        faults.append("history_check_failed")
    return faults


def inspect(root):
    root = Path(root)
    faults = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or ".git" in p.relative_to(root).parts[:1]:
            continue
        rel = p.relative_to(root).as_posix()
        if rel not in ALLOWED:
            faults.append("unexpected_file")
            continue
        faults.extend(inspect_content(rel, p.read_bytes()))
    faults.extend("missing_required" for rel in REQUIRED if not (root / rel).exists())
    faults.extend(inspect_history(root))
    return faults


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
    found = inspect(target)
    print("SAFE_TO_REVIEW" if not found else "BLOCKED")
    sys.exit(0 if not found else 1)
