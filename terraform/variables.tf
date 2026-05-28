variable "project_id" {
  type        = string
  description = "GCP project ID."
}

variable "location" {
  type        = string
  description = "Healthcare dataset + bucket location (e.g. 'eu', 'us', 'us-central1'). DICOM de-id requires source and destination in the SAME location."
  default     = "eu"
}

variable "dataset_id" {
  type        = string
  description = "Healthcare dataset ID."
  default     = "healthcare_api_dataset"
}

variable "source_store_id" {
  type        = string
  description = "DICOM store that holds the original (identified) data."
  default     = "dicom_source"
}

variable "deid_store_id" {
  type        = string
  description = "DICOM store that receives de-identified copies. Must exist before calling deidentify."
  default     = "dicom_deid"
}

variable "export_bucket_name" {
  type        = string
  description = "Globally-unique GCS bucket name for DICOM export + filter files."
}

variable "time_zone" {
  type        = string
  description = "Dataset default time zone."
  default     = "UTC"
}

variable "labels" {
  type        = map(string)
  description = "Labels applied to the DICOM stores."
  default     = {}
}

variable "enable_healthcare_api" {
  type        = bool
  description = "Whether Terraform should enable the Healthcare API."
  default     = true
}

# ---- BigQuery streaming ----------------------------------------------------
variable "enable_bq_streaming" {
  type        = bool
  description = "Stream DICOM metadata from both stores into BigQuery for exploration."
  default     = true
}

variable "bq_dataset_id" {
  type        = string
  description = "BigQuery dataset that holds the streamed DICOM metadata tables."
  default     = "dicom_metadata"
}

variable "bq_location" {
  type        = string
  description = "BigQuery dataset location. Use 'EU' or 'US' multi-region (case-insensitive) or a specific region."
  default     = "EU"
}

variable "source_bq_table" {
  type        = string
  description = "BigQuery table name for the SOURCE store metadata stream."
  default     = "source_instances"
}

variable "deid_bq_table" {
  type        = string
  description = "BigQuery table name for the DE-IDENTIFIED store metadata stream."
  default     = "deid_instances"
}
