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

# NAT Gateway (для выхода в интернет без публичных IP у узлов)
resource "yandex_vpc_gateway" "nat_gateway" {
  name = "${var.project_name}-nat-gateway"
  shared_egress_gateway {}
}

resource "yandex_vpc_route_table" "nat_route" {
  name       = "${var.project_name}-nat-route"
  network_id = yandex_vpc_network.network.id

  static_route {
    destination_prefix = "0.0.0.0/0"
    gateway_id         = yandex_vpc_gateway.nat_gateway.id
  }
}

# Единственная подсеть (с маршрутом через NAT)
resource "yandex_vpc_subnet" "subnet" {
  name           = "${var.project_name}-subnet"
  description    = "Subnet for ${var.project_name} project"
  zone           = var.zone
  network_id     = yandex_vpc_network.network.id
  v4_cidr_blocks = ["10.0.0.0/16"]
  route_table_id = yandex_vpc_route_table.nat_route.id
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

# ========== ЗАДЕРЖКА ПЕРЕД СОЗДАНИЕМ КЛАСТЕРА ==========

resource "time_sleep" "wait_network" {
  create_duration = "30s"
  depends_on      = [yandex_vpc_subnet.subnet]
}

# ========== KUBERNETES CLUSTER (БЕЗ ПУБЛИЧНОГО IP У МАСТЕРА) ==========

resource "yandex_kubernetes_cluster" "k8s_cluster" {
  name        = "${var.project_name}-cluster-${random_string.suffix.result}"
  description = "Kubernetes cluster for ${var.project_name} project"
  network_id  = yandex_vpc_network.network.id

  master {
    version   = var.k8s_version
    public_ip = true   # MASTER C ПУБЛИЧНЫМ IP

    master_location {
      zone      = var.zone
      subnet_id = yandex_vpc_subnet.subnet.id
    }
  }

  service_account_id      = var.service_account_id
  node_service_account_id = var.service_account_id

  depends_on = [time_sleep.wait_network]
}

# ========== ЗАДЕРЖКА ПЕРЕД СОЗДАНИЕМ НОД ==========

resource "time_sleep" "wait_cluster" {
  create_duration = "60s"
  depends_on      = [yandex_kubernetes_cluster.k8s_cluster]
}

# ========== KUBERNETES NODE GROUP (БЕЗ ПУБЛИЧНОГО IP У НОД) ==========

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
      nat        = false   # БЕЗ ПУБЛИЧНОГО IP
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

  depends_on = [time_sleep.wait_cluster]
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