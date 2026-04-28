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
