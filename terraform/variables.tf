variable "project" {
  type    = string
  default = "windeurope72-private"
}

variable "project_number" {
  type = string
  default = "406572174402"
}

variable "region" {
  type = string
  default = "europe-west1"
}

variable "github_organisation" {
  type    = string
  default = "octue"
}

variable "credentials_file" {
  type    = string
  default = "gcp-credentials.json"
}

variable "service_namespace" {
  type    = string
  default = "octue"
}

variable "service_name" {
  type    = string
  default = "elevations-populator-private"
}

variable "environment" {
  type    = string
  default = "main"
}
