terraform {
  required_version = ">= 1.0.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

# ==========================================
# 1. REPOSITORIOS EN AMAZON ECR
# ==========================================

resource "aws_ecr_repository" "backend_repo" {
  name                 = "cloud-stockflow-backend"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Environment = "Development"
    Project     = "Cloud-StockFlow"
  }
}

resource "aws_ecr_repository" "frontend_repo" {
  name                 = "cloud-stockflow-frontend"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Environment = "Development"
    Project     = "Cloud-StockFlow"
  }
}

# ==========================================
# 2. GRUPO DE SEGURIDAD (SECURITY GROUP)
# ==========================================

resource "aws_security_group" "k3s_sg" {
  name        = "k3s-security-group"
  description = "Permite trafico HTTP, SSH y puertos NodePort de Kubernetes"

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 30000
    to_port     = 32767
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "k3s-security-group"
  }
}

# ==========================================
# 3. INSTANCIA EC2 (NODO KUBERNETES K3S)
# ==========================================

resource "aws_instance" "k3s_server" {
  ami           = "ami-0c7217cdde317cfec" # Ubuntu 22.04 LTS en us-east-1
  instance_type = "t3.medium"
  key_name      = "vockey" #

  vpc_security_group_ids = [aws_security_group.k3s_sg.id]

  user_data = <<-EOF
              #!/bin/bash
              curl -sfL https://get.k3s.io | sh -
              chmod 644 /etc/rancher/k3s/k3s.yaml
              EOF

  tags = {
    Name = "k3s-kubernetes-server"
  }
}

# ==========================================
# 4. OUTPUTS
# ==========================================

output "backend_ecr_url" {
  description = "URL del repositorio ECR para el Backend"
  value       = aws_ecr_repository.backend_repo.repository_url
}

output "frontend_ecr_url" {
  description = "URL del repositorio ECR para el Frontend"
  value       = aws_ecr_repository.frontend_repo.repository_url
}

output "k3s_public_ip" {
  description = "IP publica de la instancia EC2 con Kubernetes"
  value       = aws_instance.k3s_server.public_ip
}