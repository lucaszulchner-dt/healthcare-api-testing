import marimo

__generated_with = "0.23.8"
app = marimo.App(width="medium")


@app.cell
def _():
    # Setup - connect to the lab
    from dicomlab import DicomLab
    import marimo as mo

    lab = DicomLab(
        project_id   = "lucas--rios-sandbox",
        location     = "eu",
        dataset_id   = "tf-healthcare_api_dataset",
        source_store = "dicom_source_burnin",   # curated burn-in source
        deid_store   = "dicom_deid_burnin",     # de-id destination
        export_bucket= "lucas-rios-sandbox-dicom-export",
    )
    return lab, mo


@app.cell
def _():
    # Confirm we're authenticated as the expected identity
    import subprocess
    result = subprocess.run(
        ["gcloud", "auth", "list"], capture_output=True, text=True
    )
    print(result.stdout)
    return


@app.cell
def _(lab):
    # Step 1 - Start clean. Clear both burn-in stores so the demo is deterministic.
    # Note: clearing the DICOM store does NOT delete the streamed BigQuery rows
    # (streaming is append/upsert, not a live mirror).
    lab.clear_store("dicom_source_burnin")
    lab.clear_store("dicom_deid_burnin")
    lab.count_instances("dicom_source_burnin")
    lab.count_instances("dicom_deid_burnin")
    return


@app.cell
def _(lab):
    # Step 2 - Import the burn-in instance from the bucket (single record).
    # Import runs as an async long-running operation with a fixed startup cost
    # for tiny payloads. Non-.dcm files are ignored.
    lab.import_data("gs://dicom-data-source/dicom_burnin/**")
    lab.count_instances("dicom_source_burnin")
    return


@app.cell
def _(lab, mo):
    # Step 3 - BEFORE: the identified image.
    # Burnt-in patient name + DOB should be visible (typically top-left corner).
    before_path = lab.render_first(store_id="dicom_source_burnin",
                                   out_path="before.png", show=False)
    mo.image(before_path)
    return


@app.cell
def _(lab):
    # Step 4 - De-identify.
    # filterProfile = ATTRIBUTE_CONFIDENTIALITY_BASIC_PROFILE (DICOM PS3.15 standard
    # keep/clean/remove rules) + textRedactionMode = REDACT_ALL_TEXT (OCRs and
    # blacks out burnt-in pixel text). NOT REDACT_NO_TEXT, which would leave the
    # pixel text intact and defeat the burn-in demo.
    import time
    _t = time.time()
    lab.deidentify(
        redact_all_text=True,
        filter_profile="ATTRIBUTE_CONFIDENTIALITY_BASIC_PROFILE",
        source_store_id="dicom_source_burnin",
        dest_store_id="dicom_deid_burnin",
    )
    print(f"de-id wall time: {time.time() - _t:.1f}s")
    lab.count_instances("dicom_deid_burnin")
    return


@app.cell
def _(lab, mo):
    # Step 5 - AFTER: the de-identified image, rendered from the deid store.
    # The burnt-in text should now be blacked out.
    # Note: the UID is different - de-id regenerates Study/Series/SOP UIDs by
    # default, so this isn't the "same" instance by identifier. We render the
    # deid store's (only) instance directly.
    after_path = lab.render_first(store_id="dicom_deid_burnin",
                                  out_path="after.png", show=False)
    mo.image(after_path)
    return


app._unparsable_cell(
    r"""
    # Step 6 - Metadata before vs. after, run in BigQuery.
    # Compares identifying tags from the source vs. deid streamed tables.
    # Under the Basic Profile, expect names blanked (Z) and sex/age removed (X)
    # rather than encrypted; UNION ALL stacks the two rows with a state label.
    #
     SELECT
       'BEFORE (identified)'             AS state,
       PatientName.Alphabetic            AS patient_name,
       PatientID                         AS patient_id,
       PatientBirthDate                  AS birth_date,
       PatientSex                        AS sex,
       PatientAge                        AS age,
       ReferringPhysicianName.Alphabetic AS referring_physician,
       AccessionNumber                   AS accession,
       StudyDate                         AS study_date,
       StudyDescription                  AS study_desc,
       SeriesDescription                 AS series_desc,
       BurnedInAnnotation                AS burned_in_flag
     FROM `lucas--rios-sandbox.dicom_metadata.source_burnin_instancesView`
     UNION ALL
     SELECT
       'AFTER (de-identified)',
       PatientName.Alphabetic, PatientID, PatientBirthDate, PatientSex, PatientAge,
       ReferringPhysicianName.Alphabetic, AccessionNumber, StudyDate,
       StudyDescription, SeriesDescription, BurnedInAnnotation
     FROM `lucas--rios-sandbox.dicom_metadata.deid_burnin_instancesView`;
    """,
    name="_"
)


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
