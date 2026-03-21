import streamlit as st
import pandas as pd
from snowflake.snowpark.context import get_active_session

# -----------------------------------------------------------------------------
# Page config
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Snowflake Object Catalog",
    layout="wide"
)

st.title("Snowflake Object Catalog")
st.caption("Explore databases, tables, views, stored procedures, row counts, and storage usage in Snowflake.")

session = get_active_session()

# -----------------------------------------------------------------------------
# Styling
# -----------------------------------------------------------------------------
st.markdown("""
<style>
.block-container {
    padding-top: 1.2rem;
    padding-bottom: 2rem;
    max-width: 1500px;
}
.metric-card {
    background: white;
    border: 1px solid #E5E7EB;
    border-radius: 16px;
    padding: 16px 18px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.05);
}
.metric-label {
    font-size: 0.9rem;
    color: #6B7280;
    margin-bottom: 0.3rem;
}
.metric-value {
    font-size: 1.6rem;
    font-weight: 700;
    color: #111827;
}
.section-title {
    font-size: 1.1rem;
    font-weight: 700;
    margin: 1rem 0 0.5rem 0;
}
.small-note {
    color: #6B7280;
    font-size: 0.85rem;
}
div[data-testid="stDataFrame"] {
    border: 1px solid #E5E7EB;
    border-radius: 12px;
    overflow: hidden;
}
</style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def quote_ident(name: str) -> str:
    """Safely quote Snowflake identifiers."""
    if name is None:
        return '""'
    return '"' + str(name).replace('"', '""') + '"'

def format_bytes(num_bytes):
    if pd.isna(num_bytes):
        return None
    num_bytes = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:,.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:,.1f} EB"

@st.cache_data(ttl=1800, show_spinner=False)
def load_table_view_metadata():
    query = """
    SELECT
        t.TABLE_ID,
        t.TABLE_CATALOG,
        t.CREATED,
        t.TABLE_NAME,
        t.TABLE_SCHEMA,
        t.TABLE_OWNER,
        t.TABLE_TYPE,
        t.IS_TRANSIENT,
        t.CLUSTERING_KEY,
        t.ROW_COUNT,
        t.BYTES,
        t.RETENTION_TIME,
        t.LAST_ALTERED,
        t.AUTO_CLUSTERING_ON,
        t.COMMENT,
        c.column_count
    FROM SNOWFLAKE.ACCOUNT_USAGE.TABLES t
    LEFT JOIN (
        SELECT
            table_id,
            COUNT(DISTINCT column_id) AS column_count
        FROM SNOWFLAKE.ACCOUNT_USAGE.COLUMNS
        GROUP BY table_id
    ) c
        ON c.table_id = t.table_id
    WHERE t.TABLE_SCHEMA NOT LIKE '%ANON_HOL%'
      AND t.DELETED IS NULL
    """
    df = session.sql(query).to_pandas()

    if df.empty:
        return df

    df.columns = [c.upper() for c in df.columns]
    df["CREATED"] = pd.to_datetime(df["CREATED"], errors="coerce")
    df["LAST_ALTERED"] = pd.to_datetime(df["LAST_ALTERED"], errors="coerce")
    df["ROW_COUNT"] = pd.to_numeric(df["ROW_COUNT"], errors="coerce").fillna(0)
    df["BYTES"] = pd.to_numeric(df["BYTES"], errors="coerce").fillna(0)
    df["COLUMN_COUNT"] = pd.to_numeric(df["COLUMN_COUNT"], errors="coerce").fillna(0)
    return df

@st.cache_data(ttl=3600, show_spinner=False)
def load_databases():
    df = session.sql("SHOW DATABASES").to_pandas()
    if df.empty:
        return df
    df.columns = [c.upper() for c in df.columns]
    return df

@st.cache_data(ttl=3600, show_spinner=False)
def load_procedures():
    """
    Pull stored procedure metadata from each database's INFORMATION_SCHEMA.PROCEDURES.
    This avoids hardcoding databases and keeps the app account-wide.
    """
    dbs = load_databases()
    if dbs.empty or "NAME" not in dbs.columns:
        return pd.DataFrame()

    frames = []

    for db_name in dbs["NAME"].dropna().tolist():
        safe_db = quote_ident(db_name)
        proc_query = f"""
        SELECT
            PROCEDURE_CATALOG,
            PROCEDURE_SCHEMA,
            PROCEDURE_NAME,
            PROCEDURE_OWNER,
            CREATED,
            LAST_ALTERED,
            PROCEDURE_LANGUAGE,
            ARGUMENT_SIGNATURE,
            DATA_TYPE AS RETURNS
        FROM {safe_db}.INFORMATION_SCHEMA.PROCEDURES
        """
        try:
            part = session.sql(proc_query).to_pandas()
            if not part.empty:
                part.columns = [c.upper() for c in part.columns]
                frames.append(part)
        except Exception:
            # Some databases may be inaccessible to the current role.
            continue

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df["CREATED"] = pd.to_datetime(df["CREATED"], errors="coerce")
    df["LAST_ALTERED"] = pd.to_datetime(df["LAST_ALTERED"], errors="coerce")
    return df

def metric_card(label, value):
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
        </div>
        """,
        unsafe_allow_html=True
    )

# -----------------------------------------------------------------------------
# Data loading
# -----------------------------------------------------------------------------
with st.spinner("Loading Snowflake metadata..."):
    obj_df = load_table_view_metadata()
    proc_df = load_procedures()

if obj_df.empty:
    st.warning("No table/view metadata was returned. Check privileges on SNOWFLAKE.ACCOUNT_USAGE.TABLES and COLUMNS.")
    st.stop()

# -----------------------------------------------------------------------------
# Sidebar filters
# -----------------------------------------------------------------------------
st.sidebar.header("Filters")

all_dbs = sorted(obj_df["TABLE_CATALOG"].dropna().unique().tolist())
selected_dbs = st.sidebar.multiselect("Database", all_dbs, default=all_dbs)

filtered_df = obj_df[obj_df["TABLE_CATALOG"].isin(selected_dbs)].copy()

all_schemas = sorted(filtered_df["TABLE_SCHEMA"].dropna().unique().tolist())
selected_schemas = st.sidebar.multiselect("Schema", all_schemas, default=all_schemas)

filtered_df = filtered_df[filtered_df["TABLE_SCHEMA"].isin(selected_schemas)].copy()

object_types = sorted(filtered_df["TABLE_TYPE"].dropna().unique().tolist())
selected_types = st.sidebar.multiselect("Object Type", object_types, default=object_types)

filtered_df = filtered_df[filtered_df["TABLE_TYPE"].isin(selected_types)].copy()

search_text = st.sidebar.text_input("Search table/view name")

if search_text:
    filtered_df = filtered_df[
        filtered_df["TABLE_NAME"].str.contains(search_text, case=False, na=False)
    ].copy()

show_only_transient = st.sidebar.checkbox("Only transient objects", value=False)
if show_only_transient:
    filtered_df = filtered_df[filtered_df["IS_TRANSIENT"] == "YES"].copy()

# Procedure filtering
filtered_proc_df = proc_df.copy()
if not filtered_proc_df.empty:
    if selected_dbs:
        filtered_proc_df = filtered_proc_df[
            filtered_proc_df["PROCEDURE_CATALOG"].isin(selected_dbs)
        ].copy()

    if selected_schemas:
        filtered_proc_df = filtered_proc_df[
            filtered_proc_df["PROCEDURE_SCHEMA"].isin(selected_schemas)
        ].copy()

# -----------------------------------------------------------------------------
# KPI area
# -----------------------------------------------------------------------------
tables_count = int((filtered_df["TABLE_TYPE"] == "BASE TABLE").sum()) if "BASE TABLE" in filtered_df["TABLE_TYPE"].values else int((filtered_df["TABLE_TYPE"].str.contains("TABLE", case=False, na=False)).sum())
views_count = int(filtered_df["TABLE_TYPE"].str.contains("VIEW", case=False, na=False).sum())
db_count = int(filtered_df["TABLE_CATALOG"].nunique())
schema_count = int(filtered_df["TABLE_SCHEMA"].nunique())
proc_count = int(len(filtered_proc_df)) if not filtered_proc_df.empty else 0
total_rows = float(filtered_df["ROW_COUNT"].sum())
total_bytes = float(filtered_df["BYTES"].sum())

c1, c2, c3, c4, c5, c6 = st.columns(6)
with c1:
    metric_card("Databases", f"{db_count:,}")
with c2:
    metric_card("Schemas", f"{schema_count:,}")
with c3:
    metric_card("Tables", f"{tables_count:,}")
with c4:
    metric_card("Views", f"{views_count:,}")
with c5:
    metric_card("Stored Procedures", f"{proc_count:,}")
with c6:
    metric_card("Storage", format_bytes(total_bytes))

st.markdown("<div class='small-note'>Rows shown below are metadata-based counts from Snowflake system views.</div>", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# Tabs
# -----------------------------------------------------------------------------
tab1, tab2, tab3, tab4 = st.tabs([
    "Overview",
    "Databases & Schemas",
    "Tables & Views",
    "Stored Procedures"
])

# -----------------------------------------------------------------------------
# Tab 1 - Overview
# -----------------------------------------------------------------------------
with tab1:
    st.subheader("Overview by Database")

    db_summary = (
        filtered_df.groupby("TABLE_CATALOG", dropna=False)
        .agg(
            schemas=("TABLE_SCHEMA", "nunique"),
            objects=("TABLE_NAME", "count"),
            tables=("TABLE_TYPE", lambda s: int(s.astype(str).str.contains("TABLE", case=False, na=False).sum())),
            views=("TABLE_TYPE", lambda s: int(s.astype(str).str.contains("VIEW", case=False, na=False).sum())),
            rows=("ROW_COUNT", "sum"),
            bytes=("BYTES", "sum"),
            columns=("COLUMN_COUNT", "sum")
        )
        .reset_index()
        .rename(columns={"TABLE_CATALOG": "DATABASE"})
    )

    if not filtered_proc_df.empty:
        proc_summary = (
            filtered_proc_df.groupby("PROCEDURE_CATALOG")
            .size()
            .reset_index(name="stored_procedures")
            .rename(columns={"PROCEDURE_CATALOG": "DATABASE"})
        )
        db_summary = db_summary.merge(proc_summary, on="DATABASE", how="left")
    else:
        db_summary["stored_procedures"] = 0

    db_summary["stored_procedures"] = db_summary["stored_procedures"].fillna(0).astype(int)
    db_summary["rows"] = db_summary["rows"].fillna(0).astype("int64", errors="ignore")
    db_summary["size"] = db_summary["bytes"].apply(format_bytes)

    show_db_summary = db_summary[[
        "DATABASE", "schemas", "objects", "tables", "views",
        "stored_procedures", "rows", "columns", "size"
    ]].sort_values(["objects", "rows"], ascending=[False, False])

    st.dataframe(show_db_summary, use_container_width=True, hide_index=True)

# -----------------------------------------------------------------------------
# Tab 2 - Databases & Schemas
# -----------------------------------------------------------------------------
with tab2:
    st.subheader("Schema-Level Breakdown")

    schema_summary = (
        filtered_df.groupby(["TABLE_CATALOG", "TABLE_SCHEMA"], dropna=False)
        .agg(
            objects=("TABLE_NAME", "count"),
            tables=("TABLE_TYPE", lambda s: int(s.astype(str).str.contains("TABLE", case=False, na=False).sum())),
            views=("TABLE_TYPE", lambda s: int(s.astype(str).str.contains("VIEW", case=False, na=False).sum())),
            rows=("ROW_COUNT", "sum"),
            bytes=("BYTES", "sum"),
            columns=("COLUMN_COUNT", "sum"),
            last_altered=("LAST_ALTERED", "max")
        )
        .reset_index()
        .rename(columns={
            "TABLE_CATALOG": "DATABASE",
            "TABLE_SCHEMA": "SCHEMA"
        })
    )

    if not filtered_proc_df.empty:
        proc_schema_summary = (
            filtered_proc_df.groupby(["PROCEDURE_CATALOG", "PROCEDURE_SCHEMA"])
            .size()
            .reset_index(name="stored_procedures")
            .rename(columns={
                "PROCEDURE_CATALOG": "DATABASE",
                "PROCEDURE_SCHEMA": "SCHEMA"
            })
        )
        schema_summary = schema_summary.merge(
            proc_schema_summary,
            on=["DATABASE", "SCHEMA"],
            how="left"
        )
    else:
        schema_summary["stored_procedures"] = 0

    schema_summary["stored_procedures"] = schema_summary["stored_procedures"].fillna(0).astype(int)
    schema_summary["size"] = schema_summary["bytes"].apply(format_bytes)

    st.dataframe(
        schema_summary[[
            "DATABASE", "SCHEMA", "objects", "tables", "views",
            "stored_procedures", "rows", "columns", "size", "last_altered"
        ]].sort_values(["DATABASE", "objects"], ascending=[True, False]),
        use_container_width=True,
        hide_index=True
    )

# -----------------------------------------------------------------------------
# Tab 3 - Tables & Views
# -----------------------------------------------------------------------------
with tab3:
    st.subheader("Tables and Views")

    detail_df = filtered_df.copy()
    detail_df["SIZE"] = detail_df["BYTES"].apply(format_bytes)

    st.dataframe(
        detail_df[[
            "TABLE_CATALOG",
            "TABLE_SCHEMA",
            "TABLE_NAME",
            "TABLE_TYPE",
            "TABLE_OWNER",
            "ROW_COUNT",
            "SIZE",
            "COLUMN_COUNT",
            "IS_TRANSIENT",
            "RETENTION_TIME",
            "AUTO_CLUSTERING_ON",
            "LAST_ALTERED",
            "COMMENT"
        ]].rename(columns={
            "TABLE_CATALOG": "DATABASE",
            "TABLE_SCHEMA": "SCHEMA",
            "TABLE_NAME": "OBJECT_NAME",
            "TABLE_TYPE": "OBJECT_TYPE",
            "TABLE_OWNER": "OWNER",
            "ROW_COUNT": "ROWS",
            "COLUMN_COUNT": "COLUMNS"
        }).sort_values(["DATABASE", "SCHEMA", "OBJECT_TYPE", "OBJECT_NAME"]),
        use_container_width=True,
        hide_index=True
    )

# -----------------------------------------------------------------------------
# Tab 4 - Stored Procedures
# -----------------------------------------------------------------------------
with tab4:
    st.subheader("Stored Procedures")

    if filtered_proc_df.empty:
        st.info("No stored procedure metadata returned for the selected scope or current role.")
    else:
        proc_search = st.text_input("Search procedure name", key="proc_search")
        proc_display = filtered_proc_df.copy()

        if proc_search:
            proc_display = proc_display[
                proc_display["PROCEDURE_NAME"].str.contains(proc_search, case=False, na=False)
            ].copy()

        st.dataframe(
            proc_display[[
                "PROCEDURE_CATALOG",
                "PROCEDURE_SCHEMA",
                "PROCEDURE_NAME",
                "ARGUMENT_SIGNATURE",
                "RETURNS",
                "PROCEDURE_LANGUAGE",
                "PROCEDURE_OWNER",
                "CREATED",
                "LAST_ALTERED"
            ]].rename(columns={
                "PROCEDURE_CATALOG": "DATABASE",
                "PROCEDURE_SCHEMA": "SCHEMA",
                "PROCEDURE_NAME": "PROCEDURE",
                "PROCEDURE_OWNER": "OWNER",
                "PROCEDURE_LANGUAGE": "LANGUAGE"
            }).sort_values(["DATABASE", "SCHEMA", "PROCEDURE"]),
            use_container_width=True,
            hide_index=True
        )

# -----------------------------------------------------------------------------
# Footer notes
# -----------------------------------------------------------------------------
st.markdown("---")
st.markdown(
    """
    <div class='small-note'>
    Notes:<br>
    • Table and view metadata comes from SNOWFLAKE.ACCOUNT_USAGE.<br>
    • Stored procedure metadata comes from each database's INFORMATION_SCHEMA.PROCEDURES.<br>
    • ACCOUNT_USAGE can lag behind live metadata, so very recent object changes may not appear immediately.
    </div>
    """,
    unsafe_allow_html=True
)
