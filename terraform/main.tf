terraform {
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

# Repositorio ECR para el Backend
resource "aws_ecr_repository" "backend_repo" {
  name                 = "cloud-stockflow-backend"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Repositorio ECR para el Frontend
resource "aws_ecr_repository" "frontend_repo" {
  name                 = "cloud-stockflow-frontend"
  image_tag_mutability = "MUTABLE"
  force_delete         = true 

  image_scanning_configuration {
    scan_on_push = true
  }
}