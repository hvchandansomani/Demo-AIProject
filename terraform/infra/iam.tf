# Each server gets an IAM role instead of AWS access keys. The AWS CLI on the
# server picks up short-lived credentials automatically, so there are no
# long-lived keys to leak, rotate or copy into Jenkins.

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

locals {
  ecr_repo_arns = [for r in aws_ecr_repository.app : r.arn]
}

# ---------------------------------------------------------------- Jenkins: push images
resource "aws_iam_role" "jenkins" {
  name               = "${var.project}-jenkins"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

data "aws_iam_policy_document" "jenkins_ecr" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # this action doesn't support resource-level permissions
  }
  statement {
    sid = "PushAndPullOnlyOurRepos"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = local.ecr_repo_arns
  }
}

resource "aws_iam_role_policy" "jenkins_ecr" {
  name   = "ecr-push"
  role   = aws_iam_role.jenkins.id
  policy = data.aws_iam_policy_document.jenkins_ecr.json
}

resource "aws_iam_instance_profile" "jenkins" {
  name = "${var.project}-jenkins"
  role = aws_iam_role.jenkins.name
}

# ---------------------------------------------------------------- k3s: pull images
resource "aws_iam_role" "k3s" {
  name               = "${var.project}-k3s"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

data "aws_iam_policy_document" "k3s_ecr" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "PullOnlyOurRepos"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = local.ecr_repo_arns
  }
}

resource "aws_iam_role_policy" "k3s_ecr" {
  name   = "ecr-pull"
  role   = aws_iam_role.k3s.id
  policy = data.aws_iam_policy_document.k3s_ecr.json
}

resource "aws_iam_instance_profile" "k3s" {
  name = "${var.project}-k3s"
  role = aws_iam_role.k3s.name
}

# ---------------------------------------------------------------- both: Session Manager
# Lets you open a shell from the AWS console (Systems Manager > Session
# Manager) even if SSH breaks or your IP changes. No port needs to be open.
resource "aws_iam_role_policy_attachment" "jenkins_ssm" {
  role       = aws_iam_role.jenkins.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "k3s_ssm" {
  role       = aws_iam_role.k3s.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
