"""Fetch failed CI job logs from GitHub Actions using the locally stored git credential."""
import json
import os
import subprocess
import sys
import urllib.request
import zipfile

RUN_ID = sys.argv[1] if len(sys.argv) > 1 else "32459161481"
JOB_NAME = sys.argv[2] if len(sys.argv) > 2 else "Python 3.12"
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


class _NoAuthRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub redirects log downloads to blob storage; the API token must not follow."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = urllib.request.Request(newurl, headers={"User-Agent": "ci-log-fetch"})
        return new


_opener = urllib.request.build_opener(_NoAuthRedirect)


def api(path: str) -> bytes:
    req = urllib.request.Request(
        f"https://api.github.com/{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "ci-log-fetch",
        },
    )
    with _opener.open(req) as resp:
        return resp.read()


if RUN_ID == "latest":
    runs = json.loads(api(f"repos/{REPO}/actions/runs?per_page=1"))
    RUN_ID = str(runs["workflow_runs"][0]["id"])
jobs = json.loads(api(f"repos/{REPO}/actions/runs/{RUN_ID}/jobs"))
job = next(j for j in jobs["jobs"] if j["name"] == JOB_NAME)
print(f"job id={job['id']} conclusion={job['conclusion']}")

raw = api(f"repos/{REPO}/actions/jobs/{job['id']}/logs")
out_dir = os.path.join(os.environ["TEMP"], "ci_logs")
zip_path = os.path.join(os.environ["TEMP"], "ci_logs.zip")
with open(zip_path, "wb") as fh:
    fh.write(raw)
zipfile.ZipFile(zip_path).extractall(out_dir)
for name in sorted(os.listdir(out_dir)):
    print("extracted:", name)
