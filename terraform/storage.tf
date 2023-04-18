resource "google_storage_bucket" "crash_diagnostics" {
  name                        = "${var.service_namespace}-${var.service_name}"
  location                    = "EU"
  force_destroy               = true
  uniform_bucket_level_access = true
}
