#!/bin/bash

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}Creating Terraform configuration files for MLOps infrastructure...${NC}"

# Create variables.tf
cat > variables.tf << 'EOF'
# ========== REQUIRED VARIABLES ==========

variable "cloud_id" {
  description = "Yandex Cloud cloud ID"
  type        = string
}

variable "folder_id" {
  description = "Yandex Cloud folder ID"
  type        = string
}

variable "zone" {
  description = "Yandex Cloud availability zone"
  type        = string
  default     = "ru-central1-a"
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "mlops"
}

variable "yc_token" {
  description = "Yandex Cloud IAM token"
  type        = string
  sensitive   = true
}

# ========== SSH AND CLUSTER VARIABLES ==========

variable "public_ssh_key" {
  description = "Public SSH key for cluster nodes"
  type        = string
}

variable "service_account_id" {
  description = "Service account ID for Kubernetes cluster"
  type        = string
}

# ========== OPTIONAL VARIABLES ==========

variable "k8s_version" {
  description = "Kubernetes version"
  type        = string
  default     = "1.31"
}

variable "node_cores" {
  description = "Number of CPU cores per node"
  type        = number
  default     = 2
}

variable "node_memory" {
  description = "Memory per node in GB"
  type        = number
  default     = 4
}

variable "node_count" {
  description = "Number of nodes in the node group"
  type        = number
  default     = 3
}
EOF

# Create providers.tf
cat > providers.tf << 'EOF'
terraform {
  required_providers {
    yandex = {
      source  = "yandex-cloud/yandex"
      version = "~> 0.130"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.8"
    }
  }
  required_version = ">= 1.0"
}

provider "yandex" {
  token     = var.yc_token
  cloud_id  = var.cloud_id
  folder_id = var.folder_id
  zone      = var.zone
}
EOF

# Create main.tf
cat > main.tf << 'EOF'
# ========== RANDOM SUFFIX FOR UNIQUE NAMES ==========

resource "random_string" "suffix" {
  length  = 4
  special = false
  upper   = false
}

# ========== NETWORK RESOURCES ==========

resource "yandex_vpc_network" "network" {
  name        = "${var.project_name}-network"
  description = "Network for ${var.project_name} project"
}

resource "yandex_vpc_subnet" "subnet" {
  name           = "${var.project_name}-subnet"
  description    = "Subnet for ${var.project_name} project"
  zone           = var.zone
  network_id     = yandex_vpc_network.network.id
  v4_cidr_blocks = ["10.0.0.0/16"]
}

# ========== S3 BUCKET ==========

resource "random_string" "bucket_suffix" {
  length  = 4
  special = false
  upper   = false
}

resource "yandex_storage_bucket" "bucket" {
  bucket        = "${var.project_name}-${random_string.bucket_suffix.result}"
  folder_id     = var.folder_id
  force_destroy = true
  
  anonymous_access_flags {
    read = false
    list = false
  }
}

# ========== KUBERNETES CLUSTER ==========

resource "yandex_kubernetes_cluster" "k8s_cluster" {
  name        = "${var.project_name}-cluster-${random_string.suffix.result}"
  description = "Kubernetes cluster for ${var.project_name} project"
  network_id  = yandex_vpc_network.network.id
  
  master {
    version   = var.k8s_version
    public_ip = true
    
    master_location {
      zone      = var.zone
      subnet_id = yandex_vpc_subnet.subnet.id
    }
  }
  
  service_account_id      = var.service_account_id
  node_service_account_id = var.service_account_id
}

resource "yandex_kubernetes_node_group" "node_group" {
  cluster_id = yandex_kubernetes_cluster.k8s_cluster.id
  name       = "${var.project_name}-nodes"
  
  instance_template {
    platform_id = "standard-v2"
    
    resources {
      cores         = var.node_cores
      memory        = var.node_memory
      core_fraction = 100
    }
    
    boot_disk {
      type = "network-hdd"
      size = 64
    }
    
    network_interface {
      subnet_ids = [yandex_vpc_subnet.subnet.id]
      nat        = true
    }
    
    metadata = {
      ssh-keys = "ubuntu:${var.public_ssh_key}"
    }
  }
  
  scale_policy {
    fixed_scale {
      size = var.node_count
    }
  }
  
  allocation_policy {
    location {
      zone = var.zone
    }
  }
}

# ========== SECURITY GROUP FOR DATAPROC ==========

resource "yandex_vpc_security_group" "dataproc_sg" {
  name        = "${var.project_name}-dataproc-sg"
  description = "Security group for Dataproc cluster"
  network_id  = yandex_vpc_network.network.id

  ingress {
    protocol       = "TCP"
    description    = "Allow SSH from anywhere"
    v4_cidr_blocks = ["0.0.0.0/0"]
    port           = 22
  }

  ingress {
    protocol       = "TCP"
    description    = "Allow internal cluster communication"
    v4_cidr_blocks = ["10.0.0.0/16"]
    from_port      = 0
    to_port        = 65535
  }

  egress {
    protocol       = "ANY"
    description    = "Allow all outbound traffic"
    v4_cidr_blocks = ["0.0.0.0/0"]
    from_port      = 0
    to_port        = 65535
  }
}

# ========== SERVICE ACCOUNT FOR DATAPROC ==========

resource "yandex_iam_service_account" "dataproc_sa" {
  name        = "${var.project_name}-dataproc-sa"
  description = "Service account for Dataproc cluster"
  folder_id   = var.folder_id
}

resource "yandex_iam_service_account_key" "dataproc_sa_key" {
  service_account_id = yandex_iam_service_account.dataproc_sa.id
  format             = "PEM_FILE"
  description        = "Service account key for Airflow Dataproc operator"
}

# IAM roles for Dataproc service account
resource "yandex_resourcemanager_folder_iam_member" "dataproc_agent" {
  folder_id = var.folder_id
  role      = "mdb.dataproc.agent"
  member    = "serviceAccount:${yandex_iam_service_account.dataproc_sa.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "dataproc_storage_editor" {
  folder_id = var.folder_id
  role      = "storage.editor"
  member    = "serviceAccount:${yandex_iam_service_account.dataproc_sa.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "dataproc_compute_admin" {
  folder_id = var.folder_id
  role      = "compute.admin"
  member    = "serviceAccount:${yandex_iam_service_account.dataproc_sa.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "dataproc_vpc_user" {
  folder_id = var.folder_id
  role      = "vpc.user"
  member    = "serviceAccount:${yandex_iam_service_account.dataproc_sa.id}"
}
EOF

# Create outputs.tf
cat > outputs.tf << 'EOF'
# ========== NETWORK OUTPUTS ==========

output "yc_zone" {
  description = "Yandex Cloud availability zone"
  value       = var.zone
}

output "yc_folder_id" {
  description = "Yandex Cloud folder ID"
  value       = var.folder_id
  sensitive   = true
}

output "subnet_id" {
  description = "Subnet ID"
  value       = yandex_vpc_subnet.subnet.id
}

# ========== S3 BUCKET OUTPUTS ==========

output "bucket_name" {
  description = "S3 bucket name"
  value       = yandex_storage_bucket.bucket.id
}

output "bucket_url" {
  description = "S3 bucket URL"
  value       = "s3://${yandex_storage_bucket.bucket.id}"
}

# ========== KUBERNETES OUTPUTS ==========

output "cluster_id" {
  description = "Kubernetes cluster ID"
  value       = yandex_kubernetes_cluster.k8s_cluster.id
}

output "cluster_name" {
  description = "Kubernetes cluster name"
  value       = yandex_kubernetes_cluster.k8s_cluster.name
}

output "node_group_id" {
  description = "Node group ID"
  value       = yandex_kubernetes_node_group.node_group.id
}

output "node_group_name" {
  description = "Node group name"
  value       = yandex_kubernetes_node_group.node_group.name
}

# ========== DATAPROC OUTPUTS ==========

output "dp_service_account_id" {
  description = "Dataproc service account ID"
  value       = yandex_iam_service_account.dataproc_sa.id
}

output "dp_service_account_json" {
  description = "Dataproc service account JSON key (sensitive)"
  value       = yandex_iam_service_account_key.dataproc_sa_key.private_key
  sensitive   = true
}

output "dp_public_ssh_key" {
  description = "Public SSH key for Dataproc nodes"
  value       = var.public_ssh_key
}

output "dp_security_group_id" {
  description = "Security group ID for Dataproc"
  value       = yandex_vpc_security_group.dataproc_sg.id
}

# ========== PROJECT INFO OUTPUTS ==========

output "project_info" {
  description = "Project information"
  value = {
    project_name    = var.project_name
    zone            = var.zone
    cluster_version = var.k8s_version
    node_count      = var.node_count
    node_cores      = var.node_cores
    node_memory     = var.node_memory
  }
}
EOF

# Create backend.tf (optional)
cat > backend.tf << 'EOF'
# Uncomment to store state in S3 bucket
# terraform {
#   backend "s3" {
#     endpoint   = "https://storage.yandexcloud.net"
#     bucket     = "your-terraform-state-bucket"
#     region     = "ru-central1"
#     key        = "terraform.tfstate"
#     skip_region_validation      = true
#     skip_credentials_validation = true
#     skip_request_payment        = true
#   }
# }
EOF

echo -e "${GREEN}All Terraform configuration files created successfully!${NC}"
echo ""
echo -e "${YELLOW}Your terraform.tfvars file already exists. Make sure it has all required variables:${NC}"
echo "  - cloud_id"
echo "  - folder_id"
echo "  - zone"
echo "  - project_name"
echo "  - yc_token"
echo "  - public_ssh_key"
echo "  - service_account_id"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Review and edit terraform.tfvars if needed"
echo "2. Run: terraform init"
echo "3. Run: terraform plan"
echo "4. Run: terraform apply -auto-approve"