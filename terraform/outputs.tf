output "project_id" {
  value = var.project_id
}

output "location" {
  value = var.location
}

output "dataset_id" {
  value = google_healthcare_dataset.this.name
}

output "source_store_id" {
  value = google_healthcare_dicom_store.source.name
}

output "deid_store_id" {
  value = google_healthcare_dicom_store.deid.name
}

output "export_bucket" {
  value = google_storage_bucket.export.name
}

output "healthcare_service_agent" {
  value = google_project_service_identity.healthcare_sa.email
}

output "bq_dataset" {
  value       = var.enable_bq_streaming ? google_bigquery_dataset.dicom_meta[0].dataset_id : null
  description = "BigQuery dataset holding streamed DICOM metadata (null if streaming disabled)."
}

output "bq_source_table" {
  value = var.enable_bq_streaming ? "${var.project_id}.${var.bq_dataset_id}.${var.source_bq_table}" : null
}

output "bq_deid_table" {
  value = var.enable_bq_streaming ? "${var.project_id}.${var.bq_dataset_id}.${var.deid_bq_table}" : null
}

# Full resource paths, handy to paste into curl / Python.
output "source_store_path" {
  value = "projects/${var.project_id}/locations/${var.location}/datasets/${google_healthcare_dataset.this.name}/dicomStores/${google_healthcare_dicom_store.source.name}"
}

output "deid_store_path" {
  value = "projects/${var.project_id}/locations/${var.location}/datasets/${google_healthcare_dataset.this.name}/dicomStores/${google_healthcare_dicom_store.deid.name}"
}
