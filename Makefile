.PHONY: test run-mock dry-run ui build-lambda tf-init tf-plan tf-apply clean

help:
	@echo "Multi-Factor Stock Scoring Engine v2.0 commands:"
	@echo "  make test         - Run full pytest test suite"
	@echo "  make run-mock     - Run pipeline locally with synthetic test data"
	@echo "  make ui           - Launch interactive web dashboard UI at localhost:8080"
	@echo "  make dry-run      - Run pipeline without dispatching Discord/SNS alerts"
	@echo "  make build-lambda - Package dependencies and code into Lambda ZIP"
	@echo "  make tf-init      - Initialize Terraform in terraform/ directory"
	@echo "  make tf-plan      - Plan Terraform deployment"
	@echo "  make tf-apply     - Deploy AWS infrastructure via Terraform"
	@echo "  make clean        - Clean temporary build caches and artifacts"

ui:
	python3 run_ui.py

test:
	python3 -m pytest tests/ -v

run-mock:
	python3 run_pipeline.py --mock --verbose

dry-run:
	python3 run_pipeline.py --dry-run

build-lambda:
	./scripts/build_lambda.sh

tf-init:
	cd terraform && terraform init

tf-plan:
	cd terraform && terraform plan

tf-apply:
	cd terraform && terraform apply

clean:
	rm -rf build dist .pytest_cache .coverage htmlcov terraform/lambda_payload.zip
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

