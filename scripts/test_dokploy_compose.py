"""Migration state-machine and HTTP safety checks; no live Dokploy writes."""

import contextlib
import copy
import io
import json
import re
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import dokploy_compose as dc
import dokploy_env


class FakeDokploy:
    """Use the v0.29.13 response inventory shapes, including null write acks."""

    def __init__(self):
        self.env = '\n'.join(f'{key}={value}' for key, value in dokploy_env.expected_env().items())
        self.app = {'applicationId': dc.APPLICATION_ID, 'environmentId': dc.ENVIRONMENT_ID,
                    'name': 'Flexy', 'env': self.env, 'serverId': None,
                    'sourceType': 'github', 'githubId': 'connected-github-id'}
        self.providers = [{'githubId': 'connected-github-id'}]
        self.repositories = [{'full_name': 'noel-ult/Flexy'}]
        self.branches = [{'name': 'main'}]
        self.compose = None
        self.calls = []
        self.app_domains = []
        self.domains = []
        self.ignore_save = False
        self.race = False
        self.duplicate = False
        self.save_timeout = False
        self.ignore_update = False

    def request(self, operation, body=None, **query):
        self.calls.append((operation, copy.deepcopy(body), query))
        if operation == 'application.one':
            value = copy.deepcopy(self.app)
            if self.race and sum(call[0] == operation for call in self.calls) > 1:
                value['env'] += '\nCHANGED=true'
            return value
        if operation == 'github.githubProviders':
            return copy.deepcopy(self.providers)
        if operation == 'github.getGithubRepositories':
            return copy.deepcopy(self.repositories)
        if operation == 'github.getGithubBranches':
            return copy.deepcopy(self.branches)
        if operation == 'environment.one':
            rows = [] if self.compose is None else [self.compose]
            if self.duplicate:
                rows = rows * 2
            return copy.deepcopy({'environmentId': dc.ENVIRONMENT_ID, 'projectId': dc.PROJECT_ID,
                                  'applications': [self.app], 'compose': rows})
        if operation == 'compose.create':
            if self.compose is not None:
                raise AssertionError('duplicate create')
            self.compose = {**body, 'composeId': 'created-compose-id', 'env': None,
                            'composeStatus': 'idle', 'deployments': []}
            return None
        if operation == 'compose.one':
            return copy.deepcopy(self.compose)
        if operation == 'compose.update':
            if not self.ignore_update:
                self.compose.update(body)
            return None
        if operation == 'compose.saveEnvironment':
            if self.save_timeout:
                raise dc.HelperError('compose.saveEnvironment: Write outcome is unknown.')
            if not self.ignore_save:
                self.compose['env'] = body['env']
            return None
        if operation == 'domain.byApplicationId':
            return copy.deepcopy(self.app_domains)
        if operation == 'domain.byComposeId':
            return copy.deepcopy(self.domains)
        if operation == 'domain.create':
            self.domains.append({**body, 'domainId': 'domain-' + str(len(self.domains))})
            return None
        if operation == 'compose.deploy':
            return {'success': True}
        raise AssertionError(operation)

    def writes(self):
        return [operation for operation, body, _ in self.calls if body is not None]


class MigrationTests(unittest.TestCase):
    def run_migration(self, api, **args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = dc.migrate(api, **args)
        return result, output.getvalue()

    def test_default_only_reads(self):
        api = FakeDokploy()
        result, output = self.run_migration(api)
        self.assertEqual(result, 1)
        self.assertEqual(api.writes(), [])
        self.assertIn('Run with --apply', output)

    def test_apply_copies_exact_secrets_and_preserves_source(self):
        api = FakeDokploy()
        original = copy.deepcopy(api.app)
        result, output = self.run_migration(api, apply=True)
        self.assertEqual(result, 0)
        self.assertEqual(api.app, original)
        self.assertEqual(api.compose['env'], api.env)
        self.assertTrue(all(api.compose.get(key) == value for key, value in dc.CONFIG.items()))
        self.assertEqual(api.compose['githubId'], 'connected-github-id')
        self.assertEqual(api.compose['sourceType'], 'github')
        self.assertIsNone(api.compose.get('customGitUrl'))
        self.assertEqual(len(api.domains), 2)
        self.assertTrue(all(domain['stripPath'] is False for domain in api.domains))
        self.assertNotIn('compose.deploy', api.writes())
        for key in ('POSTGRES_PASSWORD', 'REDIS_PASSWORD', 'MINIO_ROOT_PASSWORD'):
            self.assertNotIn(dokploy_env.parse_env(api.env)[key], output)

    def test_rerun_and_read_only_check_reuse_service_without_writes(self):
        api = FakeDokploy()
        self.run_migration(api, apply=True)
        api.calls = []
        self.assertEqual(self.run_migration(api, apply=True)[0], 0)
        self.assertEqual(api.writes(), [])
        api.calls = []
        self.assertEqual(self.run_migration(api)[0], 0)
        self.assertEqual(api.writes(), [])

    def test_partial_configuration_resumes_without_duplicate_service(self):
        api = FakeDokploy()
        api.save_timeout = True
        with self.assertRaises(dc.HelperError):
            self.run_migration(api, apply=True)
        api.save_timeout = False
        api.calls = []
        self.assertEqual(self.run_migration(api, apply=True)[0], 0)
        self.assertNotIn('compose.create', api.writes())

    def test_invalid_source_env_stops_before_writes(self):
        api = FakeDokploy()
        api.app['env'] = 'POSTGRES_PASSWORD=REPLACE_ME'
        with self.assertRaises(dc.HelperError):
            self.run_migration(api, apply=True, deploy=True)
        self.assertEqual(api.writes(), [])

    def test_wrong_project_stops_before_writes(self):
        api = FakeDokploy()
        original_request = api.request

        def wrong_project(operation, body=None, **query):
            value = original_request(operation, body, **query)
            if operation == 'environment.one':
                value['projectId'] = 'another-project'
            return value

        api.request = wrong_project
        with self.assertRaises(dc.HelperError):
            self.run_migration(api, apply=True)
        self.assertEqual(api.writes(), [])

    def test_different_compose_env_is_not_overwritten(self):
        api = FakeDokploy()
        self.run_migration(api, apply=True)
        api.compose['env'] = 'CUSTOM=value'
        api.calls = []
        with self.assertRaises(dc.HelperError):
            self.run_migration(api, apply=True)
        self.assertEqual(api.writes(), [])

    def test_unverified_save_prevents_domains_and_deploy(self):
        api = FakeDokploy()
        api.ignore_save = True
        with self.assertRaises(dc.HelperError):
            self.run_migration(api, apply=True, deploy=True)
        self.assertNotIn('compose.deploy', api.writes())
        self.assertNotIn('domain.create', api.writes())

    def test_source_race_does_not_copy_environment(self):
        api = FakeDokploy()
        api.race = True
        with self.assertRaises(dc.HelperError):
            self.run_migration(api, apply=True)
        self.assertNotIn('compose.saveEnvironment', api.writes())

    def test_duplicate_migration_services_refused(self):
        api = FakeDokploy()
        self.run_migration(api, apply=True)
        api.duplicate = True
        api.calls = []
        with self.assertRaises(dc.HelperError):
            self.run_migration(api, apply=True)
        self.assertEqual(api.writes(), [])

    def test_old_application_domain_conflict_does_not_delete_or_deploy(self):
        api = FakeDokploy()
        api.app_domains = [{'host': dc.HOST, 'path': '/'}]
        with self.assertRaises(dc.HelperError):
            self.run_migration(api, apply=True, deploy=True)
        self.assertEqual(api.compose['env'], api.env)
        self.assertEqual(api.app_domains, [{'host': dc.HOST, 'path': '/'}])
        self.assertNotIn('domain.create', api.writes())
        self.assertNotIn('compose.deploy', api.writes())

    def test_wrong_existing_route_not_overwritten(self):
        api = FakeDokploy()
        self.run_migration(api, apply=True)
        api.domains[1]['stripPath'] = True
        api.calls = []
        with self.assertRaises(dc.HelperError):
            self.run_migration(api, apply=True, deploy=True)
        self.assertEqual(api.writes(), [])

    def test_additional_dokploy_mounts_block_deployment(self):
        api = FakeDokploy()
        self.run_migration(api, apply=True)
        api.compose['mounts'] = [{'type': 'bind', 'hostPath': '/'}]
        api.calls = []
        with self.assertRaises(dc.HelperError):
            self.run_migration(api, apply=True, deploy=True)
        self.assertEqual(api.writes(), [])

    def test_deploy_requires_apply_and_only_claims_request_accepted(self):
        api = FakeDokploy()
        with self.assertRaises(dc.HelperError):
            self.run_migration(api, deploy=True)
        self.assertEqual(api.calls, [])
        result, output = self.run_migration(api, apply=True, deploy=True)
        self.assertEqual(result, 0)
        self.assertEqual(api.writes()[-1], 'compose.deploy')
        self.assertIn('live health are NOT verified', output)

    def failed_git_setup(self):
        api = FakeDokploy()
        self.run_migration(api, apply=True)
        api.compose.update({'sourceType': 'git',
                            'customGitUrl': 'https://github.com/noel-ult/Flexy.git',
                            'customGitBranch': 'main', 'githubId': None,
                            'owner': None, 'repository': None, 'branch': None,
                            'composeStatus': 'error',
                            'deployments': [{'status': 'error', 'deploymentId': 'failed-id'}]})
        api.calls = []
        return api

    def test_failed_custom_git_switch_preserves_id_env_domains_history(self):
        api = self.failed_git_setup()
        preserved = {key: copy.deepcopy(api.compose[key])
                     for key in ('composeId', 'env', 'appName', 'deployments')}
        domains = copy.deepcopy(api.domains)
        self.assertEqual(self.run_migration(api, apply=True)[0], 0)
        self.assertEqual(api.writes(), ['compose.update'])
        update = next(body for op, body, _ in api.calls if op == 'compose.update')
        self.assertTrue((set(update) - {'composeId'}).issubset(dc.GITHUB_SOURCE_KEYS))
        for key, value in preserved.items():
            self.assertEqual(api.compose[key], value)
        self.assertEqual(api.domains, domains)
        self.assertEqual(api.compose['sourceType'], 'github')

    def test_failed_custom_git_switch_diagnostic_has_no_writes(self):
        api = self.failed_git_setup()
        self.assertEqual(self.run_migration(api)[0], 1)
        self.assertEqual(api.writes(), [])

    def test_switch_rejects_running_or_successful_history_and_unrelated_changes(self):
        for change in ({'composeStatus': 'running'},
                       {'deployments': [{'status': 'done'}]},
                       {'deployments': [{'status': 'running'}]},
                       {'customGitUrl': 'https://github.com/someone/another.git'},
                       {'isolatedDeployment': False},
                       {'env': 'DIFFERENT=value'}):
            with self.subTest(change=change):
                api = self.failed_git_setup()
                api.compose.update(change)
                with self.assertRaises(dc.HelperError):
                    self.run_migration(api, apply=True, deploy=True)
                self.assertEqual(api.writes(), [])

    def test_no_provider_or_repository_or_branch_access_stops_before_create(self):
        for attribute in ('providers', 'repositories', 'branches'):
            with self.subTest(attribute=attribute):
                api = FakeDokploy()
                setattr(api, attribute, [])
                with self.assertRaises(dc.HelperError):
                    self.run_migration(api, apply=True)
                self.assertEqual(api.writes(), [])
                self.assertIsNone(api.compose)

    def test_single_provider_is_used_when_application_has_no_github_id(self):
        api = FakeDokploy()
        api.app.update({'sourceType': 'git', 'githubId': None})
        self.assertEqual(self.run_migration(api, apply=True)[0], 0)
        self.assertEqual(api.compose['githubId'], 'connected-github-id')

    def test_multiple_providers_require_selection_and_never_custom_git_fallback(self):
        api = FakeDokploy()
        api.app.update({'sourceType': 'git', 'githubId': None})
        api.providers.append({'githubId': 'other-id'})
        with self.assertRaises(dc.HelperError):
            self.run_migration(api, apply=True)
        self.assertEqual(api.writes(), [])
        self.assertEqual(self.run_migration(api, apply=True, github_id='other-id')[0], 0)
        self.assertEqual(api.compose['githubId'], 'other-id')

    def test_unverified_source_update_prevents_deployment(self):
        api = self.failed_git_setup()
        api.ignore_update = True
        with self.assertRaises(dc.HelperError):
            self.run_migration(api, apply=True, deploy=True)
        self.assertEqual(api.writes(), ['compose.update'])


class HttpSafetyTests(unittest.TestCase):
    def test_redirect_denied_and_error_body_not_printed(self):
        headers = Message()
        headers['Location'] = 'https://another-host.invalid/private'
        opener = MagicMock()
        opener.open.side_effect = HTTPError('ignored', 302, 'ignored', headers, io.BytesIO(b'private-secret'))
        with patch('dokploy_compose.build_opener', return_value=opener):
            api = dc.Client('private-api-key')
            with self.assertRaises(dc.HelperError) as caught:
                api.request('application.one', applicationId=dc.APPLICATION_ID)
        self.assertNotIn('private-secret', str(caught.exception))
        self.assertNotIn('private-api-key', str(caught.exception))
        self.assertIn('not forwarded', str(caught.exception))
        self.assertIsInstance(dc.NoRedirect(), dc.NoRedirect)

    def test_timeout_does_not_retry_mutation(self):
        opener = MagicMock()
        opener.open.side_effect = URLError('private-secret')
        with patch('dokploy_compose.build_opener', return_value=opener):
            api = dc.Client('private-api-key')
            with self.assertRaises(dc.HelperError) as caught:
                api.request('compose.saveEnvironment', {'env': 'SECRET=private-secret'})
        self.assertEqual(opener.open.call_count, 1)
        self.assertIn('outcome is unknown', str(caught.exception))
        self.assertNotIn('private-secret', str(caught.exception))

    def test_null_and_empty_acknowledgements_supported(self):
        for body in (b'null', b'', b'true'):
            response = MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = body
            response.headers = Message()
            opener = MagicMock()
            opener.open.return_value = response
            with patch('dokploy_compose.build_opener', return_value=opener):
                result = dc.Client('private-key').request('compose.update', {'composeId': 'id'})
            self.assertIn(result, (None, True))


class DeploymentProfileTests(unittest.TestCase):
    def test_root_dockerfile_matches_the_explicit_web_build_entrypoint(self):
        root = Path(__file__).resolve().parents[1]
        content = (root / 'Dockerfile').read_text()
        self.assertEqual(content, (root / 'infra/docker/web.Dockerfile').read_text())
        self.assertIn('COPY web ./', content)
        self.assertIn('ARG NEXT_PUBLIC_API_BASE_URL=', content)
        self.assertNotIn('http://localhost:8000', content)
        self.assertIn('USER 10001:10001', content)
        self.assertIn('EXPOSE 3000', content)
        self.assertNotIn('COPY backend', content)

    def test_profiles_provide_a_runtime_private_api_and_bounded_workers(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('compose.yaml', 'compose.dokploy.yaml'):
            with self.subTest(profile=name):
                content = (root / name).read_text()
                self.assertIn('FLEXY_API_UPSTREAM: http://api:8000', content)
                self.assertIn('"--processes", "1", "--threads", "2"', content)
        content = (root / 'compose.dokploy.yaml').read_text()
        self.assertIn('NEXT_PUBLIC_API_BASE_URL: ${NEXT_PUBLIC_API_BASE_URL:-}', content)
        web = content.split('  web:', 1)[1].split('  migrate:', 1)[0]
        self.assertIn('      - private', web)

    def test_bucket_init_passes_the_whole_script_as_one_shell_argument(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('compose.yaml', 'compose.dokploy.yaml'):
            with self.subTest(profile=name):
                init = re.split(r'^  minio-init:', (root / name).read_text(), flags=re.M)[1]
                self.assertIn('entrypoint: ["/bin/sh", "-ec"]', init)
                self.assertIn('    command:\n      - |\n', init)
                self.assertNotIn('    command: >-', init)
                self.assertIn('        mc mb --ignore-existing', init)
                self.assertIn('        mc anonymous set none', init)

    def test_profiles_build_object_storage_from_pinned_source_not_removed_images(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('compose.yaml', 'compose.dokploy.yaml'):
            with self.subTest(profile=name):
                content = (root / name).read_text()
                images = re.findall(r'^\s+image:\s+(\S+)\s*$', content, re.M)
                self.assertIn('dockerfile: infra/docker/minio.Dockerfile', content)
                self.assertIn('dockerfile: infra/docker/minio-client.Dockerfile', content)
                self.assertFalse(any('minio/' in image for image in images))
                self.assertFalse(any(image.endswith(':latest') for image in images))
        for filename, commit in (
            ('minio.Dockerfile', '0d7408fc9969caf07de6a8c3a84f9fbb10a6739e'),
            ('minio-client.Dockerfile', 'b00526b153a31b36767991a4f5ce2cced435ee8e'),
        ):
            content = (root / 'infra/docker' / filename).read_text()
            self.assertIn(commit, content)
            self.assertIn('USER 10001:10001', content)
            self.assertIn('/src/LICENSE', content)
            self.assertIn('/usr/share/', content)


if __name__ == '__main__':
    unittest.main()
