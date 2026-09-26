"""Exercise the packaged API and kill/restart its Worker against an external Temporal server."""

import argparse
import base64
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def request(base, method, path, payload=None, expected=200, actor=None):
    data = None if payload is None else json.dumps(payload).encode()
    actor = actor or ("approver" if method == "POST" and path.endswith("/approval") else "operator")
    credentials = actor + ":" + os.environ[f"PLATFORM_{actor.upper()}_PASSWORD"]
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", "X-Platform-Request": "true",
                                          "Authorization": "Basic " + base64.b64encode(credentials.encode()).decode()})
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read().decode()
        if response.status != expected:
            raise AssertionError(f"{method} {path}: expected {expected}, got {response.status}: {body}")
        return json.loads(body) if body else None


def await_state(base, run_id, state):
    deadline = time.monotonic() + 30
    latest = None
    while time.monotonic() < deadline:
        latest = request(base, "GET", f"/api/runs/{run_id}")
        if latest["state"] == state:
            return latest
        time.sleep(0.2)
    raise AssertionError(f"Expected {state}, got {latest}")


def assert_agent_complete(snapshot):
    agent = snapshot["agent"]
    assert agent["modelSteps"] == 3
    assert agent["modelVersion"] == "offline-diagnostics-v1"
    assert agent["lastDecision"]["action"] == "FINISH"
    assert agent["observation"].startswith("VERIFICATION_CONFIRMED")
    assert "No real service" in agent["conclusion"]


def budget(base, run_id):
    return request(base, "GET", f"/api/runs/{run_id}/budget")


def assert_budget_complete(base, run_id, held=0, attempts=3):
    value = budget(base, run_id)
    assert value["meteringMode"] == "OFFLINE_SIMULATED"
    assert value["usedTokens"] == 2784 and value["usedCostMicrousd"] == 3000
    assert value["reservedTokens"] == held and value["modelAttempts"] == attempts
    assert value["reservedCostMicrousd"] == (5000 if held else 0)


def assert_event_resume(base, run_id, cursor, final_event_id):
    credentials = "approver:" + os.environ["PLATFORM_APPROVER_PASSWORD"]
    req = urllib.request.Request(base + f"/api/runs/{run_id}/events/stream",
                                 headers={"Last-Event-ID": cursor,
                                          "Authorization": "Basic " + base64.b64encode(credentials.encode()).decode()})
    with urllib.request.urlopen(req, timeout=15) as response:
        assert response.headers["Content-Type"].startswith("text/event-stream")
        lines = response.read().decode().splitlines()
    prefix, after = cursor.rsplit(":", 1)
    ids = [line[3:].strip() for line in lines if line.startswith("id:")]
    assert ids == [f"{prefix}:{number}" for number in range(int(after) + 1, final_event_id + 1)]


def assert_run_listing(base, expected):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        found, token, seen_tokens = set(), "", set()
        for _ in range(100):
            page = request(base, "GET", "/api/runs?limit=2&pageToken=" + token)
            ids = {run["runId"] for run in page["runs"]}
            assert not found.intersection(ids), "Run pagination repeated an execution"
            found.update(ids)
            token = page["nextPageToken"]
            if not token:
                break
            assert token not in seen_tokens, "Run pagination did not advance"
            seen_tokens.add(token)
        if expected.issubset(found):
            return
        time.sleep(0.2)
    raise AssertionError("Temporal visibility did not expose the smoke Runs")


def assert_status_export(root, base, succeeded_run_id, cancelled_run_id):
    """Run the real exporter against this live API and check the produced document."""
    output = root / "var/mat-console-status.json"
    deadline = time.monotonic() + 30
    while True:
        subprocess.run([sys.executable, str(root / "scripts/export-status.py"), "--base-url", base,
                        "--limit", "100", "--output", str(output)], cwd=root, check=True, capture_output=True)
        document = json.loads(output.read_text(encoding="utf-8"))
        exported = {run["id"]: run for run in document["runs"]}
        if succeeded_run_id in exported and cancelled_run_id in exported:
            break
        if time.monotonic() >= deadline:
            raise AssertionError("Temporal visibility did not expose the smoke Runs to the exporter")
        time.sleep(0.2)
    assert document["contract"] == "mat-console.status/1"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})", document["generated_at"])
    assert document["project"]["id"] and document["project"]["name"]
    assert exported[succeeded_run_id]["status"] == "succeeded"
    assert exported[cancelled_run_id]["status"] == "cancelled"
    assert document["health"]["state"] in ("ok", "attention", "degraded", "unknown")
    assert "progress" not in document and "milestones" not in document
    forbidden = re.compile(r"(secret|token|passw(?:or)?d|credential|api[_-]?key|bearer|private[_-]?key"
                           r"|evidence$|output|approvalId|operationId|executionId|agent)", re.IGNORECASE)
    def walk(node, path="$"):
        if isinstance(node, dict):
            for key, value in node.items():
                assert not forbidden.search(key), f"Forbidden key {path}.{key} in exported status"
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
    walk(document)
    serialized = json.dumps(document)
    assert "TEST_LEDGER_RESTART" not in serialized and "Basic " not in serialized
    assert os.environ["PLATFORM_OPERATOR_PASSWORD"] not in serialized
    print(f"PASS mat-console status export: {len(document['runs'])} runs from the live API; "
          f"{succeeded_run_id} mapped to succeeded; health={document['health']['state']}", flush=True)


def rebuild_projection(root, base, run_id):
    # Delete only the read model for this smoke-created UUID, never its execution or business ledger.
    assert str(uuid.UUID(run_id.removeprefix("run-"))) == run_id.removeprefix("run-")
    before = request(base, "GET", f"/api/runs/{run_id}/events?limit=500")
    statements = [f"DELETE FROM {table} WHERE run_id='{run_id}'" for table in
                  ("platform_run_events", "platform_run_steps", "platform_run_views")]
    subprocess.run(["docker", "compose", "exec", "-T", "approval-db", "psql", "-U", "agent_platform",
                    "-d", "agent_platform", "-v", "ON_ERROR_STOP=1", "-c",
                    "BEGIN; " + "; ".join(statements) + "; COMMIT;"], cwd=root, check=True, capture_output=True)
    assert request(base, "GET", f"/api/runs/{run_id}/events?limit=500") == before


def start(jar, port, output, delivery=True, failure_mode="NONE", model_failure_mode="NONE"):
    process = subprocess.Popen(
        ["java", "-jar", str(jar), f"--server.port={port}",
         f"--platform.approval-delivery.enabled={str(delivery).lower()}",
         f"--platform.demo.failure-mode={failure_mode}",
         f"--platform.demo.model-failure-mode={model_failure_mode}"],
        stdout=output, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 90
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Application exited; inspect var/smoke.log")
            try:
                if request(base, "GET", "/actuator/health")["status"] == "UP":
                    return process
            except (OSError, AssertionError):
                pass
            time.sleep(0.5)
        raise TimeoutError("Application did not start; inspect var/smoke.log")
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=15)
        raise


def create(base, seconds=300):
    request_id = str(uuid.uuid4())
    payload = {"requestId": request_id, "service": "orders", "approvalTimeoutSeconds": seconds}
    created = request(base, "POST", "/api/runs", payload, 202)
    return created["runId"], payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jar", default="platform-api/target/platform-api-0.1.0-SNAPSHOT.jar")
    parser.add_argument("--port", type=int, default=9091)
    parser.add_argument("--restart-approval-db", action="store_true",
                        help="Restart this Compose project's approval-db during the recovery check")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    jar = (root / args.jar).resolve()
    if not jar.is_file():
        raise FileNotFoundError("Build the application with Maven verify first")
    for port in (args.port, args.port + 1):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    base = f"http://127.0.0.1:{args.port}"
    if not os.environ.get("PLATFORM_DATABASE_PASSWORD"):
        raise RuntimeError("Set PLATFORM_DATABASE_PASSWORD to match the approval-db container")
    # Ephemeral test credentials are passed only through the child process environment.
    os.environ["PLATFORM_OPERATOR_PASSWORD"] = secrets.token_urlsafe(32)
    os.environ["PLATFORM_APPROVER_PASSWORD"] = secrets.token_urlsafe(32)
    (root / "var").mkdir(exist_ok=True)
    process = None
    with (root / "var/smoke.log").open("w", encoding="utf-8") as output:
        try:
            process = start(jar, args.port, output, delivery=False)
            run_id, payload = create(base)
            pending = await_state(base, run_id, "WAITING_APPROVAL")
            assert pending["reasonCode"] == "DEMO_RESTART_REQUIRES_APPROVAL"
            assert pending["agent"]["modelSteps"] == 1
            assert pending["agent"]["lastDecision"]["tool"] == "ops.restart"
            pending_budget = budget(base, run_id)
            assert pending_budget["usedTokens"] == 928 and pending_budget["reservedTokens"] == 0
            assert pending_budget["modelAttempts"] == 1
            pending_history = request(base, "GET", f"/api/runs/{run_id}/history")
            pending_events = request(base, "GET", f"/api/runs/{run_id}/events?limit=500")
            # /events refreshes projectedAt; retain the exact stored view for the restart assertion.
            pending_history = request(base, "GET", f"/api/runs/{run_id}/history?refresh=false")
            approval_path = f"/api/runs/{run_id}/approval"
            original_record = request(base, "GET", approval_path)
            assert original_record["status"] == "PENDING"
            request(base, "POST", "/api/runs", payload, 409)
            first_pid = process.pid
            process.kill()
            process.wait(timeout=15)
            process = start(jar, args.port, output, delivery=False)
            recovered = await_state(base, run_id, "WAITING_APPROVAL")
            assert recovered == pending, "Persisted Run changed across Worker restart"
            assert budget(base, run_id) == pending_budget
            assert request(base, "GET", approval_path) == original_record
            cached_history = request(base, "GET", f"/api/runs/{run_id}/history?refresh=false")
            assert cached_history["run"] == pending_history["run"] and cached_history["steps"] == pending_history["steps"]
            decision = {"approvalId": pending["approvalId"], "decision": "APPROVE"}
            request(base, "POST", approval_path, decision, 403, actor="operator")
            request(base, "POST", approval_path, decision, 202)
            request(base, "POST", approval_path, decision, 202)
            saved_decision = request(base, "GET", approval_path)
            assert saved_decision["decidedBy"] == "approver" and saved_decision["status"] == "APPROVE"
            assert request(base, "GET", f"/api/runs/{run_id}")["state"] == "WAITING_APPROVAL"
            process.kill()
            process.wait(timeout=15)
            if args.restart_approval_db:
                subprocess.run(["docker", "compose", "restart", "approval-db"], cwd=root, check=True)
                subprocess.run(["docker", "compose", "up", "-d", "--wait", "approval-db"], cwd=root, check=True)
            process = start(jar, args.port, output)
            cached_history = request(base, "GET", f"/api/runs/{run_id}/history?refresh=false")
            assert cached_history["run"] == pending_history["run"] and cached_history["steps"] == pending_history["steps"]
            complete = await_state(base, run_id, "SUCCEEDED")
            assert_agent_complete(complete)
            assert_budget_complete(base, run_id)
            assert budget(base, run_id)["deadlineEpochMillis"] == pending_budget["deadlineEpochMillis"]
            assert complete["output"].startswith("TEST_LEDGER_RESTART:orders")
            assert complete["reasonCode"] == "DEMO_RESTART_REQUIRES_APPROVAL"
            completed_record = request(base, "GET", approval_path)
            assert completed_record == dict(saved_decision, executionStarted=True)
            request(base, "POST", approval_path, decision, 202)
            request(base, "POST", "/api/runs", payload, 409)
            final_history = request(base, "GET", f"/api/runs/{run_id}/history")
            assert final_history["run"]["businessState"] == "SUCCEEDED"
            assert len(final_history["modelAttempts"]) == 3
            assert all(attempt["meteringMode"] == "OFFLINE_SIMULATED" for attempt in final_history["modelAttempts"])
            final_events = request(base, "GET", f"/api/runs/{run_id}/events?limit=500")
            assert final_events["events"][:len(pending_events["events"])] == pending_events["events"]
            assert_event_resume(base, run_id, pending_events["nextCursor"], final_events["highWatermark"])
            if args.restart_approval_db:
                rebuild_projection(root, base, run_id)
            print(f"PASS durable history/SSE: {run_id}; resumed after {pending_events['nextCursor']}; "
                  f"final event {final_events['highWatermark']}; stable IDs and projection rebuild", flush=True)
            print(f"PASS restart recovery: {run_id}; Worker {first_pid} -> {process.pid}", flush=True)
            print("PASS persistent approval, authenticated actor, duplicate decision and outbox recovery"
                  + (" with PostgreSQL restart" if args.restart_approval_db else ""), flush=True)

            rejected, _ = create(base)
            approval = await_state(base, rejected, "WAITING_APPROVAL")
            request(base, "POST", f"/api/runs/{rejected}/approval",
                    {"approvalId": approval["approvalId"], "decision": "REJECT"}, 202)
            assert await_state(base, rejected, "REJECTED")["output"] is None
            cancelled, _ = create(base)
            await_state(base, cancelled, "WAITING_APPROVAL")
            request(base, "POST", f"/api/runs/{cancelled}/cancel", expected=202)
            assert await_state(base, cancelled, "CANCELLED")["output"] is None
            expired, _ = create(base, seconds=2)
            assert await_state(base, expired, "TIMED_OUT")["output"] is None
            print("PASS rejection, cancellation, approval timeout and duplicate Run protection", flush=True)
            assert_run_listing(base, {run_id, rejected, cancelled, expired})
            print("PASS Temporal visibility Run listing with pagination", flush=True)

            process.kill()
            process.wait(timeout=15)
            process = start(jar, args.port, output, failure_mode="PAUSE_AFTER_LEDGER_COMMIT")
            interrupted, _ = create(base)
            approval = await_state(base, interrupted, "WAITING_APPROVAL")
            operation_path = f"/api/runs/{interrupted}/operation"
            generation = request(base, "GET", operation_path)["serviceGeneration"]
            decision = {"approvalId": approval["approvalId"], "decision": "APPROVE"}
            request(base, "POST", f"/api/runs/{interrupted}/approval", decision, 202)
            deadline = time.monotonic() + 8
            while True:
                operation = request(base, "GET", operation_path)
                if operation["receipt"] is not None:
                    assert operation["execution"]["status"] == "IN_PROGRESS"
                    assert operation["serviceGeneration"] == generation + 1
                    break
                if time.monotonic() >= deadline:
                    raise AssertionError("Ledger commit was not observed before Activity timeout")
                time.sleep(0.05)
            first_pid = process.pid
            process.kill()
            process.wait(timeout=15)
            process = start(jar, args.port + 1, output)
            base = f"http://127.0.0.1:{args.port + 1}"
            complete = await_state(base, interrupted, "SUCCEEDED")
            assert_agent_complete(complete)
            assert_budget_complete(base, interrupted)
            recovered = request(base, "GET", operation_path)
            assert recovered["receipt"] == operation["receipt"]
            assert recovered["execution"]["output"] == complete["output"]
            assert recovered["serviceGeneration"] == generation + 1
            request(base, "POST", f"/api/runs/{interrupted}/approval", decision, 202)
            assert request(base, "GET", operation_path)["serviceGeneration"] == generation + 1
            print(f"PASS committed ledger crash recovery: {interrupted}; Worker {first_pid} -> "
                  f"{process.pid}; ports {args.port} -> {args.port + 1}; one ledger write", flush=True)

            process.kill()
            process.wait(timeout=15)
            process = start(jar, args.port + 1, output, failure_mode="FAIL_BEFORE_LEDGER_WRITE")
            unknown, _ = create(base)
            approval = await_state(base, unknown, "WAITING_APPROVAL")
            operation_path = f"/api/runs/{unknown}/operation"
            generation = request(base, "GET", operation_path)["serviceGeneration"]
            request(base, "POST", f"/api/runs/{unknown}/approval",
                    {"approvalId": approval["approvalId"], "decision": "APPROVE"}, 202)
            await_state(base, unknown, "RECONCILIATION_REQUIRED")
            request(base, "POST", f"/api/runs/{unknown}/reconciliation", {"action": "CHECK"}, 202)
            process.kill()
            process.wait(timeout=15)
            process = start(jar, args.port + 1, output)
            await_state(base, unknown, "RECONCILIATION_REQUIRED")
            request(base, "POST", f"/api/runs/{unknown}/reconciliation",
                    {"action": "CLOSE", "reason": "Smoke test: unresolved outcome manually closed"}, 202)
            assert await_state(base, unknown, "CLOSED_UNKNOWN")["output"] is None
            closed = request(base, "GET", operation_path)
            assert closed["receipt"] is None and closed["serviceGeneration"] == generation
            assert closed["execution"]["closedBy"] == "operator"
            print("PASS unknown outcome survives restart; manual audited closure without re-execution", flush=True)
            print("PASS offline plan/execute/verify loop and recorded decision survive Worker restarts", flush=True)

            process.kill()
            process.wait(timeout=15)
            process = start(jar, args.port + 1, output, model_failure_mode="PAUSE_AFTER_MODEL_RESPONSE")
            lost_response, _ = create(base)
            deadline = time.monotonic() + 8
            while True:
                # The Run exists before its budget initialization Activity commits.
                value = request(base, "GET", f"/api/runs/{lost_response}")
                if value.get("agent") and value["agent"].get("budget"):
                    try:
                        reserved = budget(base, lost_response)
                    except AssertionError as error:
                        if "got 404:" not in str(error):
                            raise
                    else:
                        paused = f"SYNTHETIC_MODEL_RESPONSE_PAUSED {lost_response}:model:1"
                        if reserved["reservedTokens"] == 2560 and paused in (root / "var/smoke.log").read_text(encoding="utf-8"):
                            assert reserved["modelAttempts"] == 1 and reserved["usedTokens"] == 0
                            break
                if time.monotonic() >= deadline:
                    raise AssertionError("Model reservation was not observed before Activity timeout")
                time.sleep(0.05)
            first_pid = process.pid
            process.kill()
            process.wait(timeout=15)
            process = start(jar, args.port, output)
            base = f"http://127.0.0.1:{args.port}"
            approval = await_state(base, lost_response, "WAITING_APPROVAL")
            recovered_budget = budget(base, lost_response)
            assert recovered_budget["reservedTokens"] == 2560 and recovered_budget["usedTokens"] == 928
            assert recovered_budget["modelAttempts"] == 2
            assert recovered_budget["deadlineEpochMillis"] == reserved["deadlineEpochMillis"]
            attempts = request(base, "GET", f"/api/runs/{lost_response}/history")["modelAttempts"]
            assert len(attempts) == 2 and attempts[0]["attempt"] == 1 and attempts[1]["attempt"] == 2
            assert attempts[0]["status"] == "RESERVED" and attempts[0]["usedTokens"] is None
            assert attempts[1]["status"] == "COMPLETED" and attempts[1]["usedTokens"] == 928
            request(base, "POST", f"/api/runs/{lost_response}/approval",
                    {"approvalId": approval["approvalId"], "decision": "APPROVE"}, 202)
            assert_agent_complete(await_state(base, lost_response, "SUCCEEDED"))
            assert_budget_complete(base, lost_response, held=2560, attempts=4)
            print(f"PASS model response crash: {lost_response}; Worker {first_pid} -> {process.pid}; "
                  "unknown attempt remains reserved and retries are charged separately (simulated units)", flush=True)

            assert_status_export(root, base, run_id, cancelled)
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)


if __name__ == "__main__":
    main()
