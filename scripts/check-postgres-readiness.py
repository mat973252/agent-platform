"""Reproduce socket-only initialization readiness with a disposable PostgreSQL container."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import uuid

root = Path(__file__).resolve().parents[1]
fixture = root / 'var/readiness-hold.sh'
fixture.parent.mkdir(exist_ok=True)
fixture.write_text('#!/bin/sh\necho READINESS_INIT_HOLD\nsleep 15\n', encoding='utf-8', newline='\n')
name = 'p4-readiness-' + uuid.uuid4().hex[:12]
env = dict(os.environ, POSTGRES_PASSWORD=secrets.token_urlsafe(32))
report_path = root / 'var/p4-readiness-result.json'
report = {'attemptId': name, 'status': 'RUNNING'}
report_path.write_text(json.dumps(report), encoding='utf-8')
def docker(*args, check=True):
    return subprocess.run(['docker', *args], check=check, capture_output=True, text=True, env=env)
def probe(tcp=False):
    return docker('exec', name, 'pg_isready', *(['-h', '127.0.0.1'] if tcp else []),
                  '-U', 'fixture', '-d', 'fixture', check=False).returncode
try:
    docker('run', '-d', '--name', name, '--network', 'none', '-e', 'POSTGRES_PASSWORD',
           '-e', 'POSTGRES_USER=fixture', '-e', 'POSTGRES_DB=fixture',
           '--mount', f'type=bind,source={fixture},target=/docker-entrypoint-initdb.d/hold.sh,readonly',
           '--health-cmd', 'pg_isready -U fixture -d fixture', '--health-interval', '1s', 'postgres:16')
    deadline = time.monotonic() + 40
    while 'READINESS_INIT_HOLD' not in docker('logs', name).stdout:
        if time.monotonic() > deadline:
            raise TimeoutError('Initialization hold not reached')
        time.sleep(.2)
    socket_exit, tcp_exit = probe(), probe(True)
    assert socket_exit == 0 and tcp_exit != 0, (socket_exit, tcp_exit)
    print('PASS reproduced: socket readiness accepts temporary server; TCP is unavailable', flush=True)
    while probe(True) != 0:
        if time.monotonic() > deadline:
            raise TimeoutError('Final TCP server not ready')
        time.sleep(.2)
    assert probe() == 0
    report.update({'status': 'PASS', 'scope': 'controlled initialization hold; not proof of the earlier schema failure cause',
              'temporaryServer': {'socketExit': socket_exit, 'tcpExit': tcp_exit},
              'finalServer': {'socketExit': 0, 'tcpExit': 0}})
    print('PASS explicit TCP readiness accepts final server only', flush=True)
finally:
    if report['status'] != 'PASS':
        report['status'] = 'FAILED'
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    docker('rm', '-f', '-v', name, check=False)
