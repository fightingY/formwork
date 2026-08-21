"""Print status/conclusion of the latest CI run on a branch; exit 0 once completed."""
import json
import subprocess
import sys
import urllib.request

BRANCH = sys.argv[1] if len(sys.argv) > 1 else "codex/stable-v3.2"
REPO = "fightingY/mini-claude-code"

cred = subprocess.run(
    ["git", "credential", "fill"],
    input="protocol=https\nhost=github.com\n\n",
    capture_output=True,
    text=True,
    check=True,
).stdout
token = next(
    line[len("password="):] for line in cred.splitlines() if line.startswith("password=")
)

req = urllib.request.Request(
    f"https://api.github.com/repos/{REPO}/actions/runs?branch={BRANCH}&per_page=1",
    headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ci-status",
    },
)
with urllib.request.urlopen(req) as resp:
    run = json.load(resp)["workflow_runs"][0]

print(f"run #{run['run_number']} {run['head_sha'][:7]} status={run['status']} conclusion={run['conclusion']}")
if run["status"] != "completed":
    sys.exit(2)

jobs = json.loads(
    urllib.request.urlopen(
        urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/actions/runs/{run['id']}/jobs",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "ci-status",
            },
        )
    ).read()
)
for job in jobs["jobs"]:
    print(job["name"], job["conclusion"])
sys.exit(0 if run["conclusion"] == "success" else 1)
