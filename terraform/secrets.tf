variable "secret_names" {
  description = "A list of secrets to be created and made accessible to the cloud run instance."
  type        = list(string)
  default     = [
    "neo4j-uri",
    "neo4j-username",
    "neo4j-password",
  ]
}

resource "google_secret_manager_secret" "secrets" {
  count = length(var.secret_names)
  secret_id = "${var.service_namespace}-${var.service_name}-${var.environment}-${var.secret_names[count.index]}"
  replication {
    automatic = true
  }
}
