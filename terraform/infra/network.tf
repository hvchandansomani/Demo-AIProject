# A small VPC with one public subnet. Both servers get public IPs so the
# class can reach them directly; security groups decide what's open.
# (Production would put servers in private subnets behind a load balancer.)

data "aws_availability_zones" "available" {
  #checkov:skip=CKV_AWS_394:Only the first zone is used, once, when the subnet is created; later zone additions don't change it.
  state = "available"
}

resource "aws_vpc" "main" {
  #checkov:skip=CKV2_AWS_11:VPC flow logs cost money in CloudWatch Logs; worth enabling in production, skipped for a 2-day class.
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.project}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project}-igw" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidr
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = false # public IPs come from the Elastic IPs in ec2.tf
  tags                    = { Name = "${var.project}-public" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${var.project}-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# The VPC's default security group allows all traffic between its members.
# Take control of it and leave it empty, so nothing can use it by accident.
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project}-default-unused" }
}
