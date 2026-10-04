variable "region" {
  description = "AWS region for the state bucket (use the same region as ../infra)."
  type        = string
  default     = "ap-south-1"
}

variable "project" {
  description = "Short name used in resource names and tags."
  type        = string
  default     = "marathon"
}

variable "create_dynamodb_lock_table" {
  description = <<-EOT
    Also create a DynamoDB table for state locking. Not needed with Terraform
    1.11+, which locks with a lock file in S3 (use_lockfile = true). DynamoDB
    locking is deprecated; enable this only to demonstrate the older method or
    for teams still on Terraform < 1.11.
  EOT
  type        = bool
  default     = false
}
