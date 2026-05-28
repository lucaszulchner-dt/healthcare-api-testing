import marimo

__generated_with = "0.23.8"
app = marimo.App(width="medium")


@app.cell
def _():
    from dicomlab import DicomLab
    import marimo as mo

    lab = DicomLab(
        project_id   = "lucas--rios-sandbox",
        location     = "eu",
        dataset_id   = "tf-healthcare_api_dataset",
        source_store = "dicom_source",
        deid_store   = "dicom_deid",
        export_bucket= "lucas-rios-sandbox-dicom-export",
    )
    return lab, mo


@app.cell
def _():
    import subprocess

    result = subprocess.run(
        ["gcloud", "auth", "list"],
        capture_output=True, text=True
    )
    print(result.stdout)
    return


@app.cell
def _(lab):
    # 1. load sample data
    lab.import_data("gs://spls/gsp626/LungCT-Diagnosis/R_004/*") 
    lab.count_instances("dicom_source")                 # how many images landed
    return


@app.cell
def _(lab, mo):
    # show image
    path = lab.render_first()
    mo.image(path)
    return


@app.cell
def _(lab):
    lab.clear_store("dicom_source")  
    return


@app.cell
def _(lab):
    lab.count_instances("dicom_source")    
    return


@app.cell
def _(lab):


    # 3. de-identify into the deid store, then view AFTER
    lab.deidentify(redact_all_text=True)
    lab.render_first(store_id="dicom_deid")

    # 4. export everything and inspect the file layout
    lab.export_data(prefix="gs://.../export_all")
    lab.export_distribution(prefix="export_all")

    # 5. export a SUBSET using a filter file
    f = lab.build_filter_from_store(level="instance", limit=5)   # gs:// uri
    lab.export_data(prefix="gs://.../export_subset", filter_uri=f)
    lab.export_distribution(prefix="export_subset")

    # housekeeping
    lab.clear_store("dicom_source")       # delete all studies in a store
    lab.clear_export("export_all")        # wipe a GCS export prefix
    return


if __name__ == "__main__":
    app.run()
