.PHONY: dev down logs config check api-check web-check k8s-render

dev:
	docker compose up --build

down:
	docker compose down --remove-orphans

logs:
	docker compose logs --follow --tail=150

config:
	docker compose config

check: config api-check web-check k8s-render

api-check:
	docker compose run --build --rm --no-deps api-test

web-check:
	docker compose run --build --rm --no-deps web-test

k8s-render:
	kustomize build infra/k8s/overlays/production >/dev/null
