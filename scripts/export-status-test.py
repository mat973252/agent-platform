"""Deterministic tests for the mat-console.status/1 exporter.

Run with `python3 scripts/export-status-test.py`. Exercises empty, failed,
ambiguous and approval-pending inputs plus the field allowlist, without
touching the API or the clock.
"""

import importlib.util
import json
import pathlib
import re
import sys
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SPEC = importlib.util.spec_from_file_location(
    "export_status", pathlib.Path(__file__).with_name("export-status.py"))
EXPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORTER)

GENERATED_AT = "2026-09-26T01:02:03Z"
ISO_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$")
SECRET_KEY = re.compile(
    r"(secret|token|passw(?:or)?d|credential|api[_-]?key|bearer|private[_-]?key)", re.IGNORECASE)
TOP_LEVEL_KEYS = {"contract", "generated_at", "ttl_seconds", "project", "source", "health", "runs", "attention"}
RUN_KEYS = {"id", "label", "status", "started_at", "finished_at"}
ATTENTION_KEYS = {"id", "title", "detail", "severity"}
RUN_STATUSES = {"succeeded", "failed", "running", "cancelled", "unknown"}


def run_summary(run_id, execution_status, started_at=1758783723000, closed_at=None):
    return {"runId": run_id, "executionId": "exec-" + run_id, "executionStatus": execution_status,
            "startedAt": started_at, "closedAt": closed_at}


def snapshot(state, reason=None):
    return {"runId": "unused", "state": state, "service": "orders",
            "approvalId": "approval-1", "operationId": "op-1",
            "evidence": "synthetic evidence body", "output": "tool output body",
            "reasonCode": reason, "agent": {"conclusion": "model conclusion text"}}


def build(listing_runs, snapshots=None):
    return EXPORTER.build_status({"runs": listing_runs, "nextPageToken": ""}, snapshots or {},
                                 GENERATED_AT, "agent-platform", "Agent Platform", 3600)


def walk_keys(node):
    keys = set()
    if isinstance(node, dict):
        for key, value in node.items():
            keys.add(key)
            keys |= walk_keys(value)
    elif isinstance(node, list):
        for value in node:
            keys |= walk_keys(value)
    return keys


class StatusExportTest(unittest.TestCase):
    def test_empty_run_list_reports_unknown_health(self):
        document = build([])
        self.assertEqual(document["contract"], "mat-console.status/1")
        self.assertEqual(document["generated_at"], GENERATED_AT)
        self.assertEqual(document["runs"], [])
        self.assertEqual(document["attention"], [])
        self.assertEqual(document["health"]["state"], "unknown")
        self.assertIsNone(document.get("progress"))
        self.assertIsNone(document.get("milestones"))

    def test_failed_run_is_distinct_from_execution_status(self):
        # A COMPLETED workflow whose business result is FAILED must surface as failed.
        document = build([run_summary("run-a1b2", "COMPLETED", closed_at=1758783740000)],
                         {"run-a1b2": snapshot("FAILED", "STEP_LIMIT_EXCEEDED")})
        run = document["runs"][0]
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["finished_at"], "2025-09-25T07:02:20.000Z")
        self.assertEqual(document["attention"][0]["severity"], "warn")
        self.assertIn("STEP_LIMIT_EXCEEDED", document["attention"][0]["detail"])
        self.assertEqual(document["health"]["state"], "attention")

    def test_succeeded_run_reports_ok(self):
        document = build([run_summary("run-c3d4", "COMPLETED")],
                         {"run-c3d4": snapshot("SUCCEEDED")})
        self.assertEqual(document["runs"][0]["status"], "succeeded")
        self.assertEqual(document["attention"], [])
        self.assertEqual(document["health"]["state"], "ok")

    def test_unrecognized_business_state_does_not_become_ok(self):
        # A snapshot exists but its state is missing or unknown to this exporter:
        # the run is unknown and health must not claim success.
        for state in (None, "MISSING", ""):
            with self.subTest(state=state):
                document = build([run_summary("run-s1", "COMPLETED")],
                                 {"run-s1": {"state": state} if state is not None else {}})
                self.assertEqual(document["runs"][0]["status"], "unknown")
                self.assertEqual(document["health"]["state"], "unknown")

    def test_running_only_sample_is_unknown_not_ok(self):
        document = build([run_summary("run-s2", "RUNNING")], {"run-s2": snapshot("RUNNING")})
        self.assertEqual(document["runs"][0]["status"], "running")
        self.assertEqual(document["attention"], [])
        self.assertEqual(document["health"]["state"], "unknown")

    def test_mixed_succeeded_and_unknown_sample_is_unknown(self):
        document = build(
            [run_summary("run-s3", "COMPLETED"), run_summary("run-s4", "RUNNING")],
            {"run-s3": snapshot("SUCCEEDED"), "run-s4": snapshot("RUNNING")})
        self.assertEqual(document["health"]["state"], "unknown")
        self.assertIn("1 of 2", document["health"]["summary"])

    def test_ambiguous_completed_execution_reports_unknown(self):
        # Execution COMPLETED but the snapshot cannot be read: no invented business status.
        document = build([run_summary("run-e5f6", "COMPLETED")], {})
        self.assertEqual(document["runs"][0]["status"], "unknown")
        item = document["attention"][0]
        self.assertEqual(item["severity"], "warn")
        self.assertIn("not readable", item["title"])
        self.assertEqual(document["health"]["state"], "attention")

    def test_pending_approval_is_blocked_attention(self):
        document = build([run_summary("run-g7h8", "RUNNING")],
                         {"run-g7h8": snapshot("WAITING_APPROVAL")})
        self.assertEqual(document["runs"][0]["status"], "running")
        item = document["attention"][0]
        self.assertEqual(item["id"], "approval:run-g7h8")
        self.assertEqual(item["severity"], "blocked")

    def test_reconciliation_required_is_blocked_attention(self):
        document = build([run_summary("run-i9j0", "RUNNING")],
                         {"run-i9j0": snapshot("RECONCILIATION_REQUIRED")})
        self.assertEqual(document["attention"][0]["id"], "reconcile:run-i9j0")
        self.assertEqual(document["runs"][0]["status"], "running")

    def test_no_payload_fields_leak_into_output(self):
        document = build([run_summary("run-k1l2", "COMPLETED")],
                         {"run-k1l2": snapshot("FAILED", "POLICY_DENIED")})
        keys = walk_keys(document)
        self.assertTrue(keys <= TOP_LEVEL_KEYS | RUN_KEYS | ATTENTION_KEYS
                        | {"id", "name", "summary", "label", "state", "contract", "generated_at",
                           "ttl_seconds", "url", "evidence_url", "detail", "severity", "title",
                           "status", "started_at", "finished_at", "percent", "basis"})
        self.assertFalse(keys & {"evidence", "output", "agent", "approvalId", "operationId",
                                 "service", "reasonCode", "executionId", "nextPageToken"})
        for item in document["attention"]:
            self.assertTrue(set(item) <= ATTENTION_KEYS)
        # The raw fixture payload strings must not appear anywhere in the document.
        serialized = json.dumps(document)
        for leaked in ("synthetic evidence body", "tool output body", "model conclusion text"):
            self.assertNotIn(leaked, serialized)

    def test_no_credential_looking_keys(self):
        document = build([run_summary("run-m3n4", "RUNNING")],
                         {"run-m3n4": snapshot("WAITING_APPROVAL")})
        for key in walk_keys(document):
            self.assertIsNone(SECRET_KEY.search(key), f"{key} looks like a credential field")

    def test_timestamps_have_explicit_timezone(self):
        document = build([run_summary("run-o5p6", "COMPLETED", closed_at=1758783800000)],
                         {"run-o5p6": snapshot("SUCCEEDED")})
        for value in (document["generated_at"], document["runs"][0]["started_at"],
                      document["runs"][0]["finished_at"]):
            self.assertRegex(value, ISO_TIMESTAMP)
        for run in document["runs"]:
            self.assertIn(run["status"], RUN_STATUSES)

    def test_malformed_and_unknown_enums_become_unknown(self):
        document = build(
            [{"runId": "run-q7r8", "executionStatus": "NOT_A_STATUS"},
             {"runId": "bad id!"}, {"runId": 42}],
            {"run-q7r8": snapshot("BUSINESS_STUFF")})
        self.assertEqual(len(document["runs"]), 1)
        self.assertEqual(document["runs"][0]["id"], "run-q7r8")
        self.assertEqual(document["runs"][0]["status"], "unknown")


class RecordingHandler(BaseHTTPRequestHandler):
    """Second origin: records whether an Authorization header ever arrives."""

    received = []

    def do_GET(self):
        RecordingHandler.received.append(dict(self.headers))
        body = b'{"runs": []}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def redirecting_handler(target):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    return Handler


class CredentialLeakTest(unittest.TestCase):
    """A redirect must not carry the operator Basic credential to another origin."""

    def setUp(self):
        RecordingHandler.received = []
        self.other = ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
        other_url = f"http://127.0.0.1:{self.other.server_address[1]}/api/runs"
        self.api = ThreadingHTTPServer(("127.0.0.1", 0), redirecting_handler(other_url))
        for server in (self.other, self.api):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        self.base_url = f"http://127.0.0.1:{self.api.server_address[1]}"

    def tearDown(self):
        for server in (self.other, self.api):
            server.shutdown()
            server.server_close()

    def test_redirect_is_not_followed_and_credential_is_not_forwarded(self):
        client = EXPORTER.ApiClient(self.base_url, "operator", "test-only-value")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            client.listing(20)
        self.assertEqual(raised.exception.code, 302)
        self.assertEqual(RecordingHandler.received, [])

    def test_snapshot_redirect_is_reported_as_unknown_without_leaking(self):
        client = EXPORTER.ApiClient(self.base_url, "operator", "test-only-value")
        self.assertIsNone(client.snapshot("run-redirect"))
        self.assertEqual(RecordingHandler.received, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
