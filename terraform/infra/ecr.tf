# Private image registry: one repository per app.

resource "aws_ecr_repository" "app" {
  #checkov:skip=CKV_AWS_136:AES256 (AWS-managed) encryption at rest is enough for class images; a customer-managed KMS key adds cost and key management.
  for_each = toset(var.ecr_repositories)
  name     = each.value

  # A tag like "42-a1b2c3d" always means the same image: it can't be
  # overwritten, so what was tested is exactly what gets deployed.
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true # AWS's own vulnerability scan, on top of Trivy in Jenkins
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  # Lets `terraform destroy` remove repositories that still contain images
  # (the class tears everything down after each day).
  force_delete = true
}

resource "aws_ecr_lifecycle_policy" "app" {
  for_each   = aws_ecr_repository.app
  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Delete untagged images after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep only the 15 newest images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 15
        }
        action = { type = "expire" }
      },
    ]
  })
}
