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
