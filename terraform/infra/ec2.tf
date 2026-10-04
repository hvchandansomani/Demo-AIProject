# Two Ubuntu 24.04 servers: Jenkins (CI/CD) and k3s (single-node Kubernetes).
# Terraform only creates them; Ansible installs and configures the software.

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720108477"] # Canonical, the publisher of official Ubuntu images

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_key_pair" "admin" {
  key_name   = "${var.project}-admin"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

locals {
  # Shared hardening for both servers.
  metadata_options = {
    http_endpoint = "enabled"
    # IMDSv2 only: blocks the classic SSRF trick of reading the instance's
    # IAM credentials through a plain GET to 169.254.169.254.
    http_tokens = "required"
    # 1 hop: processes on the host can reach the metadata service, containers
    # and pods (one extra network hop) cannot, so they can't borrow the role.
    http_put_response_hop_limit = 1
  }
}

resource "aws_instance" "jenkins" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.jenkins_instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.jenkins.id]
  iam_instance_profile   = aws_iam_instance_profile.jenkins.name
  key_name               = aws_key_pair.admin.key_name
  monitoring             = true # 1-minute CloudWatch metrics (used in the CloudWatch phase)
  ebs_optimized          = true

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.jenkins_volume_gb
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    http_endpoint               = local.metadata_options.http_endpoint
    http_tokens                 = local.metadata_options.http_tokens
    http_put_response_hop_limit = local.metadata_options.http_put_response_hop_limit
  }

  # Don't replace the server just because Canonical published a newer image.
  lifecycle {
    ignore_changes = [ami]
  }

  tags = { Name = "${var.project}-jenkins", Role = "jenkins" }
}

resource "aws_instance" "k3s" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.k3s_instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.k3s.id]
  iam_instance_profile   = aws_iam_instance_profile.k3s.name
  key_name               = aws_key_pair.admin.key_name
  monitoring             = true
  ebs_optimized          = true

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.k3s_volume_gb
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    http_endpoint               = local.metadata_options.http_endpoint
    http_tokens                 = local.metadata_options.http_tokens
    http_put_response_hop_limit = local.metadata_options.http_put_response_hop_limit
  }

  lifecycle {
    ignore_changes = [ami]
  }

  tags = { Name = "${var.project}-k3s", Role = "k3s" }
}

# Fixed public IPs: they survive stop/start, so URLs, the Ansible inventory and
# the k3s certificate (which lists the public IP) stay valid overnight.
resource "aws_eip" "jenkins" {
  domain   = "vpc"
  instance = aws_instance.jenkins.id
  tags     = { Name = "${var.project}-jenkins" }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_eip" "k3s" {
  domain   = "vpc"
  instance = aws_instance.k3s.id
  tags     = { Name = "${var.project}-k3s" }

  depends_on = [aws_internet_gateway.main]
}
