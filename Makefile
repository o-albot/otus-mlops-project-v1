.PHONY: help init plan apply destroy clean kubeconfig status check-nodes generate-secret save-outputs upload-venv-to-bucket upload-data-to-bucket upload-src-to-bucket deploy-to-s3 generate-variables
.PHONY: deploy deploy-namespace deploy-secret deploy-postgres deploy-mlflow
.PHONY: deploy-force status-apps urls delete-apps upload-data list-data

help:
	@echo "Commands:"
	@echo "  make init          - Terraform init"
	@echo "  make plan          - Terraform plan"
	@echo "  make apply         - Create infrastructure"
	@echo "  make destroy       - Destroy infrastructure"
	@echo "  make clean         - Clean Terraform cache"
	@echo "  make kubeconfig    - Update kubeconfig"
	@echo "  make check-nodes   - Check cluster nodes"
	@echo "  make status        - Show infrastructure status"
	@echo "  make generate-secret - Generate s3-secret.yaml"
	@echo "  upload-venv-to-bucket   - Upload venv to S3"
	@echo "  upload-data-to-bucket   - Upload data to S3"
	@echo "  upload-src-to-bucket    - Upload src to S3"
	@echo "  deploy-to-s3            - Upload all to S3"
	@echo "  make list-data          - List data in S3"
	@echo "  make generate-variables - Generate variables to airflow"

	@echo ""
	@echo "Deploy commands:"
	@echo "  make deploy        - Deploy all applications"
	@echo "  make deploy-namespace - Create namespace"
	@echo "  make deploy-secret - Deploy S3 secret"
	@echo "  make deploy-postgres - Deploy PostgreSQL"
	@echo "  make deploy-mlflow - Deploy MLflow"
	@echo "  make deploy-airflow - Deploy Airflow"
	@echo "  make deploy-force  - Force recreate all deployments"
	@echo "  make status-apps   - Show applications status"
	@echo "  make urls          - Show external URLs"
	@echo "  make delete-apps   - Delete all applications"

init:
	cd infra && terraform init

plan: init
	cd infra && terraform plan

apply: init
	cd infra && terraform apply -auto-approve
	cd infra && terraform output
	$(MAKE) save-outputs
	$(MAKE) generate-secret
	$(MAKE) kubeconfig
	$(MAKE) check-nodes

destroy: init
	cd infra && terraform destroy -auto-approve

clean:
	rm -rf infra/.terraform infra/.terraform.lock.hcl infra/terraform.tfvars
	rm -f k8s/s3-secret.yaml

save-outputs:
	@echo "Saving outputs to .env..."
	@cd infra && \
	sed -i '/^BUCKET_NAME=/d' ../.env 2>/dev/null || true; \
	sed -i '/^CLUSTER_ID=/d' ../.env 2>/dev/null || true; \
	sed -i '/^CLUSTER_NAME=/d' ../.env 2>/dev/null || true; \
	echo "BUCKET_NAME=$$(terraform output -raw bucket_name 2>/dev/null)" >> ../.env; \
	echo "CLUSTER_ID=$$(terraform output -raw cluster_id 2>/dev/null)" >> ../.env; \
	echo "CLUSTER_NAME=$$(terraform output -raw cluster_name 2>/dev/null)" >> ../.env
	@echo "Outputs saved to .env"

generate-secret:
	@echo "Generating k8s/s3-secret.yaml..."
	@if [ -f .env ]; then \
	. ./.env; \
	export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY BUCKET_NAME; \
	envsubst < k8s/s3-secret.template.yaml > k8s/s3-secret.yaml; \
	echo "✅ k8s/s3-secret.yaml generated"; \
	else \
	echo "❌ .env file not found"; \
	fi

kubeconfig:
	@CLUSTER_ID=$$(grep '^CLUSTER_ID=' .env | cut -d= -f2 | tr -d ' '); \
	if [ -n "$$CLUSTER_ID" ]; then \
	yc managed-kubernetes cluster get-credentials $$CLUSTER_ID --external --force; \
	else \
	echo "CLUSTER_ID not found in .env"; \
	fi

check-nodes:
	kubectl get nodes -o wide

status:
	@if [ -f .env ]; then \
	echo "================================"; \
	echo "Infrastructure Status"; \
	echo "================================"; \
	echo "Project: $$(grep '^PROJECT_NAME=' .env | cut -d= -f2)"; \
	echo "Bucket: $$(grep '^BUCKET_NAME=' .env | cut -d= -f2)"; \
	echo "Cluster: $$(grep '^CLUSTER_NAME=' .env | cut -d= -f2)"; \
	echo "Cluster ID: $$(grep '^CLUSTER_ID=' .env | cut -d= -f2)"; \
	echo "================================"; \
	else \
	echo "No .env file found"; \
	fi

# ========== FULL DEPLOY TO S3 ==========

deploy-to-s3: upload-venv-to-bucket upload-data-to-bucket upload-src-to-bucket list-data
	@echo "All artifacts (venv, data, src) uploaded to S3!"

list-data:
	@echo "Listing data in S3..."
	@if [ -f .env ]; then \
	. ./.env; \
	export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; \
	s3cmd ls s3://$$BUCKET_NAME/; \
	else \
	echo "❌ .env file not found"; \
	fi


# ========== VIRTUAL ENVIRONMENT ==========

upload-venv-to-bucket:
	@echo "Uploading venv to S3..."
	@if [ -f .env ]; then \
	. ./.env; \
	export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; \
	echo "Bucket: $$BUCKET_NAME"; \
	s3cmd put venvs/venv.tar.gz s3://$$BUCKET_NAME/venvs/; \
	echo "✅ venv.tar.gz uploaded to s3://$$S3_BUCKET_NAME/venvs/"; \
	else \
	echo "❌ .env file not found"; \
	fi

# ========== DATA ==========

upload-data-to-bucket:
	@echo "Uploading data to S3..."
	@if [ -f .env ]; then \
	. ./.env; \
	export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; \
	echo "Bucket: $$BUCKET_NAME"; \
	s3cmd sync data/ s3://$$BUCKET_NAME/data/; \
	echo "✅ Data uploaded to s3://$$S3_BUCKET_NAME/data/"; \
	else \
	echo "❌ .env file not found"; \
	fi

# ========== SOURCE CODE ==========

upload-src-to-bucket:
	@echo "Uploading source code to S3..."
	@if [ -f .env ]; then \
	. ./.env; \
	export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; \
	echo "Bucket: $$BUCKET_NAME"; \
	s3cmd sync src/ s3://$$BUCKET_NAME/src/; \
	echo "✅ Source code uploaded to s3://$$S3_BUCKET_NAME/src/"; \
	else \
	echo "❌ .env file not found"; \
	fi

# ========== AIRFLOW VARIABLES ==========

generate-variables:
	@echo "$(YELLOW)Generating variables.json for Airflow...$(NC)"
	@chmod +x scripts/generate_variables.sh
	@./scripts/generate_variables.sh

# ========== DEPLOY SERVICES ==========

deploy: deploy-namespace deploy-secret deploy-postgres deploy-mlflow deploy-airflow
	@echo "✅ All applications deployed successfully!"
	@$(MAKE) status-apps

deploy-namespace:
	@echo "Creating namespace mlops..."
	kubectl create namespace mlops --dry-run=client -o yaml | kubectl apply -f -
	@echo "✅ Namespace created"

deploy-secret:
	@echo "Deploying S3 secret..."
	kubectl apply -f k8s/s3-secret.yaml
	@echo "✅ S3 secret deployed"

deploy-postgres:
	@echo "Deploying PostgreSQL..."
	kubectl apply -f k8s/postgres.yaml
	@echo "✅ PostgreSQL deployed"
	@echo "Waiting for PostgreSQL to be ready..."
	kubectl wait --for=condition=ready pod -l app=postgres -n mlops --timeout=60s 2>/dev/null || true

deploy-mlflow:
	@echo "Deploying MLflow..."
	kubectl apply -f k8s/mlflow.yaml
	@echo "✅ MLflow deployed"
	@echo "Waiting for MLflow to be ready..."
	kubectl wait --for=condition=ready pod -l app=mlflow -n mlops --timeout=60s 2>/dev/null || true

#deploy-airflow:
#	@echo "Deploying Airflow..."
#	kubectl apply -f k8s/airflow.yaml
#	@echo "✅ Airflow deployed"
#	@echo "Waiting for Airflow to be ready..."
#	kubectl wait --for=condition=ready pod -l app=airflow -n mlops --timeout=120s 2>/dev/null || true

deploy-force: deploy-namespace deploy-secret
	@echo "Force recreating deployments..."
	kubectl delete deployment postgres -n mlops --ignore-not-found
	kubectl delete deployment mlflow -n mlops --ignore-not-found
	kubectl delete deployment airflow -n mlops --ignore-not-found
	sleep 5
	$(MAKE) deploy-postgres
	$(MAKE) deploy-mlflow
	$(MAKE) deploy-airflow
	@echo "✅ All applications re-deployed"

status-apps:
	@echo ""
	@echo "================================"
	@echo "Applications Status"
	@echo "================================"
	@echo "Pods in mlops namespace:"
	@kubectl get pods -n mlops
	@echo ""
	@echo "Services in mlops namespace:"
	@kubectl get svc -n mlops
	@echo ""
	@echo "External IPs:"
	@kubectl get svc -n mlops | grep LoadBalancer || echo "  No LoadBalancer services yet"

urls:
	@echo "================================"
	@echo "External URLs"
	@echo "================================"
	@echo "MLflow: http://$$(kubectl get svc -n mlops mlflow -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null):5000"
	@echo "Airflow: http://$$(kubectl get svc -n mlops airflow -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null):8080"
	@echo "(Login: admin / admin)"

delete-apps:
	@echo "Deleting all applications..."
	kubectl delete deployment postgres -n mlops --ignore-not-found
	kubectl delete deployment mlflow -n mlops --ignore-not-found
	kubectl delete deployment airflow -n mlops --ignore-not-found
	kubectl delete service postgres -n mlops --ignore-not-found
	kubectl delete service mlflow -n mlops --ignore-not-found
	kubectl delete service airflow -n mlops --ignore-not-found
	kubectl delete secret s3-credentials -n mlops --ignore-not-found
	@echo "✅ All applications deleted"


