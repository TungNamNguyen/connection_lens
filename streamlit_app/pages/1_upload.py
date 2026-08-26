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
st.title("📤 Upload a LinkedIn export")
st.caption(
    "Get your file from LinkedIn → Settings → **Get a copy of your data** → "
    "*Connections*. Nothing here is scraped, and nothing leaves this machine."
)

landing_zone = minio_status()
if not landing_zone.reachable:
    st.error(f"Landing zone unavailable — {landing_zone.detail}", icon="🚫")
    st.stop()
if landing_zone.bucket_exists:
    st.caption(f"Landing zone: {landing_zone.detail}")
else:
    # Uploading still works: the client creates the bucket on first write.
    st.warning(landing_zone.detail, icon="🪣")

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

    detail_columns = st.columns(2)
    detail_columns[0].metric("Rows", f"{prepared.row_count:,}")
    detail_columns[1].metric("Content hash (MD5)", prepared.file_hash[:12] + "…")

    is_duplicate = db.is_hash_in_bronze(prepared.file_hash)
    if is_duplicate:
        st.warning(
            "**Duplicate content.** This exact file is already in Bronze, so no "
            "new dataset will be created. It will still be uploaded to MinIO — "
            "the landing zone keeps the full upload audit trail.",
            icon="♻️",
        )
    else:
        st.info("New content — this will become a new snapshot once ingested.", icon="🆕")

    with st.expander("Preview (contains personal data)"):
        st.dataframe(prepared.parsed.frame.head(5), width="stretch")

    if st.button("Upload to landing zone", type="primary"):
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
                "This tab does not start ingestion. Open **Job Management** to "
                "trigger the DAG, or let the MinIO bucket event do it if the "
                "listener service is running."
            )
            db.clear_caches()

st.divider()

st.subheader("Landing zone")
try:
    objects = landing_zone_client().list_landing_objects()
except ConnectionLensError as error:
    st.warning(f"Could not list objects: {error}")
    objects = []

ingested_hashes = db.bronze_file_hashes()
short_ingested = {value[:8] for value in ingested_hashes}

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

st.subheader("Ingested snapshots (Bronze)")
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
