output "jenkins_url" {
  value = "http://${aws_eip.jenkins.public_ip}:8080"
}

output "app_url" {
  description = "Doubt Wall for learners; the console is at /ops."
  value       = "http://${aws_eip.k3s.public_ip}"
}

output "jenkins_public_ip" {
  value = aws_eip.jenkins.public_ip
}

output "k3s_public_ip" {
  value = aws_eip.k3s.public_ip
}

output "k3s_private_ip" {
  value = aws_instance.k3s.private_ip
}

output "ecr_registry" {
  description = "Set as ECR_REGISTRY in Jenkins (Ansible does this for you)."
  value       = local.ecr_registry
}

output "ssh_commands" {
  value = {
    jenkins = "ssh -i ${var.ssh_private_key_path} ubuntu@${aws_eip.jenkins.public_ip}"
    k3s     = "ssh -i ${var.ssh_private_key_path} ubuntu@${aws_eip.k3s.public_ip}"
  }
}

output "next_step" {
  value = "Inventory written to ansible/inventory/hosts.ini. Next: cd ../../ansible && ansible-playbook site.yml"
}
