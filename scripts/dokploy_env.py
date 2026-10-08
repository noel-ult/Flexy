"""Inspect Flexy's Dokploy environment; --apply fixes missing/template values.

Uses only Python's standard library. The API key is prompted locally, never
stored, and no environment values are printed. No deployment is triggered.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import secrets
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class HelperError(Exception):
    """A diagnostic containing only our own text, never server response values."""


def http_failure(status: int, headers, body: bytes) -> str:
    """Classify denials without echoing server bodies, credentials, or URLs."""
    if headers.get('cf-mitigated', '').lower() == 'challenge':
        return (f'HTTP {status}: Cloudflare challenged the API request. '
                'Check the matching event in Cloudflare Security Events; '
                'a Dokploy token cannot solve a browser challenge.')
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        payload = None
    code = payload.get('code') if isinstance(payload, dict) else None
    if code is None and isinstance(payload, dict):
        nested = payload.get('error')
        if isinstance(nested, dict):
            data = nested.get('data')
            code = data.get('code') if isinstance(data, dict) else nested.get('code')
    if code == 'FORBIDDEN':
        return (f'HTTP {status}: Dokploy reports FORBIDDEN. '
                'Check that the token belongs to an account/organization '
                'with access to this Flexy service and the requested operation.')
    if code == 'UNAUTHORIZED' or status == 401:
        return (f'HTTP {status}: API authentication was rejected. '
                'Use an active Dokploy API key, not a webhook token or Cloudflare key.')
    if code == 'NOT_FOUND' or status == 404:
        return f'HTTP {status}: The service or API route was not found; check the service ID and type.'
    if status in (301, 302, 303, 307, 308):
        return (f'HTTP {status}: The API redirected instead of returning data. '
                'Check whether an access gateway/login page protects this API. '
                'The key was not forwarded to the redirect destination.')
    if 'text/html' in headers.get('Content-Type', '').lower():
        return (f'HTTP {status}: An HTML error page was returned, rather than a Dokploy API response. '
                'Check proxy/Cloudflare Security Events and Dokploy logs; '
                'the response alone does not identify which layer denied access.')
    return (f'HTTP {status}: Access failed; the responsible layer is not confirmed. '
            'Check Dokploy/proxy logs. Response contents are omitted.')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not send the API key to a redirect destination.
        return None


def parse_env(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if '=' not in stripped:
            raise HelperError('Environment contains an unsupported line; inspect it in Dokploy.')
        key, value = stripped.split('=', 1)
        key = key.strip()
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            raise HelperError('Environment contains an unsupported key; inspect it in Dokploy.')
        if key in values:
            raise HelperError('Environment contains duplicate keys; inspect it in Dokploy.')
        values[key] = value
    return values


def expected_env() -> dict[str, str]:
    template = Path(__file__).resolve().parents[1] / 'infra/dokploy/.env.example'
    values = parse_env(template.read_text())
    origin = 'https://flexy.noelbiju.in'
    for key in ('APP_BASE_URL', 'FRONTEND_ORIGIN', 'NEXT_PUBLIC_API_BASE_URL'):
        values[key] = origin
    postgres, redis, minio = (secrets.token_hex(32) for _ in range(3))
    values['POSTGRES_PASSWORD'] = postgres
    values['DATABASE_URL'] = f'postgresql+psycopg://flexy:{postgres}@postgres:5432/flexy'
    values['REDIS_PASSWORD'] = redis
    values['REDIS_URL'] = f'redis://:{redis}@redis:6379/0'
    values['MINIO_ROOT_PASSWORD'] = minio
    values['S3_SECRET_KEY'] = minio
    return values


def is_template_value(value: str) -> bool:
    return not value or 'REPLACE_' in value or value.startswith('YOUR_')


def select_secret(*candidates: str) -> str:
    configured = {value for value in candidates if not is_template_value(value)}
    if len(configured) > 1:
        raise HelperError('Existing credentials disagree; correct the pair before applying. No secrets rotated.')
    return next(iter(configured)) if configured else secrets.token_hex(32)


def repair_env(existing: dict[str, str], defaults: dict[str, str]) -> dict[str, str]:
    """Keep configured credentials and unrelated settings; repair known pairs."""
    result = dict(existing)
    for key, value in defaults.items():
        if key not in result or not result[key]:
            result[key] = value
    for key in ('APP_BASE_URL', 'FRONTEND_ORIGIN', 'NEXT_PUBLIC_API_BASE_URL'):
        result[key] = defaults[key]
    for password_key, url_key in (
        ('POSTGRES_PASSWORD', 'DATABASE_URL'), ('REDIS_PASSWORD', 'REDIS_URL'),
    ):
        current_url = existing.get(url_key, '')
        try:
            url_password = urlsplit(current_url).password
        except ValueError:
            raise HelperError('DATABASE_URL or REDIS_URL is malformed; no changes made.') from None
        secret = select_secret(existing.get(password_key, ''), unquote(url_password or ''))
        result[password_key] = secret
        if is_template_value(current_url):
            # The pinned template supplies private service names and protocol.
            if url_key == 'DATABASE_URL':
                username = quote(result['POSTGRES_USER'], safe='')
                database = quote(result['POSTGRES_DB'], safe='')
                result[url_key] = (
                    f'postgresql+psycopg://{username}:{quote(secret, safe="")}@postgres:5432/{database}'
                )
            else:
                result[url_key] = f'redis://:{quote(secret, safe="")}@redis:6379/0'
    storage_secret = select_secret(
        existing.get('MINIO_ROOT_PASSWORD', ''), existing.get('S3_SECRET_KEY', ''),
    )
    result['MINIO_ROOT_PASSWORD'] = storage_secret
    result['S3_SECRET_KEY'] = storage_secret
    if not existing.get('S3_ACCESS_KEY'):
        result['S3_ACCESS_KEY'] = result['MINIO_ROOT_USER']
    return result


def render_env(original: str, values: dict[str, str]) -> str:
    """Preserve comments and unrelated entries while replacing selected values."""
    rendered = []
    seen = set()
    for line in original.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and '=' in stripped:
            key = stripped.split('=', 1)[0].strip()
            rendered.append(f'{key}={values[key]}')
            seen.add(key)
        else:
            rendered.append(line)
    rendered.extend(f'{key}={value}' for key, value in values.items() if key not in seen)
    return '\n'.join(rendered) + '\n'


def check(values: dict[str, str], required: dict[str, str]) -> bool:
    missing = sorted(set(required) - set(values))
    placeholders = sorted(key for key, value in values.items() if 'REPLACE_' in value)
    origins_ok = all(values.get(key) == 'https://flexy.noelbiju.in' for key in
                     ('APP_BASE_URL', 'FRONTEND_ORIGIN', 'NEXT_PUBLIC_API_BASE_URL'))
    passwords = [values.get(key, '') for key in
                 ('POSTGRES_PASSWORD', 'REDIS_PASSWORD', 'MINIO_ROOT_PASSWORD')]
    try:
        database_password = unquote(urlsplit(values.get('DATABASE_URL', '')).password or '')
        redis_password = unquote(urlsplit(values.get('REDIS_URL', '')).password or '')
    except ValueError:
        raise HelperError('DATABASE_URL or REDIS_URL is malformed; values omitted.') from None
    credentials_ok = bool(all(passwords)) and len(set(passwords)) == 3 and (
        database_password == passwords[0]
        and redis_password == passwords[1]
        and values.get('S3_SECRET_KEY') == passwords[2]
        and values.get('S3_ACCESS_KEY') == values.get('MINIO_ROOT_USER')
    )
    print(f'Variables present: {len(values)}')
    print('Missing variables: ' + (', '.join(missing) or 'none'))
    print('Placeholder variables: ' + (', '.join(placeholders) or 'none'))
    print(f'Public origins correct: {origins_ok}')
    print(f'Credential pairs match and services use distinct passwords: {credentials_ok}')
    executor_ok = values.get('BUILD_EXECUTOR') == 'unavailable'
    print(f'Build executor has the expected unavailable default: {executor_ok}')
    return not missing and not placeholders and origins_ok and credentials_ok and executor_ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Fix missing/template values; preserve real credentials')
    parser.add_argument('--diagnose', action='store_true', help='Print safe response metadata; no writes')
    args = parser.parse_args()
    if args.apply and args.diagnose:
        raise HelperError('Use --diagnose by itself; it is a read-only operation.')
    if not sys.stdin.isatty():
        raise HelperError('Run in your terminal so the API key can be entered privately.')
    api_key = getpass.getpass('Dokploy API key (hidden): ').strip()
    if not api_key:
        raise HelperError('An API key is required.')
    origin = 'https://dokploy.midhunpm.in'
    application_id = 'pmflYn_CR-roQBj6--XbA'
    opener = build_opener(NoRedirect())

    def request(route: str, body: dict | None = None) -> dict:
        url = origin + '/api/' + route
        data = None if body is None else json.dumps(body).encode()
        req = Request(url, data=data, headers={
            'x-api-key': api_key, 'Content-Type': 'application/json',
            'Accept': 'application/json', 'User-Agent': 'Flexy-Dokploy-Env/1.0',
        })
        operation = route.split('?', 1)[0]
        try:
            with opener.open(req, timeout=30) as response:
                response_body = response.read(2 * 1024 * 1024 + 1)
                if args.diagnose:
                    print(f'{operation}: HTTP {response.status}')
                    print('JSON content type: ' + str(
                        'application/json' in response.headers.get('Content-Type', '').lower()))
                if len(response_body) > 2 * 1024 * 1024:
                    raise HelperError('API response exceeded the inspection limit; body omitted.')
                if response.headers.get('cf-mitigated', '').lower() == 'challenge':
                    raise HelperError(http_failure(response.status, response.headers, response_body))
                if body is not None:
                    # Mutation endpoints may return null or an empty body.
                    # Success is proven by the following application.one read,
                    # not by the shape/content of this acknowledgement.
                    return {}
                try:
                    result = json.loads(response_body)
                except (ValueError, UnicodeError):
                    raise HelperError(
                        f'{operation}: Response was not valid JSON. '
                        'An HTML login/challenge page or proxy response may have been returned; '
                        'body omitted.') from None
        except HTTPError as error:
            if args.diagnose:
                print(f'{operation}: HTTP {error.code}')
                print('Cloudflare challenge header: ' + str(
                    error.headers.get('cf-mitigated', '').lower() == 'challenge'))
                ray = error.headers.get('cf-ray', '')
                if re.fullmatch(r'[0-9a-fA-F]{8,32}-[A-Za-z]{3}', ray):
                    print(f'Cloudflare Ray ID: {ray}')
            detail = http_failure(error.code, error.headers, error.read(16384))
            raise HelperError(f'{operation}: {detail}') from None
        except (URLError, OSError):
            message = f'{operation}: Connection failed or timed out.'
            if body is not None:
                message += ' Save outcome is unknown; inspect the saved environment before retrying.'
            raise HelperError(message) from None
        if not isinstance(result, dict):
            raise HelperError('Unexpected API response structure; no further action taken.')
        return result

    route = 'application.one?' + urlencode({'applicationId': application_id})
    current = request(route)
    if str(current.get('name', '')).lower() != 'flexy':
        raise HelperError('The service is not identified as Flexy; check the service ID. No changes made.')
    print('Service: Flexy (Application). This project requires a Compose service before deployment.')
    required = expected_env()
    existing_text = current.get('env') or ''
    if not isinstance(existing_text, str):
        raise HelperError('Unexpected environment format; no changes made.')
    existing = parse_env(existing_text)
    if not args.apply:
        print('Checking the environment currently saved in Dokploy:')
        return 0 if check(existing, required) else 1
    repaired = repair_env(existing, required)
    print('Validating proposed environment (not saved yet):')
    if not check(repaired, required):
        raise HelperError('Proposed environment failed validation; no changes saved.')
    changed = sorted(key for key, value in repaired.items() if existing.get(key) != value)
    if not changed:
        print('Environment already matches. No changes made.')
        return 0
    print('Updating variables: ' + ', '.join(changed))
    # Check for another writer before saving. Dokploy's API has no conditional
    # update/ETag contract, so do not edit concurrently while running this.
    latest = request(route)
    if any(latest.get(key) != current.get(key) for key in
           ('env', 'buildArgs', 'buildSecrets', 'createEnvFile')):
        raise HelperError('Settings changed during inspection; no changes made.')
    text = render_env(existing_text, repaired)
    request('application.saveEnvironment', {
        'applicationId': application_id, 'env': text,
        'buildArgs': current.get('buildArgs'),
        'buildSecrets': current.get('buildSecrets'),
        'createEnvFile': current.get('createEnvFile', True),
    })
    saved = request(route)
    if saved.get('env') != text:
        raise HelperError('Save could not be verified; inspect the Environment tab before retrying.')
    print('Environment saved and verified. No deployment triggered.')
    return 0 if check(parse_env(saved['env']), required) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except HelperError as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
    except (ValueError, OSError):
        print('Configuration check failed; no success is claimed. Inspect the service settings.', file=sys.stderr)
        sys.exit(1)
