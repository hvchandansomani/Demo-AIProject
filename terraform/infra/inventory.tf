# Writes the Ansible inventory, so Ansible always targets the servers that
# Terraform just created. Terraform hands over to Ansible through this file.
locals {
  ecr_registry = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com"
}

data "aws_caller_identity" "current" {}

resource "local_file" "ansible_inventory" {
  filename        = "${path.module}/../../ansible/inventory/hosts.ini"
  file_permission = "0644"
  content = templatefile("${path.module}/templates/hosts.ini.tftpl", {
    jenkins_public_ip    = aws_eip.jenkins.public_ip
    jenkins_private_ip   = aws_instance.jenkins.private_ip
    k3s_public_ip        = aws_eip.k3s.public_ip
    k3s_private_ip       = aws_instance.k3s.private_ip
    ssh_private_key_path = var.ssh_private_key_path
    region               = var.region
    ecr_registry         = local.ecr_registry
  })
}
