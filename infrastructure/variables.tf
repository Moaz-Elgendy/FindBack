variable "aws_region" {
  type    = string
  default = "eu-west-1"
}
variable "project_name" {
  type    = string
  default = "findback"
}
variable "instance_type" {
  type    = string
  default = "c7i-flex.large"
}
variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
  validation {
    condition     = can(cidrnetmask(var.vpc_cidr))
    error_message = "Use an IPv4 CIDR."
  }
}
variable "subnet_cidr" {
  type    = string
  default = "10.42.1.0/24"
  validation {
    condition     = can(cidrnetmask(var.subnet_cidr))
    error_message = "Use an IPv4 CIDR."
  }
}
variable "ssh_allowed_cidr" {
  type = string
  validation {
    condition     = can(cidrnetmask(var.ssh_allowed_cidr)) && !contains(["0.0.0.0/0"], var.ssh_allowed_cidr)
    error_message = "Restrict SSH to your IPv4 address or trusted network."
  }
}
variable "ssh_public_key" {
  type = string
  validation {
    condition     = startswith(var.ssh_public_key, "ssh-ed25519 ") || startswith(var.ssh_public_key, "ssh-rsa ")
    error_message = "Supply an OpenSSH public key, never a private key."
  }
}
variable "disk_size_gb" {
  type    = number
  default = 30
  validation {
    condition     = var.disk_size_gb >= 20 && var.disk_size_gb <= 100
    error_message = "Use a disk between 20 and 100 GB."
  }
}
variable "github_repository" {
  type    = string
  default = "Moaz-Elgendy/FindBack"
}
variable "deploy_branch" {
  type    = string
  default = "master"
}
