project_id = "lucas--rios-sandbox"
location   = "eu"

dataset_id      = "tf-healthcare_api_dataset"
source_store_id = "dicom_source"
deid_store_id   = "dicom_deid"

# Bucket names are GLOBALLY unique. Change if this is taken.
export_bucket_name = "lucas-rios-sandbox-dicom-export"

time_zone = "UTC"

labels = {
  hospital = "hsa"
}

# ---- BigQuery streaming ----
enable_bq_streaming = true
bq_dataset_id       = "dicom_metadata"
bq_location         = "EU"
source_bq_table     = "source_instances"
deid_bq_table       = "deid_instances"

# ---- Burn-in demo stores ----
burnin_store_id      = "dicom_source_burnin"
deid_burnin_store_id = "dicom_deid_burnin"
burnin_bq_table      = "source_burnin_instances"
deid_burnin_bq_table = "deid_burnin_instances"