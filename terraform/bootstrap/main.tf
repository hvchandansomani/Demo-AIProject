# One-time setup: the S3 bucket that stores Terraform state for ../infra.
#
# Chicken-and-egg: the bucket can't store its own state before it exists,
# so this small stack keeps a LOCAL state file (terraform.tfstate in this
# folder). Keep that file safe, or just re-import the bucket if it's lost.

data "aws_caller_identity" "current" {}

locals {
  # Bucket names are global across all AWS accounts, so the account ID and
  # region are added to make the name unique.
  bucket_name = "${var.project}-tfstate-${data.aws_caller_identity.current.account_id}-${var.region}"
}

resource "aws_s3_bucket" "state" {
  #checkov:skip=CKV_AWS_18:Access logs need a second bucket; production should add them, a class doesn't need them.
  #checkov:skip=CKV2_AWS_62:Event notifications aren't needed for a state bucket.
  #checkov:skip=CKV_AWS_144:Cross-region replication doubles cost; versioning already protects the state history.
  #checkov:skip=CKV_AWS_145:SSE-S3 (AES256) encryption is enabled; a KMS key adds cost and key management.
  bucket = local.bucket_name

  # Protects the state history from an accidental `terraform destroy`.
  # To really delete it: set to false, apply, then empty and destroy.
  lifecycle {
    prevent_destroy = true
  }
}

# Every state change keeps the previous version, so a bad apply or a
# corrupted state can be rolled back.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

# State files contain secrets (IPs, ARNs, sometimes passwords): encrypt them.
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Refuse any request that isn't over HTTPS.
resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.state]
}

# Keep old state versions for 90 days, then let S3 delete them.
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
  depends_on = [aws_s3_bucket_versioning.state]
}

# Optional, legacy: DynamoDB lock table (see variable description).
resource "aws_dynamodb_table" "lock" {
  count        = var.create_dynamodb_lock_table ? 1 : 0
  name         = "${var.project}-tf-lock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }

  point_in_time_recovery {
    enabled = true
  }
}
