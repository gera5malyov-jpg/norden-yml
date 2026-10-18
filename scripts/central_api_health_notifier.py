#!/usr/bin/env python3
"""Aggregate three GitHub Actions health artifacts. Send ONE error-only email.

Only this script sends API health email. Never reads secrets from foreign
repositories; it reads only short, sanitized status artifacts. A fine-grained
GH_MONITOR_TOKEN with Actions:read for the private megapolis-smm repository
is required for that source. Its absence is not a confirmed API outage.
"""
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from daily_api_health_check import send_failure_alert

OWNER = "gera5malyov-jpg"
API_BASE = "https://api.github.com"
MAX_AGE_HOURS = 20
SOURCES = [
    ("norden-yml", "daily-api-health-monitor.yml",
     "daily-api-health-", "daily-api-health.json", False),
    ("moex-trade-bot", "api-health-watch.yml",
     "tinvest-api-health-", "tinvest-api-health.json", False),
    ("megapolis-smm", "api-finance-health.yml",
     "finance-api-health-", "finance-api-health.json", True),
]
SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "Megapolis-Central-API-Monitor/1.0",
})


def get(repo, endpoint, private=False, binary=False):
    token = (os.getenv("GH_MONITOR_TOKEN", "").strip() if private else
             os.getenv("GITHUB_TOKEN", "").strip())
    if private and not token:
        raise RuntimeError("Private repo Actions:read token not configured")
    url = f"{API_BASE}/repos/{OWNER}/{repo}/{endpoint.lstrip('/')}"
    headers = {"Authorization": "Bearer " + token} if token else {}
    result = SESSION.get(url, headers=headers, timeout=35)
    if result.status_code != 200:
        # Do not log response body or URL, which can include sensitive data.
        raise RuntimeError("GitHub API HTTP " + str(result.status_code))
    if len(result.content) > 2_000_000:
        raise RuntimeError("GitHub artifact response exceeds safety limit")
    return result.content if binary else result.json()


def recent_status(repo, workflow, prefix, json_filename, private):
    runs = get(repo, f"actions/workflows/{workflow}/runs?per_page=8&branch=main",
               private=private).get("workflow_runs", [])
    if not runs:
        raise RuntimeError("No workflow runs registered")
    latest = runs[0]
    if latest.get("status") != "completed":
        raise RuntimeError("Newest API health check not completed")
    run_id = latest["id"]
    run_started = datetime.fromisoformat(latest["created_at"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    if not timedelta(0) <= now - run_started <= timedelta(hours=MAX_AGE_HOURS):
        raise RuntimeError("Latest API health workflow is stale")
    artifacts = get(repo, f"actions/runs/{run_id}/artifacts?per_page=100",
                    private=private).get("artifacts", [])
    selected = next((x for x in artifacts
                     if x.get("name") == prefix + str(run_id)
                     and not x.get("expired")), None)
    if selected is None:
        raise RuntimeError("Expected sanitized health artifact missing")
    raw = get(repo, f"actions/artifacts/{selected['id']}/zip",
              private=private, binary=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = [n for n in archive.namelist()
                 if Path(n).name == json_filename and not n.startswith("__MACOSX/")]
        if len(names) != 1:
            raise RuntimeError("Sanitized health file not found exactly once")
        meta = archive.getinfo(names[0])
        if meta.file_size > 60_000:
            raise RuntimeError("Health JSON too large")
        body = json.loads(archive.read(names[0]).decode("utf-8"))
    recorded_at = datetime.fromisoformat(body["checked_at"].replace("Z", "+00:00"))
    if not timedelta(minutes=-5) <= now - recorded_at <= timedelta(hours=MAX_AGE_HOURS):
        raise RuntimeError("Health artifact date invalid or stale")
    services = body["services"]
    if not isinstance(services, list) or not 0 < len(services) <= 60:
        raise RuntimeError("Invalid API health status list")
    sanitized = []
    seen = set()
    for item in services:
        if not isinstance(item, dict):
            raise RuntimeError("Unexpected health result type")
        name = str(item.get("service", ""))[:90]
        status = item.get("status")
        if not name or name in seen or status not in {"OK", "FAIL", "NO_KEY", "UNKNOWN"}:
            raise RuntimeError("Invalid or duplicate service status")
        seen.add(name)
        sanitized.append({
            "service": name,
            "status": status,
            "reason": str(item.get("reason") or "")[:200],
            "run_url": f"https://github.com/{OWNER}/{repo}/actions/runs/{run_id}",
        })
    return sanitized


def main():
    combined = []
    incomplete = []
    for repo, workflow, prefix, filename, private in SOURCES:
        try:
            rows = recent_status(repo, workflow, prefix, filename, private)
            combined.extend(rows)
            print(repo, "status files verified, services:", len(rows))
        except Exception as exc:
            # Never confuse unavailable reports with confirmed API failure.
            print("::warning::", repo, "not fully checked:", type(exc).__name__,
                  str(exc)[:120])
            incomplete.append(repo)

    failures = [x for x in combined if x["status"] == "FAIL"]
    checked = [x for x in combined if x["status"] == "OK"]
    print("Confirmed working:", len(checked),
          "confirmed API failures:", len(failures),
          "sources without validated result:", len(incomplete))
    for failure in failures:
        print("::error::", failure["service"], failure["reason"],
              failure["run_url"])
    for failure in failures:
        failure["reason"] += " | GitHub: " + failure["run_url"]

    # A push-triggered dry run verifies artifact collection without sending
    # extra emails. Only scheduled/manual runs notify.
    if os.getenv("API_ALERT_DRY_RUN") == "1":
        print("DRY_RUN: Email intentionally not sent")
        return 2 if incomplete else 0
    mailed = send_failure_alert(failures)
    if incomplete:
        print("::error::Monitoring incomplete: " + ", ".join(incomplete))
    return 2 if incomplete else (0 if mailed else 1)


if __name__ == "__main__":
    raise SystemExit(main())
