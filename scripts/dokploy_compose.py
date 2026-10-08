"""Prepare Flexy's GitHub-backed Compose service using the saved environment.

Default is read-only. --apply creates/configures the service and HTTPS routes.
--apply --deploy additionally requests a deployment, not proof of a live site.
Secrets are prompted/transferred in memory only; the old Application is intact.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from dokploy_env import HelperError, NoRedirect, check, expected_env, http_failure, parse_env

ORIGIN = 'https://dokploy.midhunpm.in'
PROJECT_ID = 'VO20oI3DR48rWk_-PDD8P'
ENVIRONMENT_ID = 'A-zEXPT5dUGokRQxgn417'
APPLICATION_ID = 'pmflYn_CR-roQBj6--XbA'
NAME = 'Flexy Compose'
MARKER = 'Flexy Compose migration from Application ' + APPLICATION_ID
HOST = 'flexy.noelbiju.in'
CONFIG = {
    'sourceType': 'github',
    'owner': 'noel-ult',
    'repository': 'Flexy',
    'branch': 'main',
    'customGitUrl': None,
    'customGitBranch': None,
    'composePath': 'compose.dokploy.yaml',
    'composeType': 'docker-compose',
    'isolatedDeployment': True,
    'randomize': False,
    'autoDeploy': False,
    'command': '',
    'enableSubmodules': False,
}
GITHUB_SOURCE_KEYS = {
    'sourceType', 'owner', 'repository', 'branch', 'githubId',
    'customGitUrl', 'customGitBranch',
}


class Client:
    def __init__(self, key: str):
        self.key = key
        self.opener = build_opener(NoRedirect())

    def request(self, operation: str, body: dict | None = None, **query):
        # Callers supply fixed routes; secrets must never go into query strings.
        url = ORIGIN + '/api/' + operation
        if query:
            url += '?' + urlencode(query)
        req = Request(url, data=None if body is None else json.dumps(body).encode(), headers={
            'x-api-key': self.key, 'Content-Type': 'application/json',
            'Accept': 'application/json', 'User-Agent': 'Flexy-Dokploy-Compose/1.0',
        })
        try:
            with self.opener.open(req, timeout=30) as response:
                content = response.read(2 * 1024 * 1024 + 1)
                if len(content) > 2 * 1024 * 1024:
                    raise HelperError(operation + ': Response exceeded the inspection limit.')
                if response.headers.get('cf-mitigated', '').lower() == 'challenge':
                    raise HelperError(http_failure(response.status, response.headers, content))
                if not content and body is not None:
                    return None
                try:
                    result = json.loads(content)
                except (ValueError, UnicodeError):
                    raise HelperError(operation + ': Response was not JSON; contents omitted.') from None
                if isinstance(result, dict) and ('error' in result or 'code' in result):
                    raise HelperError(operation + ': API reported an error; contents omitted.')
                return result
        except HTTPError as error:
            try:
                detail = http_failure(error.code, error.headers, error.read(16384))
            finally:
                error.close()
            raise HelperError(operation + ': ' + detail) from None
        except (URLError, OSError):
            detail = operation + ': Connection failed or timed out.'
            if body is not None:
                detail += ' Write outcome is unknown. Inspect Dokploy before retrying; no automatic retry made.'
            raise HelperError(detail) from None


def object_response(value) -> dict:
    if not isinstance(value, dict):
        raise HelperError('Unexpected API object structure; no further action taken.')
    return value


def records(value) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise HelperError('Unexpected API list structure; no further action taken.')
    return value


def identifier(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise HelperError('Missing or invalid service ID; no further action taken.')
    return value


def inventory(client: Client) -> dict:
    environment = object_response(client.request('environment.one', environmentId=ENVIRONMENT_ID))
    if environment.get('environmentId') != ENVIRONMENT_ID or environment.get('projectId') != PROJECT_ID:
        raise HelperError('Environment is not the expected Flexy project. No changes made.')
    records(environment.get('compose'))
    records(environment.get('applications'))
    return environment


def find_target(environment: dict) -> dict | None:
    matches = [item for item in environment['compose'] if item.get('description') == MARKER]
    if len(matches) > 1:
        raise HelperError('Multiple migration services exist; inspect Dokploy before continuing.')
    if not matches and any(item.get('name') == NAME for item in environment['compose']):
        raise HelperError('An unrelated Flexy Compose service exists; it will not be overwritten.')
    return matches[0] if matches else None


def source(client: Client) -> dict:
    current = object_response(client.request('application.one', applicationId=APPLICATION_ID))
    if current.get('applicationId') != APPLICATION_ID or current.get('environmentId') != ENVIRONMENT_ID \
            or str(current.get('name', '')).lower() != 'flexy':
        raise HelperError('Source is not the expected Flexy Application. No changes made.')
    if not isinstance(current.get('env'), str) or not check(parse_env(current['env']), expected_env()):
        raise HelperError('Saved Application environment failed validation. Fix it before migrating.')
    return current


def target(client: Client, compose_id: str, server_id) -> dict:
    current = object_response(client.request('compose.one', composeId=compose_id))
    if current.get('composeId') != compose_id or current.get('environmentId') != ENVIRONMENT_ID \
            or current.get('description') != MARKER or current.get('serverId') != server_id:
        raise HelperError('Compose ownership/environment/server does not match. No further changes made.')
    if current.get('mounts'):
        raise HelperError('Compose has additional Dokploy mounts. Review isolation before continuing; '
                          'no mounts removed and no deployment requested.')
    return current


def github_config(client: Client, original: dict, current: dict | None,
                  requested_id: str | None = None) -> dict:
    """Use Dokploy's connected GitHub App; never fall back to Custom Git.

    Read only the provider listing/repository metadata, not private GitHub keys.
    Stop before creating a service if the App cannot access Flexy or main.
    """
    providers = records(client.request('github.githubProviders'))
    provider_ids = [identifier(item.get('githubId')) for item in providers]
    preferred = requested_id
    if not preferred and current and current.get('sourceType') == 'github':
        preferred = current.get('githubId')
    if not preferred and original.get('sourceType') == 'github':
        preferred = original.get('githubId')
    if preferred:
        preferred = identifier(preferred)
        if preferred not in provider_ids:
            raise HelperError('The selected GitHub provider is unavailable to this API key. '
                              'Check Dokploy Settings -> Git Providers and project access.')
    elif len(provider_ids) == 1:
        preferred = provider_ids[0]
    elif not provider_ids:
        raise HelperError('No configured GitHub provider is accessible. In Dokploy Settings -> '
                          'Git Providers, connect GitHub and grant the App access to noel-ult/Flexy. '
                          'No service created; Custom Git will not be used.')
    else:
        print('Accessible GitHub provider IDs: ' + ', '.join(provider_ids))
        raise HelperError('Multiple GitHub providers exist. Select one with --github-id; '
                          'the helper will not choose a different account automatically.')
    repositories = records(client.request('github.getGithubRepositories', githubId=preferred))
    if not any(str(item.get('full_name', '')).lower() == 'noel-ult/flexy' for item in repositories):
        raise HelperError('The GitHub App cannot access noel-ult/Flexy. Grant repository access '
                          'to the installation in GitHub, then retry. No settings changed.')
    branches = records(client.request('github.getGithubBranches', githubId=preferred,
                                      owner='noel-ult', repo='Flexy'))
    if not any(item.get('name') == 'main' for item in branches):
        raise HelperError('The GitHub provider could not verify branch main. No settings changed.')
    print('GitHub integration verified: noel-ult/Flexy, branch main. Custom Git is not used.')
    return {**CONFIG, 'githubId': preferred}


def can_switch_failed_source(current: dict, changed: dict, copy_env: bool) -> bool:
    """The earlier failed Custom Git setup may be repaired without deleting it."""
    if copy_env or current.get('sourceType') != 'git' or current.get('composeStatus') != 'error':
        return False
    if current.get('customGitUrl') != 'https://github.com/noel-ult/Flexy.git' \
            or current.get('customGitBranch') != 'main':
        return False
    history = records(current.get('deployments'))
    return bool(history) and all(item.get('status') in ('error', 'cancelled') for item in history) \
        and set(changed).issubset(GITHUB_SOURCE_KEYS)


def desired_routes(compose_id: str) -> list[dict]:
    return [{'host': HOST, 'path': path, 'port': port, 'serviceName': service,
             'https': True, 'certificateType': 'letsencrypt', 'stripPath': False,
             'composeId': compose_id, 'domainType': 'compose'}
            for path, port, service in [('/', 3000, 'web'), ('/v1', 8000, 'api')]]


def configure_routes(client: Client, compose_id: str, apply: bool) -> bool:
    # Fail closed on same-host routes in this environment. Other projects must
    # also be checked by the operator before accepting the deployment preview.
    environment = inventory(client)
    for collection, id_key, operation in [
        ('applications', 'applicationId', 'domain.byApplicationId'),
        ('compose', 'composeId', 'domain.byComposeId'),
    ]:
        for service in environment[collection]:
            service_id = identifier(service.get(id_key))
            if service_id == compose_id:
                continue
            domains = records(client.request(operation, **{id_key: service_id}))
            if any(item.get('host', '').lower() == HOST for item in domains):
                kind = 'application' if collection == 'applications' else 'compose'
                print('Conflicting domain owner: ' + ORIGIN + '/dashboard/project/' + PROJECT_ID
                      + '/environment/' + ENVIRONMENT_ID + '/services/' + kind + '/' + service_id
                      + '?tab=domains')
                raise HelperError('The public host is already assigned to another service in this environment. '
                                  'Compose/env are preserved. Resolve the domain conflict in Dokploy; '
                                  'no routes removed and no deployment requested.')
    for desired in desired_routes(compose_id):
        saved = records(client.request('domain.byComposeId', composeId=compose_id))
        matches = [item for item in saved if item.get('host') == HOST
                   and (item.get('path') or '/') == desired['path']]
        if len(matches) > 1:
            raise HelperError('Duplicate public routes exist; no routes changed.')
        if matches:
            if any(matches[0].get(key) != value for key, value in desired.items()):
                raise HelperError('An existing public route differs from the required configuration. '
                                  'Inspect Domains; it will not be overwritten.')
        elif not apply:
            print('Missing HTTPS route: ' + desired['path'])
            return False
        else:
            client.request('domain.create', desired)
            readback = records(client.request('domain.byComposeId', composeId=compose_id))
            if sum(all(item.get(key) == value for key, value in desired.items())
                   for item in readback) != 1:
                raise HelperError('Public route save could not be verified. No deployment requested.')
    print('HTTPS route configuration verified: / -> web:3000; /v1 -> api:8000 (no strip).')
    return True


def migrate(client: Client, apply: bool = False, deploy: bool = False,
            github_id: str | None = None) -> int:
    if deploy and not apply:
        raise HelperError('--deploy requires --apply.')
    original = source(client)
    environment = inventory(client)
    match = find_target(environment)
    existing_target = None if match is None else target(
        client, identifier(match.get('composeId')), original.get('serverId'))
    desired_config = github_config(client, original, existing_target, github_id)
    if match is None:
        if not apply:
            print('No migration Compose service exists. Run with --apply to create it and copy the saved env.')
            return 1
        # Inspect again immediately before creating to avoid ordinary rerun /
        # concurrent-edit duplicates. Dokploy has no transactional CAS API.
        match = find_target(inventory(client))
        if match is None:
            client.request('compose.create', {
                'name': NAME, 'description': MARKER, 'appName': 'flexy-compose-noelbiju',
                'environmentId': ENVIRONMENT_ID, 'serverId': original.get('serverId'),
                'composeType': 'docker-compose',
            })
            # Do not assume the create acknowledgement contains an object/ID.
            match = find_target(inventory(client))
            if match is None:
                raise HelperError('Compose creation could not be verified. Inspect Dokploy before retrying.')
    compose_id = identifier(match.get('composeId'))
    current = target(client, compose_id, original.get('serverId'))
    dashboard = (ORIGIN + '/dashboard/project/' + PROJECT_ID + '/environment/' + ENVIRONMENT_ID
                 + '/services/compose/' + compose_id)
    print('Compose service: ' + dashboard)
    changed = {key: value for key, value in desired_config.items() if current.get(key) != value}
    copy_env = current.get('env') != original['env']
    if copy_env and current.get('env'):
        raise HelperError('Compose already has a different environment. It will not be overwritten.')
    if changed or copy_env:
        if not apply:
            print('Compose configuration or environment is incomplete. Run with --apply.')
            return 1
        failed_source_switch = can_switch_failed_source(current, changed, copy_env)
        if (current.get('deployments') or current.get('composeStatus') not in ('idle', 'error')) \
                and not failed_source_switch:
            raise HelperError('Compose has deployment history or is active. It will not be reconfigured automatically.')
        if failed_source_switch:
            print('Switching only the source of the failed Custom Git deployment to GitHub; '
                  'env, routes, history, service ID, and volumes are preserved.')
        latest = object_response(client.request('application.one', applicationId=APPLICATION_ID))
        if any(latest.get(key) != original.get(key) for key in
               ('env', 'serverId', 'environmentId', 'sourceType', 'githubId')):
            raise HelperError('Source changed during inspection. No environment copied.')
        if target(client, compose_id, original.get('serverId')) != current:
            raise HelperError('Compose changed during inspection. No settings overwritten.')
        if changed:
            client.request('compose.update', {'composeId': compose_id, **changed})
            current = target(client, compose_id, original.get('serverId'))
            if any(current.get(key) != value for key, value in desired_config.items()):
                raise HelperError('Compose configuration read-back failed. No deployment requested.')
        if copy_env:
            client.request('compose.saveEnvironment', {'composeId': compose_id, 'env': original['env']})
    current = target(client, compose_id, original.get('serverId'))
    if current.get('env') != original['env'] or any(
            current.get(key) != value for key, value in desired_config.items()):
        raise HelperError('Compose/env read-back failed. No deployment requested.')
    print('Compose configuration and exact environment copy verified. Original Application unchanged.')
    if not configure_routes(client, compose_id, apply):
        return 1
    if deploy:
        acknowledgement = client.request('compose.deploy', {'composeId': compose_id, 'title': 'Flexy Compose setup'})
        if acknowledgement is not True and not (isinstance(acknowledgement, dict)
                                               and acknowledgement.get('success') is True):
            raise HelperError('Deployment request acknowledgement is inconclusive. Inspect Deployments before retrying.')
        print('Deployment request accepted; build completion, TLS, and live health are NOT verified.')
        print('Deployment logs: ' + dashboard + '?tab=deployments')
    else:
        print('No deployment requested. Review Preview Compose, then click Deploy in Dokploy.')
    print('BUILD_EXECUTOR stays unavailable. Conversion builds remain disabled pending sandbox validation.')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Create/configure Compose, copy env, add missing HTTPS routes')
    parser.add_argument('--deploy', action='store_true', help='Additionally request deployment (requires --apply)')
    parser.add_argument('--github-id', help='Dokploy GitHub provider ID (not an API key); normally detected automatically')
    args = parser.parse_args()
    if args.deploy and not args.apply:
        raise HelperError('--deploy requires --apply.')
    if not sys.stdin.isatty():
        raise HelperError('Run in your terminal so the API key can be entered privately.')
    key = getpass.getpass('Dokploy API key (hidden): ').strip()
    if not key:
        raise HelperError('An API key is required.')
    return migrate(Client(key), args.apply, args.deploy, args.github_id)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except HelperError as error:
        print('Error: ' + str(error), file=sys.stderr)
        sys.exit(1)
    except (ValueError, OSError, TypeError):
        print('Configuration could not be verified; response details omitted. Inspect Dokploy.', file=sys.stderr)
        sys.exit(1)
