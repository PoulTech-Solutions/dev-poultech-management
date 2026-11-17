#!/usr/bin/env python3
"""Live HTTP checks against :8000 using real NECC rows in Postgres (not pytest seeds)."""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import httpx

from app.core.redis_conn import close_redis, get_redis
from app.core.security import decrypt_otp_payload
from app.core.store import otp_key

TEST_PASSWORD = 'Str0ng!Pass1'
PROFILE_PATCH = {
    'village_city': 'Chennai',
    'state': 'Tamil Nadu',
    'district': 'Chennai',
    'pincode': '600001',
}


def bearer(tokens: dict) -> dict:
    return {'Authorization': f"Bearer {tokens['access_token']}"}


def new_email(prefix: str = 'market-live') -> str:
    return f'{prefix}.{uuid.uuid4().hex[:10]}@example.com'


def new_phone() -> str:
    return f'9{uuid.uuid4().int % 1_000_000_000:09d}'


async def _otp(purpose: str, sid: str) -> str:
    raw = await get_redis().hget(otp_key(purpose, sid), 'raw')
    if not raw:
        raise RuntimeError(f'no OTP in redis for {purpose}:{sid}')
    return decrypt_otp_payload(str(raw), session_id=sid, purpose=purpose)


async def login(client: httpx.AsyncClient, email: str) -> dict:
    step1 = await client.post('/api/v1/auth/login', json={'email': email, 'password': TEST_PASSWORD})
    step1.raise_for_status()
    sid = step1.json()['otp_session_id']
    otp = await _otp('login', sid)
    step2 = await client.post(
        '/api/v1/auth/verify-otp',
        json={'otp_session_id': sid, 'otp': otp, 'purpose': 'login'},
    )
    step2.raise_for_status()
    return step2.json()


def _print_case(title: str, method: str, path: str, *, params: dict | None, status: int, body: dict) -> None:
    print(f'\n=== {title} ===')
    print(f'{method} {path}')
    if params:
        print('query:', params)
    print('status:', status)
    print(json.dumps(body, indent=2)[:4000])


async def _main() -> int:
    get_redis()
    base = 'http://127.0.0.1:8000'
    email = new_email()
    async with httpx.AsyncClient(base_url=base, timeout=120.0) as client:
        signup = await client.post(
            '/api/v1/auth/signup',
            json={
                'email': email,
                'password': TEST_PASSWORD,
                'confirm_password': TEST_PASSWORD,
                'first_name': 'Live',
                'last_name': 'NECC',
                'phone': new_phone(),
            },
        )
        if signup.status_code != 202:
            print('signup failed', signup.status_code, signup.text)
            return 1
        sid = signup.json()['otp_session_id']
        otp = await _otp('signup', sid)
        verify = await client.post(
            '/api/v1/auth/verify-email',
            json={'otp_session_id': sid, 'otp': otp, 'purpose': 'signup'},
        )
        verify.raise_for_status()
        tokens = await login(client, email)
        hdr = bearer(tokens)
        await client.patch('/api/v1/me/profile', json=PROFILE_PATCH, headers=hdr)

        cases: list[tuple[str, str, str, dict | None]] = [
            ('zones', 'GET', '/api/v1/market/zones', {'include_today': 'true'}),
            ('dashboard-profile', 'GET', '/api/v1/market/dashboard', None),
            ('dashboard-ahmedabad', 'GET', '/api/v1/market/dashboard', {'zone': 'Ahmedabad'}),
            ('dashboard-chittoor', 'GET', '/api/v1/market/dashboard', {'zone': 'Chittoor'}),
            ('cities-today', 'GET', '/api/v1/market/eggs/cities-today', None),
            ('trend-days-pune', 'GET', '/api/v1/market/eggs/trend', {'zone': 'Pune', 'window': 'days'}),
            ('trend-month-oct', 'GET', '/api/v1/market/eggs/trend', {'zone': 'Pune', 'window': 'month', 'year': '2026', 'month': '10'}),
            ('trend-year', 'GET', '/api/v1/market/eggs/trend', {'zone': 'Mumbai (CC)', 'window': 'year'}),
            ('sheet-nov-pune', 'GET', '/api/v1/market/eggs/sheet', {'zone': 'Pune', 'year': '2026', 'month': '11'}),
            ('sheet-oct-ahm', 'GET', '/api/v1/market/eggs/sheet', {'zone': 'Ahmedabad', 'year': '2026', 'month': '10'}),
            (
                'compare-multi',
                'GET',
                '/api/v1/market/eggs/compare',
                {'zones': 'Pune,Mumbai (CC),Ahmedabad,Kolkata (WB),Chittoor'},
            ),
        ]
        summary: list[tuple[str, int, str]] = []
        for title, method, path, params in cases:
            r = await client.request(method, path, params=params, headers=hdr)
            body = r.json() if r.headers.get('content-type', '').startswith('application/json') else {'raw': r.text}
            _print_case(title, method, path, params=params, status=r.status_code, body=body)
            note = ''
            if title == 'cities-today' and r.status_code == 200:
                note = f"{len(body.get('zones', []))} cities"
            elif title == 'dashboard-ahmedabad' and r.status_code == 200:
                note = f"value={body['rates'][0]['value']} as_of={body['metadata'].get('price_as_of')}"
            summary.append((title, r.status_code, note))

        print('\n=== SUMMARY ===')
        for title, status, note in summary:
            print(f'{title}: {status} {note}')
    await close_redis()
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(_main()))
