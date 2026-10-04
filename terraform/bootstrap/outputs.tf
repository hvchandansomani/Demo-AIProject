output "state_bucket" {
  description = "Put this in ../infra/backend.hcl as `bucket`."
  value       = aws_s3_bucket.state.bucket
}

output "dynamodb_lock_table" {
  description = "Only set when create_dynamodb_lock_table = true."
  value       = var.create_dynamodb_lock_table ? aws_dynamodb_table.lock[0].name : null
}

output "backend_hcl" {
  description = "Ready-to-paste contents for ../infra/backend.hcl."
  value       = <<-EOT
    bucket       = "${aws_s3_bucket.state.bucket}"
    key          = "${var.project}/infra/terraform.tfstate"
    region       = "${var.region}"
    encrypt      = true
    use_lockfile = true
  EOT
}
