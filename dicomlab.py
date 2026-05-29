"""
dicom_lab.py
============
A small, notebook-friendly wrapper around the Google Cloud Healthcare API
(DICOM) for testing import / de-identify / export workflows.

Install (Cloud Shell already has most of these):
    pip install --upgrade google-api-python-client google-auth \
                          google-cloud-storage requests
    # optional, only for inline image display / BQ queries:
    pip install google-cloud-bigquery ipython

Auth:
    Uses Application Default Credentials. In Cloud Shell you're already logged in.
    Locally:  gcloud auth application-default login

Quick start (in a notebook)::

    from dicom_lab import DicomLab

    lab = DicomLab(
        project_id   = "lucas--rios-sandbox",
        location     = "eu",
        dataset_id   = "healthcare_api_dataset",
        source_store = "dicom_source",
        deid_store   = "dicom_deid",
        export_bucket= "lucas-rios-sandbox-dicom-export",
    )

    # 1. load sample data
    lab.import_data("gs://spls/gsp626/LungCT-Diagnosis/R_004/*")
    lab.count_instances()                 # how many images landed

    # 2. view BEFORE
    lab.render_first()                    # -> dicom_source_first.png (shown inline)

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
"""

from __future__ import annotations

import time
from typing import Optional

import requests
import google.auth
from google.auth.transport.requests import Request as _AuthRequest
from googleapiclient import discovery

_BASE = "https://healthcare.googleapis.com/v1"
# DICOM JSON tag keywords we read out of QIDO responses
_TAG_STUDY = "0020000D"
_TAG_SERIES = "0020000E"
_TAG_INSTANCE = "00080018"


class DicomLab:
    def __init__(
        self,
        project_id: str,
        location: str,
        dataset_id: str,
        source_store: str,
        deid_store: Optional[str] = None,
        export_bucket: Optional[str] = None,
    ):
        self.project_id = project_id
        self.location = location
        self.dataset_id = dataset_id
        self.source_store_id = source_store
        self.deid_store_id = deid_store
        self.export_bucket = export_bucket

        self.creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        self.client = discovery.build(
            "healthcare", "v1", credentials=self.creds, cache_discovery=False
        )
        self._storage = None  # lazy

    # ------------------------------------------------------------------ paths
    def _dataset_path(self) -> str:
        return (
            f"projects/{self.project_id}/locations/{self.location}"
            f"/datasets/{self.dataset_id}"
        )

    def store_path(self, store_id: Optional[str] = None) -> str:
        store_id = store_id or self.source_store_id
        return f"{self._dataset_path()}/dicomStores/{store_id}"

    def _dicomweb_url(self, store_id: Optional[str], suffix: str = "") -> str:
        base = f"{_BASE}/{self.store_path(store_id)}/dicomWeb"
        return f"{base}/{suffix}" if suffix else base

    # ------------------------------------------------------------------ auth
    def _token(self) -> str:
        if not self.creds.valid:
            self.creds.refresh(_AuthRequest())
        return self.creds.token

    def _headers(self, accept: str = "application/dicom+json") -> dict:
        return {"Authorization": f"Bearer {self._token()}", "Accept": accept}

    # ------------------------------------------------------------- LRO poll
    def _wait(self, op_name: str, timeout: int = 1200, poll: int = 5) -> dict:
        """Poll a long-running operation.

        Behaviour on completion:
          - success>0, failure=0          -> "done. success=N failure=0"
          - success=0, failure>0          -> "no new records ..." (e.g. all
                                             files already existed; not raised)
          - success>0 AND failure>0       -> "partial: success=X failure=Y"
          - success=0 AND no counters     -> raise (operation truly failed)
        The op dict is always returned so callers can inspect counters.
        """
        ops = self.client.projects().locations().datasets().operations()
        start = time.time()
        while True:
            op = ops.get(name=op_name).execute()
            if op.get("done"):
                counter = op.get("metadata", {}).get("counter", {})
                success = int(counter.get("success", 0))
                failure = int(counter.get("failure", 0))
                has_error = bool(op.get("error"))

                if success == 0 and failure == 0 and has_error:
                    # No per-resource counters at all -> a genuine job failure.
                    raise RuntimeError(f"Operation failed: {op['error']}")

                if success == 0 and failure > 0:
                    # Nothing new was written. Most common cause on re-import:
                    # every instance already exists (the API ignores duplicates,
                    # but reports them as per-file failures).
                    print(f"  no new records (failure={failure}, success=0) — "
                          f"likely all already present; nothing changed.")
                elif failure > 0:
                    print(f"  partial: success={success} failure={failure} — "
                          f"some resources were skipped/failed.")
                else:
                    print(f"  done. success={success} failure={failure}")
                return op

            if time.time() - start > timeout:
                raise TimeoutError(f"Timed out waiting for {op_name}")
            time.sleep(poll)

    # =================================================================
    #  CORE OPERATIONS
    # =================================================================
    def import_data(
        self, gcs_uri: str, store_id: Optional[str] = None, wait: bool = True
    ):
        """Import .dcm objects from GCS into a store. gcs_uri must be a full
        'gs://bucket/path/*' (wildcards allowed)."""
        store = self.store_path(store_id)
        body = {"gcsSource": {"uri": gcs_uri}}
        print(f"Import {gcs_uri} -> {store_id or self.source_store_id}")
        op = (
            self.client.projects()
            .locations()
            .datasets()
            .dicomStores()
            .import_(name=store, body=body)
            .execute()
        )
        return self._wait(op["name"]) if wait else op

    def export_data(
        self,
        prefix: Optional[str] = None,
        store_id: Optional[str] = None,
        filter_uri: Optional[str] = None,
        mime_type: str = "application/dicom",
        wait: bool = True,
    ):
        """Export a store to a GCS prefix. Output layout is one .dcm per
        instance: <prefix>/<study>/<series>/<instance>.dcm
        Pass filter_uri (gs://.../filter.txt) to export only a subset."""
        store = self.store_path(store_id)
        if prefix is None:
            if not self.export_bucket:
                raise ValueError("No export_bucket set and no prefix given.")
            prefix = f"gs://{self.export_bucket}/export"
        dest = {"uriPrefix": prefix}
        if mime_type:
            dest["mimeType"] = mime_type
        body = {"gcsDestination": dest}
        if filter_uri:
            body["filterConfig"] = {"resourcePathsGcsUri": filter_uri}
        print(f"Export {store_id or self.source_store_id} -> {prefix}"
              + (f"  (filtered: {filter_uri})" if filter_uri else ""))
        op = (
            self.client.projects()
            .locations()
            .datasets()
            .dicomStores()
            .export(name=store, body=body)
            .execute()
        )
        return self._wait(op["name"]) if wait else op

    def deidentify(
        self,
        redact_all_text: bool = True,
        filter_profile: str = "DEIDENTIFY_TAG_CONTENTS",
        filter_uri: Optional[str] = None,
        source_store_id: Optional[str] = None,
        dest_store_id: Optional[str] = None,
        wait: bool = True,
    ):
        """De-identify the source store into the destination store.
        The destination store MUST already exist (Terraform creates it).
        - redact_all_text: OCR-redact burnt-in image text (REDACT_ALL_TEXT).
        - filter_profile: e.g. DEIDENTIFY_TAG_CONTENTS, KEEP_ALL_PROFILE,
          ATTRIBUTE_CONFIDENTIALITY_BASIC_PROFILE, MINIMAL_KEEP_LIST_PROFILE.
        - filter_uri: optional gs:// filter file to de-id only a subset."""
        src = self.store_path(source_store_id or self.source_store_id)
        dst_id = dest_store_id or self.deid_store_id
        if not dst_id:
            raise ValueError("No deid_store configured.")
        dst = self.store_path(dst_id)

        config = {"dicom": {"filterProfile": filter_profile}}
        if redact_all_text:
            config["image"] = {"textRedactionMode": "REDACT_ALL_TEXT"}
        body = {"destinationStore": dst, "config": config}
        if filter_uri:
            body["filterConfig"] = {"resourcePathsGcsUri": filter_uri}

        print(f"De-identify {src.split('/')[-1]} -> {dst_id} "
              f"(profile={filter_profile}, redact_text={redact_all_text})")
        op = (
            self.client.projects()
            .locations()
            .datasets()
            .dicomStores()
            .deidentify(sourceStore=src, body=body)
            .execute()
        )
        return self._wait(op["name"]) if wait else op

    def clear_store(self, store_id: Optional[str] = None, wait: bool = True):
        """Delete ALL studies in a store via DICOMweb DeleteStudy.
        NOTE: this does NOT remove the corresponding rows from BigQuery
        (streaming does not propagate deletes)."""
        store_id = store_id or self.source_store_id
        studies = self.list_studies(store_id)
        print(f"Clearing {len(studies)} study(ies) from {store_id}")
        op_names = []
        for s in studies:
            uid = s[_TAG_STUDY]["Value"][0]
            url = self._dicomweb_url(store_id, f"studies/{uid}")
            r = requests.delete(url, headers=self._headers())
            r.raise_for_status()
            body = r.json() if r.text.strip() else {}
            if isinstance(body, dict) and "name" in body:
                op_names.append(body["name"])
        if wait:
            for n in op_names:
                self._wait(n)
        print(f"  cleared {store_id}")
        return op_names

    # =================================================================
    #  INSPECTION (DICOMweb / QIDO)
    # =================================================================
    def list_studies(self, store_id: Optional[str] = None) -> list:
        r = requests.get(self._dicomweb_url(store_id, "studies"),
                         headers=self._headers())
        r.raise_for_status()
        return r.json() if r.text.strip() else []

    def list_series(self, study_uid: str, store_id: Optional[str] = None) -> list:
        r = requests.get(
            self._dicomweb_url(store_id, f"studies/{study_uid}/series"),
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json() if r.text.strip() else []

    def list_all_instances(self, store_id: Optional[str] = None) -> list:
        """All instances in the store (paginated under the hood)."""
        out, offset, page = [], 0, 500
        while True:
            url = self._dicomweb_url(store_id, f"instances?limit={page}&offset={offset}")
            r = requests.get(url, headers=self._headers())
            r.raise_for_status()
            chunk = r.json() if r.text.strip() else []
            out.extend(chunk)
            if len(chunk) < page:
                break
            offset += page
        return out

    def count_instances(self, store_id: Optional[str] = None) -> int:
        n = len(self.list_all_instances(store_id))
        print(f"{store_id or self.source_store_id}: {n} instance(s)")
        return n

    def first_instance_path(self, store_id: Optional[str] = None):
        """Return (study_uid, series_uid, instance_uid) of the first instance."""
        insts = self.list_all_instances(store_id)
        if not insts:
            raise RuntimeError(f"Store {store_id or self.source_store_id} is empty.")
        i = insts[0]
        return (
            i[_TAG_STUDY]["Value"][0],
            i[_TAG_SERIES]["Value"][0],
            i[_TAG_INSTANCE]["Value"][0],
        )

    # =================================================================
    #  VIEWING (WADO-RS rendered)
    # =================================================================
    def render(
        self,
        study: str,
        series: str,
        instance: str,
        store_id: Optional[str] = None,
        out_path: str = "image.png",
        accept: str = "image/png",
    ) -> str:
        suffix = f"studies/{study}/series/{series}/instances/{instance}/rendered"
        r = requests.get(self._dicomweb_url(store_id, suffix),
                         headers={"Authorization": f"Bearer {self._token()}",
                                  "Accept": accept})
        r.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(r.content)
        return out_path

    def render_first(
        self,
        store_id: Optional[str] = None,
        out_path: Optional[str] = None,
        show: bool = True,
    ) -> str:
        sid = store_id or self.source_store_id
        study, series, instance = self.first_instance_path(sid)
        out_path = out_path or f"{sid}_first.png"
        path = self.render(study, series, instance, sid, out_path)
        print(f"Rendered first instance of {sid} -> {path}")
        if show:
            self._show(path)
        return path

    @staticmethod
    def _show(path: str):
        try:
            from IPython.display import Image, display
            display(Image(filename=path))
        except Exception:
            pass  # not in a notebook; file is on disk

    # =================================================================
    #  FILTER FILES (for subset export / de-id)
    # =================================================================
    def build_filter_file(self, lines: list, gcs_path: Optional[str] = None) -> str:
        """Upload a filter file (list of /studies/.../series/... paths) to the
        export bucket. Returns the gs:// URI."""
        if not self.export_bucket:
            raise ValueError("export_bucket required to store filter files.")
        if gcs_path is None:
            gcs_path = f"filters/filter_{int(time.time())}.txt"
        content = "\n".join(lines) + "\n"
        self._bucket().blob(gcs_path).upload_from_string(content)
        uri = f"gs://{self.export_bucket}/{gcs_path}"
        print(f"Filter file ({len(lines)} line(s)) -> {uri}")
        return uri

    def build_filter_from_store(
        self,
        store_id: Optional[str] = None,
        level: str = "series",
        limit: Optional[int] = None,
        gcs_path: Optional[str] = None,
    ) -> str:
        """Generate a filter file from what's currently in a store.
        level: 'study' | 'series' | 'instance'. limit: keep first N lines."""
        insts = self.list_all_instances(store_id)
        seen, lines = set(), []
        for i in insts:
            st = i[_TAG_STUDY]["Value"][0]
            se = i[_TAG_SERIES]["Value"][0]
            ins = i[_TAG_INSTANCE]["Value"][0]
            if level == "study":
                key = f"/studies/{st}"
            elif level == "series":
                key = f"/studies/{st}/series/{se}"
            else:
                key = f"/studies/{st}/series/{se}/instances/{ins}"
            if key not in seen:
                seen.add(key)
                lines.append(key)
        if limit:
            lines = lines[:limit]
        return self.build_filter_file(lines, gcs_path)

    # =================================================================
    #  GCS export inspection
    # =================================================================
    def _client_storage(self):
        if self._storage is None:
            from google.cloud import storage
            self._storage = storage.Client(project=self.project_id,
                                            credentials=self.creds)
        return self._storage

    def _bucket(self):
        return self._client_storage().bucket(self.export_bucket)

    def list_export_files(self, prefix: str = "export") -> list:
        names = [b.name for b in
                 self._client_storage().list_blobs(self.export_bucket, prefix=prefix)
                 if b.name.endswith(".dcm")]
        return names

    def export_distribution(self, prefix: str = "export") -> dict:
        """Summarise how an export is laid out in GCS:
        total files, studies, series. Prints and returns a dict."""
        names = self.list_export_files(prefix)
        studies, series = set(), set()
        for n in names:
            # gs path tail: <prefix>/<study>/<series>/<instance>.dcm
            parts = n[len(prefix):].strip("/").split("/")
            if len(parts) >= 3:
                studies.add(parts[0])
                series.add(f"{parts[0]}/{parts[1]}")
        summary = {
            "prefix": prefix,
            "dcm_files": len(names),
            "studies": len(studies),
            "series": len(series),
        }
        print(f"gs://{self.export_bucket}/{prefix}  ->  "
              f"{summary['dcm_files']} .dcm file(s), "
              f"{summary['studies']} study(ies), {summary['series']} series")
        for n in names[:5]:
            print(f"   {n}")
        if len(names) > 5:
            print(f"   ... (+{len(names) - 5} more)")
        return summary

    def clear_export(self, prefix: str = "export") -> int:
        bucket = self._bucket()
        blobs = list(bucket.list_blobs(prefix=prefix))
        for b in blobs:
            b.delete()
        print(f"Deleted {len(blobs)} object(s) under gs://{self.export_bucket}/{prefix}")
        return len(blobs)

    # =================================================================
    #  Convenience
    # =================================================================
    def summary(self):
        print("=" * 50)
        print(f"project : {self.project_id}")
        print(f"location: {self.location}")
        print(f"dataset : {self.dataset_id}")
        for sid in filter(None, [self.source_store_id, self.deid_store_id]):
            try:
                n = len(self.list_all_instances(sid))
            except Exception as e:
                n = f"error: {e}"
            print(f"store   : {sid:<20} {n} instance(s)")
        print("=" * 50)

    # =================================================================
    #  INDEX / PATH HELPERS  (add inside class DicomLab)
    # =================================================================

    def get_path(self, index, store_id=None):
        """Return the (study, series, instance) UID triple for the instance
        at `index` in a store."""
        sid = store_id or self.source_store_id
        insts = self.list_all_instances(sid)
        if index < 0 or index >= len(insts):
            raise IndexError(
                f"index {index} out of range ({sid} has {len(insts)} instances)"
            )
        i = insts[index]
        return (
            i[_TAG_STUDY]["Value"][0],
            i[_TAG_SERIES]["Value"][0],
            i[_TAG_INSTANCE]["Value"][0],
        )

    def render_path(self, path, store_id=None, out_path=None, show=True):
        """Render an instance given its (study, series, instance) UID triple,
        from any store. `path` is the 3-tuple returned by get_path()."""
        sid = store_id or self.source_store_id
        study, series, instance = path
        out_path = out_path or f"{sid}_{instance[-12:]}.png"
        out = self.render(study, series, instance, sid, out_path)
        print(f"Rendered {sid}: .../{instance[-20:]} -> {out}")
        if show:
            self._show(out)
        return out

    def render_index(self, index, store_id=None, out_path=None, show=True):
        """Render the instance at `index` in a store. Plug in 0, 1, 2, ..."""
        sid = store_id or self.source_store_id
        path = self.get_path(index, sid)
        out_path = out_path or f"{sid}_{index}.png"
        return self.render_path(path, sid, out_path, show)