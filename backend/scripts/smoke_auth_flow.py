"""End-to-end check against a *running* server: signup -> verify -> login -> me -> logout.

Nothing here is mocked - it speaks HTTP to uvicorn, so it exercises the wiring pytest
deliberately skips: a real server process, real middleware, real cookie flags, and a
real Celery worker delivering the mail. Run it while the API (and the worker, unless
EMAIL_BACKEND=console with a broker-less setup) is up:

    uvicorn app.main:app --reload
    celery -A app.core.celery_app worker -Q email --loglevel=INFO
    python scripts/smoke_auth_flow.py

The 6-digit codes come from wherever your mail backend puts them:

    --otp-file /tmp/worker.log   grep the code out of a `EMAIL_BACKEND=console` log
    --otp 123456                 you read the code off the terminal yourself
    (neither)                    the script asks

Exits 0 with one line per step; any unexpected status prints the response body and
exits 1, so it is safe to drop into CI next to a docker-compose stack.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

PASSWORD = 'Str0ng!Pass1'
_CODE = re.compile(r'verification code is ([0-9]{6})')


def _ok(step: str, detail: str = '') -> None:
    print(f'ok   {step}{f" - {detail}" if detail else ""}')


def _fail(step: str, response: httpx.Response) -> None:
    print(f'FAIL {step}: {response.status_code} {response.text[:500]}', file=sys.stderr)
    raise SystemExit(1)


class LogCodes:
    """Pulls the codes out of a console-backend server log, one per request.

    Only ever reads past the position it stopped at, so a code from an earlier run is
    never mistaken for this one.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.pos = os.path.getsize(path) if os.path.exists(path) else 0

    def next(self, *, timeout: float = 25.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            code = self._scan()
            if code:
                return code
            time.sleep(0.2)
        raise SystemExit(f'no verification code appeared in {self.path} within {timeout:.0f}s')

    def _scan(self) -> str | None:
        try:
            with open(self.path, encoding='utf-8', errors='ignore') as fh:
                fh.seek(self.pos)
                chunk = fh.read()
        except FileNotFoundError:
            return None
        match = _CODE.search(chunk)
        if match is None:
            self.pos += len(chunk)
            return None
        self.pos += match.end()
        return match.group(1)


async def run(base_url: str, otp: str | None, otp_file: str | None) -> int:
    email = f'smoke.{uuid.uuid4().hex[:8]}@example.com'
    codes = LogCodes(otp_file) if otp_file else None

    def _code() -> str:
        if otp:
            return otp
        if codes:
            return codes.next()
        return input('6-digit code from the mail log: ').strip()

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0, follow_redirects=False) as client:
        ready = await client.get('/health/ready')
        if ready.status_code != 200:
            _fail('GET /health/ready', ready)
        _ok('health/ready', json.dumps(ready.json()))

        signup = await client.post(
            '/api/v1/auth/signup',
            json={
                'email': email,
                'password': PASSWORD,
                'confirm_password': PASSWORD,
                'first_name': 'Smoke',
                'last_name': 'Tester',
            },
        )
        if signup.status_code != 202:
            _fail('POST /auth/signup', signup)
        challenge = signup.json()
        _ok('signup accepted', f'challenge for {challenge["masked_destination"]}')

        verified = await client.post(
            '/api/v1/auth/verify-email',
            json={
                'otp_session_id': challenge['otp_session_id'],
                'otp': _code(),
                'purpose': 'signup',
            },
        )
        if verified.status_code != 200:
            _fail('POST /auth/verify-email', verified)
        _ok('email verified', verified.json()['message'])

        # Sign-in is two steps: the password alone only earns an OTP challenge.
        step1 = await client.post('/api/v1/auth/login', json={'email': email, 'password': PASSWORD})
        if step1.status_code != 200:
            _fail('POST /auth/login', step1)
        login_sid = step1.json()['otp_session_id']
        _ok('password accepted', 'otp challenge issued')

        step2 = await client.post(
            '/api/v1/auth/verify-otp',
            json={'otp_session_id': login_sid, 'otp': _code(), 'purpose': 'login'},
        )
        if step2.status_code != 200:
            _fail('POST /auth/verify-otp', step2)
        tokens = step2.json()
        headers = {'Authorization': f'Bearer {tokens["access_token"]}'}
        _ok(
            'signed in',
            f'role={tokens["role"]} refresh_cookie={"poultech_refresh" in step2.cookies}'
            f' http_only={"HttpOnly" in step2.headers.get("set-cookie", "")}',
        )

        me = await client.get('/api/v1/me', headers=headers)
        if me.status_code != 200:
            _fail('GET /me', me)
        _ok(
            'me',
            f'email_verified={me.json()["is_email_verified"]} complete={me.json()["is_profile_complete"]}',
        )

        profile = await client.patch(
            '/api/v1/me/profile',
            headers=headers,
            json={'region': 'Vetapalem', 'district': 'Bapatla', 'village_city': 'Guntur Road'},
        )
        if profile.status_code != 200:
            _fail('PATCH /me/profile', profile)
        body = profile.json()
        _ok('profile', f'complete={body["is_profile_complete"]} {body["region"]}/{body["district"]}')

        sessions = await client.get('/api/v1/me/sessions', headers=headers)
        if sessions.status_code != 200:
            _fail('GET /me/sessions', sessions)
        _ok(
            'sessions',
            f'open={len(sessions.json()["items"])} current={sessions.json()["items"][0]["current"]}',
        )

        # One account, one device: a second sign-in must be refused until confirmed.
        other = httpx.AsyncClient(base_url=base_url, timeout=30.0)
        clash = await other.post('/api/v1/auth/login', json={'email': email, 'password': PASSWORD})
        details = clash.json().get('error', {}).get('details', {})
        if clash.status_code != 409 or 'signed_in_at' not in details:
            print(
                f'FAIL second device: expected 409 already_signed_in, got {clash.status_code}',
                file=sys.stderr,
            )
            print(clash.text[:300], file=sys.stderr)
            return 1
        _ok('second device refused', '409 before any email was sent')
        await other.aclose()

        refreshed = await client.post('/api/v1/auth/refresh', headers={'X-Poultech-Refresh': '1'})
        if refreshed.status_code != 200:
            _fail('POST /auth/refresh', refreshed)
        _ok('refresh', 'new access token issued, cookie rotated')

        if (await client.post('/api/v1/auth/refresh')).status_code != 403:
            print('FAIL refresh without the CSRF header should be 403', file=sys.stderr)
            return 1
        _ok('csrf guard', '403 without X-Poultech-Refresh')

        logout = await client.post('/api/v1/auth/logout', headers=headers)
        if logout.status_code != 204:
            _fail('POST /auth/logout', logout)
        if (await client.get('/api/v1/me', headers=headers)).status_code != 401:
            print('FAIL access token still works after logout', file=sys.stderr)
            return 1
        _ok('logout', 'session revoked, old access token now 401')

        anonymous = await client.get('/api/v1/me')
        if anonymous.status_code != 401:
            _fail('GET /me unauthenticated', anonymous)
        _ok('anonymous request rejected')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Smoke-test a running Poultech API.')
    parser.add_argument('--base-url', default=os.getenv('SMOKE_BASE_URL', 'http://127.0.0.1:8000'))
    parser.add_argument('--otp', default=os.getenv('OTP'), help='fixed code, for a single-challenge flow')
    parser.add_argument(
        '--otp-file', default=os.getenv('OTP_FILE'), help='console-backend log to read codes from'
    )
    args = parser.parse_args()
    return asyncio.run(run(args.base_url, args.otp, args.otp_file))


if __name__ == '__main__':
    raise SystemExit(main())
