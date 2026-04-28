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
