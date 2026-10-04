"""Verify 0.2 -> 0.5 Worker recovery against an isolated PostgreSQL/Temporal stack."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import time
import zipfile
import uuid

from smoke import start, create, request, await_state, budget, assert_agent_complete, assert_budget_complete

REPORT = Path(__file__).resolve().parents[1] / 'var/p4-upgrade-result.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-jar', required=True)
    parser.add_argument('--new-jar', default='platform-api/target/platform-api-0.1.0-SNAPSHOT.jar')
    parser.add_argument('--port', type=int, default=9095)
    args = parser.parse_args()
    jars = [Path(args.old_jar).resolve(), Path(args.new_jar).resolve()]
    artifacts = []
    for jar, version in zip(jars, ['0.2.0', '0.5.0']):
        with zipfile.ZipFile(jar) as archive:
            assert f'BOOT-INF/lib/agent-permit-core-{version}.jar' in archive.namelist()
        artifacts.append({'agentPermit': version, 'sha256': hashlib.sha256(jar.read_bytes()).hexdigest()})
    for port in [args.port, args.port + 1]:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
    if not os.environ.get('PLATFORM_DATABASE_PASSWORD'):
        raise RuntimeError('Set PLATFORM_DATABASE_PASSWORD for the isolated database')
    os.environ['PLATFORM_OPERATOR_PASSWORD'] = secrets.token_urlsafe(32)
    os.environ['PLATFORM_APPROVER_PASSWORD'] = secrets.token_urlsafe(32)
    os.environ['PLATFORM_MODEL_PROVIDER'] = 'OFFLINE'
    root = Path(__file__).resolve().parents[1]
    process = None
    results = []
    with (root / 'var/p4-upgrade-application.log').open('w', encoding='utf-8') as log:
        try:
            old_base = f'http://127.0.0.1:{args.port}'
            new_base = f'http://127.0.0.1:{args.port + 1}'
            process = start(jars[0], args.port, log, delivery=False)
            run_id, _ = create(old_base)
            pending = await_state(old_base, run_id, 'WAITING_APPROVAL')
            path = f'/api/runs/{run_id}/approval'
            old_record = request(old_base, 'GET', path)
            old_budget = budget(old_base, run_id)
            process.kill()
            process.wait(timeout=15)
            process = start(jars[1], args.port + 1, log, delivery=False)
            assert await_state(new_base, run_id, 'WAITING_APPROVAL') == pending
            assert request(new_base, 'GET', path) == old_record
            assert budget(new_base, run_id) == old_budget
            decision = {'approvalId': pending['approvalId'], 'decision': 'APPROVE'}
            request(new_base, 'POST', path, decision, 202)
            process.kill()
            process.wait(timeout=15)
            process = start(jars[1], args.port + 1, log)
            assert_agent_complete(await_state(new_base, run_id, 'SUCCEEDED'))
            assert_budget_complete(new_base, run_id)
            operation_path = f'/api/runs/{run_id}/operation'
            generation = request(new_base, 'GET', operation_path)['serviceGeneration']
            request(new_base, 'POST', path, decision, 202)
            assert request(new_base, 'GET', operation_path)['serviceGeneration'] == generation
            results.append({'case': 'old_pending_approval', 'runId': run_id, 'status': 'PASS'})
            print('PASS old 0.2 approval and budget survive 0.5 process recovery', flush=True)

            process.kill()
            process.wait(timeout=15)
            process = start(jars[0], args.port, log, failure_mode='PAUSE_AFTER_LEDGER_COMMIT')
            run_id, _ = create(old_base)
            pending = await_state(old_base, run_id, 'WAITING_APPROVAL')
            operation_path = f'/api/runs/{run_id}/operation'
            generation = request(old_base, 'GET', operation_path)['serviceGeneration']
            decision = {'approvalId': pending['approvalId'], 'decision': 'APPROVE'}
            request(old_base, 'POST', f'/api/runs/{run_id}/approval', decision, 202)
            deadline = time.monotonic() + 8
            while True:
                committed = request(old_base, 'GET', operation_path)
                if committed['receipt'] is not None:
                    assert committed['execution']['status'] == 'IN_PROGRESS'
                    assert committed['serviceGeneration'] == generation + 1
                    break
                if time.monotonic() >= deadline:
                    raise AssertionError('Old ledger commit not observed before Activity timeout')
                time.sleep(0.05)
            process.kill()
            process.wait(timeout=15)
            process = start(jars[1], args.port + 1, log)
            completed = await_state(new_base, run_id, 'SUCCEEDED')
            assert_agent_complete(completed)
            assert_budget_complete(new_base, run_id)
            recovered = request(new_base, 'GET', operation_path)
            assert recovered['receipt'] == committed['receipt']
            assert recovered['execution']['output'] == completed['output']
            assert recovered['serviceGeneration'] == generation + 1
            request(new_base, 'POST', f'/api/runs/{run_id}/approval', decision, 202)
            assert request(new_base, 'GET', operation_path)['serviceGeneration'] == generation + 1
            results.append({'case': 'old_committed_receipt', 'runId': run_id, 'status': 'PASS'})
            print('PASS old 0.2 committed receipt recovers under 0.5 without another ledger write', flush=True)
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=15)
    report = json.loads(REPORT.read_text(encoding='utf-8'))
    report.update({
        'status': 'PASS',
        'scope': 'isolated PostgreSQL and real Temporal; offline model and demo ledger',
        'artifacts': artifacts, 'results': results,
    })
    REPORT.write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps({'attemptId': str(uuid.uuid4()), 'status': 'RUNNING',
        'startedAt': datetime.now(timezone.utc).isoformat(),
        'scriptSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'composeProject': os.environ.get('COMPOSE_PROJECT_NAME')}, indent=2), encoding='utf-8')
    try:
        main()
    except BaseException:
        report = json.loads(REPORT.read_text(encoding='utf-8'))
        report['status'] = 'FAILED'
        REPORT.write_text(json.dumps(report, indent=2), encoding='utf-8')
        raise
    finally:
        report = json.loads(REPORT.read_text(encoding='utf-8'))
        report['finishedAt'] = datetime.now(timezone.utc).isoformat()
        REPORT.write_text(json.dumps(report, indent=2), encoding='utf-8')
