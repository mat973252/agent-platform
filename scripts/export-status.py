"""Export a read-only mat-console.status/1 document from the authenticated local P3 API.

The exporter only reads `GET /api/runs` and `GET /api/runs/{runId}` with the operator
account, copies an allowlist of enumerated fields and writes the document to a local
file (default under the ignored `var/` directory). It never prints credentials, never
copies evidence, outputs, prompts or error bodies, and does not derive progress.
"""

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

CONTRACT = "mat-console.status/1"
SOURCE_LABEL = "agent-platform/scripts/export-status.py"
PROJECT_SUMMARY = ("Java + Temporal durable agent execution prototype; "
                   "status reported from the local P3 run API, not a production readiness signal.")
REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")
RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
BUSINESS_STATES = {"CREATED", "RUNNING", "WAITING_APPROVAL", "RECONCILIATION_REQUIRED", "CLOSED_UNKNOWN",
                   "SUCCEEDED", "DENIED", "REJECTED", "TIMED_OUT", "CANCELLED", "FAILED"}
EXECUTION_STATUSES = {"RUNNING", "COMPLETED", "FAILED", "TIMED_OUT", "CANCELED", "TERMINATED", "CONTINUED_AS_NEW"}


def iso_timestamp(epoch_millis):
    """Epoch milliseconds → ISO-8601 with an explicit UTC designator."""
    moment = datetime.fromtimestamp(int(epoch_millis) / 1000, tz=timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def enumerated(value, allowed):
    return value if isinstance(value, str) and value in allowed else None


def reason_code(value):
    return value if isinstance(value, str) and REASON_CODE.match(value) else None


def run_status(execution_status, business_state):
    """Temporal execution status decides running/cancelled/failed; the business result decides the rest.

    A COMPLETED workflow only counts as succeeded when the returned business state is SUCCEEDED;
    a COMPLETED execution without a trustworthy business result is reported as unknown.
    """
    if execution_status == "RUNNING":
        return "running"
    if execution_status == "CANCELED":
        return "cancelled"
    if execution_status in ("FAILED", "TIMED_OUT", "TERMINATED"):
        return "failed"
    if execution_status != "COMPLETED":
        return "unknown"
    if business_state == "SUCCEEDED":
        return "succeeded"
    if business_state in ("FAILED", "DENIED", "REJECTED", "TIMED_OUT"):
        return "failed"
    if business_state == "CANCELLED":
        return "cancelled"
    return "unknown"


def attention_for(run_id, execution_status, business_state, code, snapshot_available):
    detail = f"reasonCode={code}" if code else None
    if business_state == "WAITING_APPROVAL":
        return {"id": f"approval:{run_id}", "title": f"{run_id} is waiting for approval", "severity": "blocked"}
    if business_state == "RECONCILIATION_REQUIRED":
        return {"id": f"reconcile:{run_id}", "title": f"{run_id} has an unknown write result pending reconciliation",
                "severity": "blocked"}
    if business_state == "CLOSED_UNKNOWN":
        return {"id": f"unknown:{run_id}", "title": f"{run_id} was closed manually with an unknown result",
                "severity": "warn"}
    if run_status(execution_status, business_state) == "failed":
        item = {"id": f"failed:{run_id}", "title": f"{run_id} did not succeed", "severity": "warn"}
        if detail:
            item["detail"] = detail
        return item
    if not snapshot_available and execution_status == "COMPLETED":
        return {"id": f"ambiguous:{run_id}", "title": f"{run_id} completed but its business result was not readable",
                "severity": "warn"}
    return None


def map_run(summary, snapshot):
    run_id = summary.get("runId")
    if not isinstance(run_id, str) or not RUN_ID.match(run_id):
        return None, None
    execution_status = enumerated(summary.get("executionStatus"), EXECUTION_STATUSES)
    business_state = enumerated(snapshot.get("state"), BUSINESS_STATES) if isinstance(snapshot, dict) else None
    code = reason_code(snapshot.get("reasonCode")) if isinstance(snapshot, dict) else None
    run = {"id": run_id, "status": run_status(execution_status, business_state)}
    label = run_id
    if business_state:
        label += f" [{business_state}" + (f"/{code}" if code else "") + "]"
    if label != run_id:
        run["label"] = label
    if isinstance(summary.get("startedAt"), int):
        run["started_at"] = iso_timestamp(summary["startedAt"])
    if isinstance(summary.get("closedAt"), int):
        run["finished_at"] = iso_timestamp(summary["closedAt"])
    return run, attention_for(run_id, execution_status, business_state, code, isinstance(snapshot, dict))


def health_for(runs, attention):
    """Conservative health: `ok` only when every sampled run reports a source-backed success."""
    if attention:
        return {"state": "attention",
                "summary": f"{len(attention)} of {len(runs)} sampled runs need attention; local demo runs only."}
    if not runs:
        return {"state": "unknown", "summary": "No runs are visible in the local run list."}
    if all(run["status"] == "succeeded" for run in runs):
        return {"state": "ok",
                "summary": f"All {len(runs)} sampled local demo runs reported a succeeded business result."}
    unresolved = sum(1 for run in runs if run["status"] != "succeeded")
    return {"state": "unknown",
            "summary": f"{unresolved} of {len(runs)} sampled runs have no reported business result yet."}


def build_status(listing, snapshots, generated_at, project_id, project_name, ttl_seconds):
    """Pure mapping from API responses to a mat-console.status/1 document."""
    runs, attention = [], []
    for summary in listing.get("runs", []) if isinstance(listing, dict) else []:
        run, item = map_run(summary, snapshots.get(summary.get("runId")))
        if run is None:
            continue
        runs.append(run)
        if item:
            attention.append(item)
    document = {
        "contract": CONTRACT,
        "generated_at": generated_at,
        "ttl_seconds": ttl_seconds,
        "project": {"id": project_id, "name": project_name, "summary": PROJECT_SUMMARY},
        "source": {"label": SOURCE_LABEL},
        "health": health_for(runs, attention),
        "runs": runs,
        "attention": attention,
    }
    return document


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: urllib copies Authorization onto the redirected request,
    which would send the operator credential to whatever origin the response names."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect not followed", headers, fp)


class ApiClient:
    def __init__(self, base_url, username, password, timeout=10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._headers = {"Accept": "application/json", "Authorization": "Basic " + token}
        self._opener = urllib.request.build_opener(NoRedirect)

    def get(self, path, query=None):
        url = self.base_url + path + ("?" + urllib.parse.urlencode(query) if query else "")
        request = urllib.request.Request(url, headers=self._headers, method="GET")
        with self._opener.open(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode())

    def listing(self, limit):
        return self.get("/api/runs", {"limit": limit})

    def snapshot(self, run_id):
        try:
            return self.get("/api/runs/" + urllib.parse.quote(run_id, safe=""))
        except urllib.error.HTTPError as error:
            print(f"warning: snapshot for {run_id} unavailable (HTTP {error.code}); reporting as unknown", file=sys.stderr)
            return None


def now_iso():
    moment = datetime.now(tz=timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:9090", help="local API root (default %(default)s)")
    parser.add_argument("--operator-username", default="operator")
    parser.add_argument("--limit", type=int, default=20, help="run list page size, 1-500 (default %(default)s)")
    parser.add_argument("--project-id", default="agent-platform")
    parser.add_argument("--project-name", default="Agent Platform")
    parser.add_argument("--ttl-seconds", type=int, default=3600)
    parser.add_argument("--output", default=os.path.join("var", "mat-console-status.json"),
                        help="local output file; var/ is git-ignored (default %(default)s)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not 1 <= args.limit <= 500:
        print("error: --limit must be between 1 and 500", file=sys.stderr)
        return 2
    if not args.base_url.startswith(("http://", "https://")) or "@" in args.base_url:
        print("error: --base-url must be an http(s) URL without embedded credentials", file=sys.stderr)
        return 2
    password = os.environ.get("PLATFORM_OPERATOR_PASSWORD")
    if not password:
        print("error: PLATFORM_OPERATOR_PASSWORD is not set in the environment", file=sys.stderr)
        return 2
    client = ApiClient(args.base_url, args.operator_username, password)
    try:
        listing = client.listing(args.limit)
    except urllib.error.HTTPError as error:
        print(f"error: GET /api/runs failed with HTTP {error.code}", file=sys.stderr)
        return 1
    except urllib.error.URLError:
        print("error: local API is not reachable", file=sys.stderr)
        return 1
    run_ids = [summary.get("runId") for summary in listing.get("runs", []) if isinstance(summary.get("runId"), str)]
    snapshots = {run_id: client.snapshot(run_id) for run_id in run_ids}
    document = build_status(listing, snapshots, now_iso(), args.project_id, args.project_name, args.ttl_seconds)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8", newline="\n") as output:
        json.dump(document, output, indent=2, ensure_ascii=False)
        output.write("\n")
    print(f"wrote {args.output}: {len(document['runs'])} runs, {len(document['attention'])} attention items, "
          f"health={document['health']['state']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
