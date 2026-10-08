"""Safe diagnostics and read-only behavior for the Dokploy helper."""

import contextlib
import io
import json
import unittest
from email.message import Message
from unittest.mock import MagicMock, patch

import dokploy_env


class DokployDiagnosticTests(unittest.TestCase):
    def headers(self, **values):
        headers = Message()
        for key, value in values.items():
            headers[key.replace('_', '-')] = value
        return headers

    def test_cloudflare_challenge_has_positive_evidence(self):
        result = dokploy_env.http_failure(403, self.headers(cf_mitigated='challenge'), b'private body')
        self.assertIn('Cloudflare challenged', result)
        self.assertNotIn('private body', result)

    def test_api_error_never_echoes_message_or_secrets(self):
        body = json.dumps({'code': 'FORBIDDEN', 'message': 'password=private-secret'}).encode()
        result = dokploy_env.http_failure(403, self.headers(Content_Type='application/json'), body)
        self.assertIn('Dokploy reports FORBIDDEN', result)
        self.assertNotIn('private-secret', result)

    def test_cloudflare_proxy_header_alone_is_not_block_attribution(self):
        result = dokploy_env.http_failure(403, self.headers(Server='cloudflare'), b'private body')
        self.assertIn('responsible layer is not confirmed', result)
        self.assertNotIn('Cloudflare challenged', result)

    def test_default_only_reads_and_does_not_print_values(self):
        values = dokploy_env.expected_env()
        response = MagicMock()
        response.read.return_value = json.dumps({
            'name': 'Flexy', 'env': '\n'.join(f'{k}={v}' for k, v in values.items()),
        }).encode()
        response.status = 200
        response.headers = self.headers(Content_Type='application/json')
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response
        output = io.StringIO()
        with patch('sys.argv', ['dokploy_env.py', '--diagnose']), \
                patch('sys.stdin.isatty', return_value=True), \
                patch('getpass.getpass', return_value='private-api-key'), \
                patch('dokploy_env.build_opener', return_value=opener), \
                contextlib.redirect_stdout(output):
            self.assertEqual(dokploy_env.main(), 0)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(opener.open.call_count, 1)
        self.assertNotIn('private-api-key', output.getvalue())
        for key in ('POSTGRES_PASSWORD', 'REDIS_PASSWORD', 'MINIO_ROOT_PASSWORD'):
            self.assertNotIn(values[key], output.getvalue())

    def test_template_is_repaired_with_unique_matching_secrets(self):
        template = dokploy_env.parse_env((
            dokploy_env.Path(__file__).resolve().parents[1] / 'infra/dokploy/.env.example'
        ).read_text())
        repaired = dokploy_env.repair_env(template, dokploy_env.expected_env())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(dokploy_env.check(repaired, dokploy_env.expected_env()))
        self.assertEqual(len(repaired), 25)

    def test_existing_real_secrets_and_unrelated_values_are_preserved(self):
        configured = dokploy_env.expected_env()
        configured['UNRELATED_VALUE'] = 'keep-this-value'
        configured['MAX_UPLOAD_BYTES'] = '10485760'
        configured['APP_BASE_URL'] = 'https://flexy.example.com'
        repaired = dokploy_env.repair_env(configured, dokploy_env.expected_env())
        for key in ('POSTGRES_PASSWORD', 'DATABASE_URL', 'REDIS_PASSWORD', 'REDIS_URL',
                    'MINIO_ROOT_PASSWORD', 'S3_SECRET_KEY', 'UNRELATED_VALUE', 'MAX_UPLOAD_BYTES'):
            self.assertEqual(repaired[key], configured[key])
        self.assertEqual(repaired['APP_BASE_URL'], 'https://flexy.noelbiju.in')
        original = '# Preserve this comment\nUNRELATED_VALUE=keep-this-value\n'
        text = dokploy_env.render_env(original, repaired)
        self.assertIn('# Preserve this comment', text)
        self.assertEqual(dokploy_env.parse_env(text), repaired)

    def test_partially_configured_pair_reuses_real_credential(self):
        configured = dokploy_env.expected_env()
        actual = configured['POSTGRES_PASSWORD']
        configured['POSTGRES_PASSWORD'] = 'REPLACE_WITH_A_UNIQUE_URL_SAFE_SECRET'
        repaired = dokploy_env.repair_env(configured, dokploy_env.expected_env())
        self.assertEqual(repaired['POSTGRES_PASSWORD'], actual)
        self.assertEqual(repaired['DATABASE_URL'], configured['DATABASE_URL'])

    def test_conflicting_configured_credentials_are_not_rotated(self):
        configured = dokploy_env.expected_env()
        configured['POSTGRES_PASSWORD'] = 'different-existing-password'
        with self.assertRaises(dokploy_env.HelperError):
            dokploy_env.repair_env(configured, dokploy_env.expected_env())

    def test_save_accepts_null_or_empty_ack_only_after_readback(self):
        for acknowledgement in (b'null', b''):
            with self.subTest(acknowledgement=acknowledgement):
                state = {'name': 'Flexy', 'env': '', 'buildArgs': 'KEEP=yes',
                         'buildSecrets': 'PRIVATE=retained', 'createEnvFile': True}
                methods = []
                posted = []

                def open_request(request, timeout):
                    methods.append(request.get_method())
                    if request.get_method() == 'POST':
                        payload = json.loads(request.data)
                        posted.append(payload)
                        state.update({k: v for k, v in payload.items() if k != 'applicationId'})
                        content = acknowledgement
                    else:
                        content = json.dumps(state).encode()
                    response = MagicMock()
                    response.read.return_value = content
                    response.headers = self.headers(Content_Type='application/json')
                    response.__enter__.return_value = response
                    return response

                opener = MagicMock()
                opener.open.side_effect = open_request
                output = io.StringIO()
                with patch('sys.argv', ['dokploy_env.py', '--apply']), \
                        patch('sys.stdin.isatty', return_value=True), \
                        patch('getpass.getpass', return_value='private-api-key'), \
                        patch('dokploy_env.build_opener', return_value=opener), \
                        contextlib.redirect_stdout(output):
                    self.assertEqual(dokploy_env.main(), 0)
                self.assertEqual(methods, ['GET', 'GET', 'POST', 'GET'])
                self.assertEqual(posted[0]['buildArgs'], 'KEEP=yes')
                self.assertEqual(posted[0]['buildSecrets'], 'PRIVATE=retained')
                self.assertIn('Environment saved and verified', output.getvalue())
                saved = dokploy_env.parse_env(state['env'])
                for key in ('POSTGRES_PASSWORD', 'REDIS_PASSWORD', 'MINIO_ROOT_PASSWORD'):
                    self.assertNotIn(saved[key], output.getvalue())


if __name__ == '__main__':
    unittest.main()
