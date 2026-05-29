# ---------------------------------------------------------------------------
# (Optional) Enable required APIs. disable_on_destroy = false so tearing down
# this config won't disable the APIs project-wide.
# ---------------------------------------------------------------------------
resource "google_project_service" "healthcare" {
  count              = var.enable_healthcare_api ? 1 : 0
  service            = "healthcare.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "bigquery" {
  count              = var.enable_bq_streaming ? 1 : 0
  service            = "bigquery.googleapis.com"
  disable_on_destroy = false
}

# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------
resource "google_healthcare_dataset" "this" {
  name      = var.dataset_id
  location  = var.location
  time_zone = var.time_zone

  depends_on = [google_project_service.healthcare]
}

# ---------------------------------------------------------------------------
# Healthcare Service Agent identity.
# Resolves service-<PROJECT_NUMBER>@gcp-sa-healthcare.iam.gserviceaccount.com
# without hardcoding the project number.
# ---------------------------------------------------------------------------
resource "google_project_service_identity" "healthcare_sa" {
  provider = google-beta
  service  = "healthcare.googleapis.com"

  depends_on = [google_project_service.healthcare]
}

# ===========================================================================
# BigQuery metadata catalog (for exploration)
# ===========================================================================
# One BQ dataset holds the streamed DICOM metadata. Streaming auto-creates the
# tables and manages their schema, so we deliberately DO NOT define
# google_bigquery_table resources (a fixed schema would conflict).
#
# NOTE: streaming is APPEND/UPSERT, not a live mirror. Deleting DICOM instances
# does NOT remove their BigQuery rows -> the table grows over time. For a true
# point-in-time snapshot, use a batch metadata export instead.
# ---------------------------------------------------------------------------
resource "google_bigquery_dataset" "dicom_meta" {
  count                      = var.enable_bq_streaming ? 1 : 0
  dataset_id                 = var.bq_dataset_id
  location                   = var.bq_location
  description                = "Streamed DICOM instance metadata (source + de-identified) for exploration."
  delete_contents_on_destroy = true

  depends_on = [google_project_service.bigquery]
}

# Service Agent needs to write rows + run load jobs into BigQuery.
# jobUser must be granted at project level; dataEditor here is project-level
# for simplicity in a sandbox.
resource "google_project_iam_member" "healthcare_sa_bq_data_editor" {
  count   = var.enable_bq_streaming ? 1 : 0
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_project_service_identity.healthcare_sa.email}"
}

resource "google_project_iam_member" "healthcare_sa_bq_job_user" {
  count   = var.enable_bq_streaming ? 1 : 0
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_project_service_identity.healthcare_sa.email}"
}

# ===========================================================================
# DICOM stores
# ===========================================================================
# Source store (original / identified data lands here) -> streams to source table.
resource "google_healthcare_dicom_store" "source" {
  provider = google-beta
  name     = var.source_store_id
  dataset  = google_healthcare_dataset.this.id
  labels   = var.labels

  dynamic "stream_configs" {
    for_each = var.enable_bq_streaming ? [1] : []
    content {
      bigquery_destination {
        table_uri = "bq://${var.project_id}.${google_bigquery_dataset.dicom_meta[0].dataset_id}.${var.source_bq_table}"
      }
    }
  }

  depends_on = [
    google_bigquery_dataset.dicom_meta,
    google_project_iam_member.healthcare_sa_bq_data_editor,
    google_project_iam_member.healthcare_sa_bq_job_user,
  ]
}

# De-identified copies land here -> streams to a SEPARATE table for before/after
# comparison in BigQuery. deidentify() requires this store to already exist.
resource "google_healthcare_dicom_store" "deid" {
  provider = google-beta
  name     = var.deid_store_id
  dataset  = google_healthcare_dataset.this.id
  labels   = var.labels

  dynamic "stream_configs" {
    for_each = var.enable_bq_streaming ? [1] : []
    content {
      bigquery_destination {
        table_uri = "bq://${var.project_id}.${google_bigquery_dataset.dicom_meta[0].dataset_id}.${var.deid_bq_table}"
      }
    }
  }

  depends_on = [
    google_bigquery_dataset.dicom_meta,
    google_project_iam_member.healthcare_sa_bq_data_editor,
    google_project_iam_member.healthcare_sa_bq_job_user,
  ]
}

# ===========================================================================
# Export bucket (DICOM export output + de-id filter files)
# ===========================================================================
resource "google_storage_bucket" "export" {
  name                        = var.export_bucket_name
  location                    = var.location
  uniform_bucket_level_access = true
  force_destroy               = true # convenient for a test/sandbox bucket
}

# Service Agent needs objectAdmin for BOTH import (read) and export (write).
resource "google_storage_bucket_iam_member" "healthcare_sa_object_admin" {
  bucket = google_storage_bucket.export.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_project_service_identity.healthcare_sa.email}"
}


resource "google_storage_bucket_iam_member" "healthcare_sa_source_viewer" {
  bucket = "dicom-data-source"
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_project_service_identity.healthcare_sa.email}"
}

# ===========================================================================
# BURN-IN DEMO stores
# ===========================================================================
# Curated subset of images that have burnt-in pixel text (e.g. MIDI-B CR/US).
# Kept separate so the de-id demo always lands on visibly-redacting images.

# Source burn-in store -> streams to its own table.
resource "google_healthcare_dicom_store" "burnin" {
  provider = google-beta
  name     = var.burnin_store_id
  dataset  = google_healthcare_dataset.this.id
  labels   = var.labels

  dynamic "stream_configs" {
    for_each = var.enable_bq_streaming ? [1] : []
    content {
      bigquery_destination {
        table_uri = "bq://${var.project_id}.${google_bigquery_dataset.dicom_meta[0].dataset_id}.${var.burnin_bq_table}"
      }
    }
  }

  depends_on = [
    google_bigquery_dataset.dicom_meta,
    google_project_iam_member.healthcare_sa_bq_data_editor,
    google_project_iam_member.healthcare_sa_bq_job_user,
  ]
}

# De-identified burn-in copies land here -> separate table for before/after.
# deidentify() requires this store to already exist.
resource "google_healthcare_dicom_store" "deid_burnin" {
  provider = google-beta
  name     = var.deid_burnin_store_id
  dataset  = google_healthcare_dataset.this.id
  labels   = var.labels

  dynamic "stream_configs" {
    for_each = var.enable_bq_streaming ? [1] : []
    content {
      bigquery_destination {
        table_uri = "bq://${var.project_id}.${google_bigquery_dataset.dicom_meta[0].dataset_id}.${var.deid_burnin_bq_table}"
      }
    }
  }

  depends_on = [
    google_bigquery_dataset.dicom_meta,
    google_project_iam_member.healthcare_sa_bq_data_editor,
    google_project_iam_member.healthcare_sa_bq_job_user,
  ]
}