.PHONY: help init-train plan-train apply-train destroy-train status-train clean-train
.PHONY: init-prod plan-prod apply-prod destroy-prod status-prod clean-prod clean-all
.PHONY: deploy-to-s3 upload-venv upload-data upload-src upload-dags list-data

help:
	@echo "Training Infrastructure (infra/train):"
	@echo "  make init-train     - Terraform init for training"
	@echo "  make plan-train     - Terraform plan for training"
	echo "  make apply-train    - Create training infrastructure"
	@echo "  make destroy-train  - Destroy training infrastructure"
	@echo "  make status-train   - Show training outputs"
	@echo "  make clean-train    - Clean training Terraform cache"
	@echo ""
	@echo "Production Infrastructure (infra/prod):"
	@echo "  make init-prod      - Terraform init for production"
	@echo "  make plan-prod      - Terraform plan for production"
	@echo "  make apply-prod     - Create production infrastructure"
	@echo "  make destroy-prod   - Destroy production infrastructure"
	@echo "  make status-prod    - Show production outputs"
	@echo "  make clean-prod     - Clean production Terraform cache"
	@echo ""
	@echo "S3 Upload Commands (after apply-train):"
	@echo "  make deploy-to-s3   - Upload all (dags + data + src + venv) to S3"
	@echo "  make upload-dags    - Upload dags/ to S3"
	@echo "  make upload-data    - Upload data/ to S3"
	@echo "  make upload-src     - Upload src/ to S3"
	@echo "  make upload-venv    - Upload venvs/venv.tar.gz to S3"
	@echo "  make list-data      - List all files in S3 bucket"
	@echo ""
	@echo "General:"
	@echo "  make clean-all      - Clean both Terraform caches"

# ========== TRAINING INFRASTRUCTURE ==========

init-train:
	cd infra/train && terraform init

plan-train: init-train
	cd infra/train && terraform plan

apply-train: init-train
	cd infra/train && terraform apply -auto-approve
	cd infra/train && terraform output

destroy-train: init-train
	cd infra/train && terraform destroy -auto-approve

status-train:
	cd infra/train && terraform output

clean-train:
	rm -rf infra/train/.terraform infra/train/.terraform.lock.hcl

# ========== PRODUCTION INFRASTRUCTURE ==========

init-prod:
	cd infra/prod && terraform init

plan-prod: init-prod
	cd infra/prod && terraform plan

apply-prod: init-prod
	cd infra/prod && terraform apply -auto-approve
	cd infra/prod && terraform output

destroy-prod: init-prod
	cd infra/prod && terraform destroy -auto-approve

status-prod:
	cd infra/prod && terraform output

clean-prod:
	rm -rf infra/prod/.terraform infra/prod/.terraform.lock.hcl

# ========== S3 UPLOADS (используем variables.json из infra/train) ==========

deploy-to-s3: upload-dags upload-data upload-src upload-venv list-data
	@echo "All artifacts uploaded to S3!"

upload-dags:
	@echo "Uploading dags to S3..."
	@if [ -f infra/train/variables.json ]; then \
	S3_BUCKET=$$(jq -r '.S3_BUCKET_NAME' infra/train/variables.json); \
	AWS_ACCESS_KEY_ID=$$(jq -r '.S3_ACCESS_KEY' infra/train/variables.json); \
	AWS_SECRET_ACCESS_KEY=$$(jq -r '.S3_SECRET_KEY' infra/train/variables.json); \
	export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; \
	echo "Bucket: $$S3_BUCKET"; \
	s3cmd sync dags/ s3://$$S3_BUCKET/dags/; \
	echo "✅ dags uploaded to s3://$$S3_BUCKET/dags/"; \
	else \
	echo "❌ infra/train/variables.json not found. Run 'make apply-train' first."; \
	exit 1; \
	fi

upload-data:
	@echo "Uploading data to S3..."
	@if [ -f infra/train/variables.json ]; then \
	S3_BUCKET=$$(jq -r '.S3_BUCKET_NAME' infra/train/variables.json); \
	AWS_ACCESS_KEY_ID=$$(jq -r '.S3_ACCESS_KEY' infra/train/variables.json); \
	AWS_SECRET_ACCESS_KEY=$$(jq -r '.S3_SECRET_KEY' infra/train/variables.json); \
	export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; \
	echo "Bucket: $$S3_BUCKET"; \
	s3cmd sync input_data/ s3://$$S3_BUCKET/input_data/; \
	echo "✅ data uploaded to s3://$$S3_BUCKET/input_data/"; \
	else \
	echo "❌ infra/train/variables.json not found. Run 'make apply-train' first."; \
	exit 1; \
	fi

upload-src:
	@echo "Uploading source code to S3..."
	@if [ -f infra/train/variables.json ]; then \
	S3_BUCKET=$$(jq -r '.S3_BUCKET_NAME' infra/train/variables.json); \
	AWS_ACCESS_KEY_ID=$$(jq -r '.S3_ACCESS_KEY' infra/train/variables.json); \
	AWS_SECRET_ACCESS_KEY=$$(jq -r '.S3_SECRET_KEY' infra/train/variables.json); \
	export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; \
	echo "Bucket: $$S3_BUCKET"; \
	s3cmd sync src/ s3://$$S3_BUCKET/src/; \
	echo "✅ source code uploaded to s3://$$S3_BUCKET/src/"; \
	else \
	echo "❌ infra/train/variables.json not found. Run 'make apply-train' first."; \
	exit 1; \
	fi

upload-venv:
	@echo "Uploading venv to S3..."
	@if [ -f infra/train/variables.json ]; then \
	S3_BUCKET=$$(jq -r '.S3_BUCKET_NAME' infra/train/variables.json); \
	AWS_ACCESS_KEY_ID=$$(jq -r '.S3_ACCESS_KEY' infra/train/variables.json); \
	AWS_SECRET_ACCESS_KEY=$$(jq -r '.S3_SECRET_KEY' infra/train/variables.json); \
	export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; \
	echo "Bucket: $$S3_BUCKET"; \
	s3cmd put venvs/venv.tar.gz s3://$$S3_BUCKET/venvs/; \
	echo "✅ venv uploaded to s3://$$S3_BUCKET/venvs/"; \
	else \
	echo "❌ infra/train/variables.json not found. Run 'make apply-train' first."; \
	exit 1; \
	fi

list-data:
	@echo "Listing data in S3..."
	@if [ -f infra/train/variables.json ]; then \
	S3_BUCKET=$$(jq -r '.S3_BUCKET_NAME' infra/train/variables.json); \
	AWS_ACCESS_KEY_ID=$$(jq -r '.S3_ACCESS_KEY' infra/train/variables.json); \
	AWS_SECRET_ACCESS_KEY=$$(jq -r '.S3_SECRET_KEY' infra/train/variables.json); \
	export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; \
	s3cmd ls -r s3://$$S3_BUCKET/; \
	else \
	echo "❌ infra/train/variables.json not found. Run 'make apply-train' first."; \
	exit 1; \
	fi

# ========== CLEAN ALL ==========

clean-all: clean-train clean-prod
	@echo "All Terraform caches cleaned"
