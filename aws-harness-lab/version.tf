terraform {
  required_providers {
    aws = {
      source = "opentofu/aws"
      version = "6.10.0"
    }
  }
}

provider "aws" {
  region = "ap-south-1"
}