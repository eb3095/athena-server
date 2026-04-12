.PHONY: build push run stop logs shell test test-speaker health fmt lint install uninstall

IMAGE := ebennerv/athena-server
TAG := latest

AUTH_TOKEN ?= test-token
OPENAI_API_KEY ?=
OPENAI_MODEL ?= gpt-4o
OPENAI_TEMPERATURE ?= 0.7
OPENAI_MAX_TOKENS ?= 4096
ATHENA_TTS_URL ?= http://host.docker.internal:5002
ATHENA_TTS_TOKEN ?= test-token
DEFAULT_VOICE ?=

build:
	docker buildx build --platform linux/amd64,linux/arm64 -t $(IMAGE):$(TAG) --push .

build-local:
	docker buildx build --platform linux/amd64 -t $(IMAGE):$(TAG) --load .

run:
	docker run -d \
		--name athena-server \
		-p 5003:5003 \
		-e AUTH_TOKEN=$(AUTH_TOKEN) \
		-e OPENAI_API_KEY=$(OPENAI_API_KEY) \
		-e OPENAI_MODEL=$(OPENAI_MODEL) \
		-e OPENAI_TEMPERATURE=$(OPENAI_TEMPERATURE) \
		-e OPENAI_MAX_TOKENS=$(OPENAI_MAX_TOKENS) \
		-e ATHENA_TTS_URL=$(ATHENA_TTS_URL) \
		-e ATHENA_TTS_TOKEN=$(ATHENA_TTS_TOKEN) \
		-e DEFAULT_VOICE=$(DEFAULT_VOICE) \
		$(IMAGE):$(TAG)

stop:
	docker stop athena-server && docker rm athena-server

logs:
	docker logs -f athena-server

shell:
	docker exec -it athena-server /bin/bash

health:
	curl -s http://localhost:5003/health

test:
	@echo "Testing prompt without speaker..."
	curl -X POST http://localhost:5003/api/prompt \
		-H "Authorization: Bearer $(AUTH_TOKEN)" \
		-H "Content-Type: application/json" \
		-d '{"prompt": "What is 2+2?"}'
	@echo

test-speaker:
	@echo "Testing prompt with speaker..."
	curl -X POST http://localhost:5003/api/prompt \
		-H "Authorization: Bearer $(AUTH_TOKEN)" \
		-H "Content-Type: application/json" \
		-d '{"prompt": "What is 2+2?", "speaker": true}' \
		| jq -r '.audio' | base64 -d > test-output.wav
	@echo "Saved audio to test-output.wav"

fmt:
	black server.py

lint:
	black --check server.py

install:
	python -m venv venv
	./venv/bin/pip install -r requirements.txt
	./venv/bin/pip install black

uninstall:
	rm -rf venv
