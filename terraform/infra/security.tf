# Firewall rules. Default is "deny"; each rule below opens one thing.
#
#   Port         Jenkins          k3s
#   22  SSH      admin_cidr       admin_cidr
#   8080 Jenkins admin_cidr       -
#   80  HTTP     -                everyone (learners use the Doubt Wall)
#   6443 K8s API -                admin_cidr + Jenkins server
#   30000-32767  -                admin_cidr (NodePorts: Argo CD, Grafana)

resource "aws_security_group" "jenkins" {
  name        = "${var.project}-jenkins"
  description = "Jenkins server"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${var.project}-jenkins" }
}

resource "aws_security_group" "k3s" {
  name        = "${var.project}-k3s"
  description = "k3s node"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${var.project}-k3s" }
}

# ---------------------------------------------------------------- Jenkins
resource "aws_vpc_security_group_ingress_rule" "jenkins_ssh" {
  security_group_id = aws_security_group.jenkins.id
  description       = "SSH from the admin IP"
  cidr_ipv4         = var.admin_cidr
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
}

resource "aws_vpc_security_group_ingress_rule" "jenkins_ui" {
  security_group_id = aws_security_group.jenkins.id
  description       = "Jenkins UI from the admin IP"
  cidr_ipv4         = var.admin_cidr
  ip_protocol       = "tcp"
  from_port         = 8080
  to_port           = 8080
}

resource "aws_vpc_security_group_egress_rule" "jenkins_all" {
  security_group_id = aws_security_group.jenkins.id
  description       = "Outbound: packages, Docker Hub, ECR, GitHub, k3s API"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# ---------------------------------------------------------------- k3s
resource "aws_vpc_security_group_ingress_rule" "k3s_ssh" {
  security_group_id = aws_security_group.k3s.id
  description       = "SSH from the admin IP"
  cidr_ipv4         = var.admin_cidr
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
}

resource "aws_vpc_security_group_ingress_rule" "k3s_http" {
  #checkov:skip=CKV_AWS_260:Intentional. Learners open the Doubt Wall from anywhere; admin paths need a token.
  security_group_id = aws_security_group.k3s.id
  description       = "Doubt Wall and AIOps Triage through Traefik (public on purpose)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_ingress_rule" "k3s_api_admin" {
  security_group_id = aws_security_group.k3s.id
  description       = "Kubernetes API from the admin IP (kubectl from your laptop)"
  cidr_ipv4         = var.admin_cidr
  ip_protocol       = "tcp"
  from_port         = 6443
  to_port           = 6443
}

resource "aws_vpc_security_group_ingress_rule" "k3s_api_jenkins" {
  security_group_id            = aws_security_group.k3s.id
  description                  = "Kubernetes API from the Jenkins server (deploy stage)"
  referenced_security_group_id = aws_security_group.jenkins.id
  ip_protocol                  = "tcp"
  from_port                    = 6443
  to_port                      = 6443
}

resource "aws_vpc_security_group_ingress_rule" "k3s_nodeports" {
  security_group_id = aws_security_group.k3s.id
  description       = "NodePorts (Argo CD, Grafana) from the admin IP"
  cidr_ipv4         = var.admin_cidr
  ip_protocol       = "tcp"
  from_port         = 30000
  to_port           = 32767
}

resource "aws_vpc_security_group_egress_rule" "k3s_all" {
  security_group_id = aws_security_group.k3s.id
  description       = "Outbound: images, packages, Claude API"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
