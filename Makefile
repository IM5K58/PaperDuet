.PHONY: dev build test
dev:
	npm run dev
build:
	npm run build
test:
	npm run test:backend
	npm run test:e2e
