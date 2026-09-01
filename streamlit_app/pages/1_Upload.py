"""Upload tab — validate, hash and land a LinkedIn export in MinIO (§7, §9).

This tab deliberately does **not** trigger ingestion: all three trigger modes
are centralised in the Job Management tab.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st  # noqa: E402

from common.csv_schema import KNOWN_COLUMNS, REQUIRED_COLUMNS  # noqa: E402
from common.errors import ConnectionLensError, CsvSchemaError  # noqa: E402
from streamlit_app import db  # noqa: E402
from streamlit_app.auth import require_login  # noqa: E402
from streamlit_app.theme import page_header, render_status_pill, section  # noqa: E402
from streamlit_app.ui import (  # noqa: E402
    configure_page,
    format_timestamp,
    landing_zone_client,
    minio_status,
    render_sidebar_footer,
)
from streamlit_app.upload_service import perform_upload, prepare_upload  # noqa: E402

configure_page("Upload")
require_login()

page_header(
    "Upload a LinkedIn export",
    # page_header escapes its arguments, so this is plain text on purpose.
    "Get your file from LinkedIn → Settings → Get a copy of your data → "
    "Connections. Nothing here is scraped, and nothing leaves this machine.",
)

landing_zone = minio_status()
if not landing_zone.reachable:
    st.error(f"Landing zone unavailable — {landing_zone.detail}", icon="🚫")
    st.stop()
if landing_zone.bucket_exists:
    render_status_pill("Landing zone reachable", "ok")
    st.caption(landing_zone.detail)
else:
    # Uploading still works: the client creates the bucket on first write.
    render_status_pill("Bucket not created yet", "warn")
    st.caption(landing_zone.detail)

# Uploading is a MinIO operation and works throughout an ingestion run; only
# the duplicate check needs Bronze, which is locked while the DAG writes. Say
# so rather than letting a known file be announced as new.
warehouse_busy = db.warehouse_status()["busy"]
if warehouse_busy:
    st.warning(
        "**Ingestion is running**, so the warehouse cannot be read to check "
        "for duplicates. Uploading still works, and the DAG checks the content "
        "hash again before it ingests anything.",
        icon="⏳",
    )

with st.container(border=True):
    uploaded = st.file_uploader(
        "Connections.csv",
        type=["csv"],
        help=f"Required columns: {', '.join(REQUIRED_COLUMNS)}",
    )

if uploaded is None:
    st.info(
        "Drop your `Connections.csv` above. The file is validated and hashed "
        "**before** anything is uploaded.",
        icon="⬆️",
    )
else:
    raw = uploaded.getvalue()

    # --- Layer 1: validation, before hashing or uploading (§7, §14 #6) -----
    try:
        prepared = prepare_upload(raw)
    except CsvSchemaError as error:
        st.error(f"{error}", icon="🚫")
        st.caption(
            "Nothing was hashed or uploaded. The known export schema is: "
            + ", ".join(KNOWN_COLUMNS)
        )
        st.stop()

    st.success(
        f"Validated **{prepared.row_count:,} rows** · header row found on line "
        f"{prepared.parsed.header_line_index + 1} "
        f"({len(prepared.parsed.note_lines)} note line(s) skipped) · "
        f"encoding `{prepared.parsed.encoding}`",
        icon="✅",
    )

    detail_columns = st.columns(2, gap="medium")
    detail_columns[0].metric(
        "Rows", f"{prepared.row_count:,}", icon=":material/table_rows:", border=True
    )
    detail_columns[1].metric(
        "Content hash (MD5)",
        prepared.file_hash[:12] + "…",
        icon=":material/fingerprint:",
        border=True,
        help="Duplicate detection compares this hash against Bronze.",
    )

    is_duplicate = not warehouse_busy and db.is_hash_in_bronze(prepared.file_hash)
    if warehouse_busy:
        st.info(
            "Whether this content is already in Bronze cannot be checked until "
            "the run finishes.",
            icon="⏳",
        )
    elif is_duplicate:
        st.warning(
            "**Duplicate content.** This exact file is already in Bronze, so no "
            "new dataset will be created. It will still be uploaded to MinIO — "
            "the landing zone keeps the full upload audit trail.",
            icon="♻️",
        )
    else:
        st.info("New content — this will become a new snapshot once ingested.", icon="🆕")

    with st.expander("Preview (contains personal data)", icon=":material/visibility:"):
        st.dataframe(prepared.parsed.frame.head(5), width="stretch")

    if st.button(
        "Upload to landing zone", type="primary", icon=":material/cloud_upload:"
    ):
        try:
            result = perform_upload(
                prepared, landing_zone_client(), db.is_hash_in_bronze
            )
        except ConnectionLensError as error:
            st.error(f"Upload failed: {error}", icon="🚫")
        else:
            st.success(result.message, icon="📦")
            st.code(result.object_key, language="text")
            st.caption(
                "Uploading does not start ingestion — trigger the DAG from "
                "**Job Management**."
            )
            db.clear_caches()

st.divider()

section(
    "Landing zone objects",
    "Every upload is kept, duplicates included — the landing zone is the "
    "audit trail. Nothing here is ever removed automatically.",
)
try:
    objects = landing_zone_client().list_landing_objects()
except ConnectionLensError as error:
    st.warning(f"Could not list objects: {error}")
    objects = []

ingested_hashes = db.bronze_file_hashes()
short_ingested = {value[:8] for value in ingested_hashes}
if warehouse_busy:
    # With Bronze unreadable every object reads as "never ingested", which is
    # the safe direction to be wrong in — it over-warns before a deletion
    # rather than under-warning — but it still has to be said out loud.
    st.caption(
        "Ingestion is running, so the “in Bronze” column below cannot be "
        "trusted until it finishes."
    )

if not objects:
    st.caption("No exports uploaded yet.")
else:
    st.dataframe(
        [
            {
                "Object": obj.key.rsplit("/", 1)[-1],
                "Snapshot": format_timestamp(obj.snapshot_ts),
                "Size (KB)": round(obj.size_bytes / 1024, 1),
                "In Bronze": "✅" if obj.hash8 in short_ingested else "⏳ pending",
            }
            for obj in reversed(objects)
        ],
        width="stretch",
        hide_index=True,
    )
    pending = [obj for obj in objects if obj.hash8 not in short_ingested]
    if pending:
        st.caption(
            f"{len(pending)} object(s) waiting for the next DAG run — "
            "trigger it from **Job Management**."
        )

    with st.expander(
        "Delete an object from the landing zone", icon=":material/delete:"
    ):
        st.caption(
            "Deleting is permanent: every version of the object goes. Bronze "
            "is never touched."
        )
        labels = {
            f"{obj.key.rsplit('/', 1)[-1]}  ·  {format_timestamp(obj.snapshot_ts)}"
            f"  ·  {'in Bronze' if obj.hash8 in short_ingested else 'never ingested'}": obj
            for obj in reversed(objects)
        }
        chosen_label = st.selectbox("Object", list(labels), key="delete_target")
        chosen = labels[chosen_label]
        already_ingested = chosen.hash8 in short_ingested

        if already_ingested:
            st.info(
                "Its rows are already in Bronze. Deleting removes only the "
                "landing-zone copy — the warehouse keeps the data, and the "
                "ingestion DAG will not re-read this file.",
                icon="✅",
            )
        else:
            st.warning(
                "**This object has never been ingested.** Deleting it loses "
                "that export permanently — there is no copy anywhere else.",
                icon="⚠️",
            )

        st.code(chosen.key, language="text")
        confirmed = st.checkbox(
            "I understand this cannot be undone", key="delete_confirmed"
        )
        if st.button(
            "Delete permanently",
            type="primary",
            icon=":material/delete_forever:",
            disabled=not confirmed,
        ):
            try:
                removed = landing_zone_client().delete_object(chosen.key)
            except ConnectionLensError as error:
                st.error(f"Delete failed: {error}", icon="🚫")
            else:
                st.success(
                    f"Deleted {chosen.key} ({removed} version(s)).", icon="🗑️"
                )
                db.clear_caches()
                st.rerun()

section(
    "Ingested snapshots (Bronze)",
    "Append-only. One batch per genuinely new export, written by the Airflow "
    "DAG — never by this app.",
)
ingestion_log = db.load_ingestion_log()
if ingestion_log.empty:
    st.caption("Bronze is empty — no export has been ingested yet.")
else:
    st.dataframe(
        ingestion_log.assign(
            snapshot_ts=ingestion_log["snapshot_ts"].map(format_timestamp),
            ingested_at=ingestion_log["ingested_at"].map(format_timestamp),
            file_hash=ingestion_log["file_hash"].str.slice(0, 12) + "…",
        ),
        width="stretch",
        hide_index=True,
    )

render_sidebar_footer()
