variable "region" {
  description = "AWS region for everything."
  type        = string
  default     = "ap-south-1"
}

variable "project" {
  description = "Short name used in resource names and tags."
  type        = string
  default     = "marathon"
}

variable "admin_cidr" {
  description = <<-EOT
    Your public IP as a /32 (e.g. 203.0.113.10/32). Only this address can
    reach SSH, Jenkins (8080), the Kubernetes API (6443) and NodePorts.
    Find it with: curl -s https://checkip.amazonaws.com
  EOT
  type        = string

  validation {
    condition     = can(cidrnetmask(var.admin_cidr)) && var.admin_cidr != "0.0.0.0/0"
    error_message = "admin_cidr must be a valid CIDR such as 203.0.113.10/32, and not 0.0.0.0/0 (that would open SSH and Jenkins to the whole internet)."
  }
}

variable "ssh_public_key_path" {
  description = "Public key to install on both servers (create one with: ssh-keygen -t ed25519 -f ~/.ssh/marathon)."
  type        = string
  default     = "~/.ssh/marathon.pub"
}

variable "ssh_private_key_path" {
  description = "Matching private key. Only written into the Ansible inventory; never read by Terraform."
  type        = string
  default     = "~/.ssh/marathon"
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "public_subnet_cidr" {
  type    = string
  default = "10.20.1.0/24"
}

variable "jenkins_instance_type" {
  description = "Jenkins builds Docker images: 2 vCPU / 4 GiB is the comfortable minimum."
  type        = string
  default     = "t3.medium"
}

variable "k3s_instance_type" {
  description = "Runs both apps, PostgreSQL, and later Prometheus + Grafana: 2 vCPU / 8 GiB."
  type        = string
  default     = "t3.large"
}

variable "jenkins_volume_gb" {
  type    = number
  default = 30
}

variable "k3s_volume_gb" {
  type    = number
  default = 30
}

variable "ecr_repositories" {
  description = "One private image repository per app."
  type        = list(string)
  default     = ["doubt-wall", "aiops-triage"]
}

variable "budget_limit_usd" {
  description = "Monthly cost alarm threshold for the whole AWS account."
  type        = number
  default     = 25
}

variable "budget_email" {
  description = "Email for budget alerts (80% spent, 100% forecast). Empty = no budget alarm."
  type        = string
  default     = ""
}
