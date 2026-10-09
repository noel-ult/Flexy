"""Guard native inspection deployment independently from Arch conversion."""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class ProductionImageTests(unittest.TestCase):
    def test_production_control_plane_uses_portable_image(self):
        compose = (ROOT / 'compose.dokploy.yaml').read_text()
        self.assertEqual(compose.count('dockerfile: infra/docker/backend-service.Dockerfile'), 3)
        self.assertNotIn('dockerfile: infra/docker/backend.Dockerfile', compose)
        self.assertIn('BUILD_EXECUTOR: ${BUILD_EXECUTOR:-unavailable}', compose)

    def test_portable_image_has_no_arch_conversion_stage_or_toolchain(self):
        dockerfile = (ROOT / 'infra/docker/backend-service.Dockerfile').read_text()
        self.assertIn('FROM python:3.12-slim-bookworm AS runtime', dockerfile)
        self.assertIn('BUILD_EXECUTOR=unavailable', dockerfile)
        self.assertIn('USER 10001:10001', dockerfile)
        self.assertNotRegex(dockerfile, r'(?m)^FROM .*archlinux')
        self.assertNotRegex(dockerfile, r'(?m)^RUN .*\b(pacman|makepkg)\b')

    def test_arch_conversion_image_remains_available_for_local_profile(self):
        self.assertIn('FROM archlinux:base-devel', (ROOT / 'infra/docker/backend.Dockerfile').read_text())
        self.assertIn('dockerfile: infra/docker/backend.Dockerfile', (ROOT / 'compose.yaml').read_text())

    def test_production_has_no_emulation_or_isolation_bypass(self):
        compose = (ROOT / 'compose.dokploy.yaml').read_text()
        self.assertNotIn('platform:', compose)
        self.assertNotIn('docker.sock', compose)
        self.assertNotIn('unconfined', compose)
        self.assertIsNone(re.search(r'^\s*privileged:\s*true', compose, re.M))
        self.assertIn('internal: true', compose)

    def test_only_routed_services_select_the_isolated_routing_network(self):
        compose = (ROOT / 'compose.dokploy.yaml').read_text()
        services = dict(re.findall(
            r'^  (\w+):\n(.*?)(?=^  \w+:\n|^networks:|\Z)', compose,
            re.M | re.S,
        ))
        label = 'traefik.docker.network: ${COMPOSE_PROJECT_NAME}'
        for name in ('web', 'api'):
            self.assertIn(label, services[name])
        for name in ('migrate', 'worker', 'postgres', 'redis', 'minio'):
            self.assertNotIn('traefik.docker.network', services[name])
        self.assertEqual(compose.count(label), 2)


if __name__ == '__main__':
    unittest.main()
