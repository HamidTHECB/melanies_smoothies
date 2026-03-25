import streamlit as st
import pandas as pd
from snowflake.snowpark.context import get_active_session

# -----------------------------------------------------------------------------
# Page setup
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Snowflake Metadata Catalog",
    layout="wide"
)

st.title("Snowflake Metadata Catalog")
st.caption("Explore databases, tables, views, procedures, functions, row counts, and data size.")

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
    padding: 18px 18px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.05);
    min-height: 110px;
}

.metric-label {
    font-size: 0.95rem;
    color: #6B7280;
    margin-bottom: 0.45rem;
    font-weight: 600;
    letter-spacing: 0.02em;
}

.metric-value {
    font-size: 2rem;
    font-weight: 800;
    color: #111827;
}

.section-subtle {
    color: #6B7280;
    font-size: 0.88rem;
    margin-top: 0.25rem;
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
def format_bytes(num_bytes):
    if pd.isna(num_bytes):
        return None
    num_bytes = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:,.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:,.1f} EB"

def metric_card(label, value, subtitle=""):
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
            <div class="section-subtle">{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True
    )

def ensure_columns(df: pd.DataFrame, required_cols: list[str]) -> pd.DataFrame:
    for col in required_cols:
        if col not in df.columns:
            df[col] = None
    return df[required_cols]

# -----------------------------------------------------------------------------
# Data loading functions
# -----------------------------------------------------------------------------
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
        t.CLUSTERING_KEY,
        t.ROW_COUNT,
        t.BYTES,
        t.RETENTION_TIME,
        t.LAST_ALTERED,
        t.AUTO_CLUSTERING_ON,
        t.COMMENT,
        c.COLUMN_COUNT
    FROM SNOWFLAKE.ACCOUNT_USAGE.TABLES t
    LEFT JOIN (
        SELECT
            TABLE_ID,
            COUNT(DISTINCT COLUMN_ID) AS COLUMN_COUNT
        FROM SNOWFLAKE.ACCOUNT_USAGE.COLUMNS
        GROUP BY TABLE_ID
    ) c
        ON c.TABLE_ID = t.TABLE_ID
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

@st.cache_data(ttl=1800, show_spinner=False)
def load_procedures():
    required_cols = [
        "PROCEDURE_CATALOG",
        "PROCEDURE_SCHEMA",
        "PROCEDURE_NAME",
        "PROCEDURE_OWNER",
        "CREATED",
        "LAST_ALTERED",
        "PROCEDURE_LANGUAGE",
        "ARGUMENT_SIGNATURE",
        "RETURNS",
        "COMMENT"
    ]

    # Try ACCOUNT_USAGE first
    try:
        query = """
        SELECT
            PROCEDURE_CATALOG,
            PROCEDURE_SCHEMA,
            PROCEDURE_NAME,
            PROCEDURE_OWNER,
            CREATED,
            LAST_ALTERED,
            PROCEDURE_LANGUAGE,
            ARGUMENT_SIGNATURE,
            DATA_TYPE AS RETURNS,
            COMMENT
        FROM SNOWFLAKE.ACCOUNT_USAGE.PROCEDURES
        WHERE DELETED IS NULL
        """
        df = session.sql(query).to_pandas()
        if not df.empty:
            df.columns = [c.upper() for c in df.columns]
            df["CREATED"] = pd.to_datetime(df["CREATED"], errors="coerce")
            df["LAST_ALTERED"] = pd.to_datetime(df["LAST_ALTERED"], errors="coerce")
            return ensure_columns(df, required_cols)
    except Exception:
        pass

    # Fallback to SHOW USER PROCEDURES IN ACCOUNT
    try:
        df = session.sql("SHOW USER PROCEDURES IN ACCOUNT").to_pandas()
        if df.empty:
            return pd.DataFrame(columns=required_cols)

        df.columns = [c.upper() for c in df.columns]

        rename_map = {
            "CATALOG_NAME": "PROCEDURE_CATALOG",
            "SCHEMA_NAME": "PROCEDURE_SCHEMA",
            "NAME": "PROCEDURE_NAME",
            "OWNER": "PROCEDURE_OWNER",
            "DESCRIPTION": "COMMENT",
            "LANGUAGE": "PROCEDURE_LANGUAGE",
            "RETURN_TYPE": "RETURNS",
            "ARGUMENTS": "ARGUMENT_SIGNATURE"
        }
        df = df.rename(columns=rename_map)

        if "CREATED_ON" in df.columns:
            df["CREATED"] = pd.to_datetime(df["CREATED_ON"], errors="coerce")
        else:
            df["CREATED"] = pd.NaT

        df["LAST_ALTERED"] = pd.NaT
        return ensure_columns(df, required_cols)
    except Exception:
        return pd.DataFrame(columns=required_cols)

@st.cache_data(ttl=1800, show_spinner=False)
def load_functions():
    required_cols = [
        "FUNCTION_CATALOG",
        "FUNCTION_SCHEMA",
        "FUNCTION_NAME",
        "FUNCTION_OWNER",
        "CREATED",
        "LAST_ALTERED",
        "FUNCTION_LANGUAGE",
        "ARGUMENT_SIGNATURE",
        "RETURNS",
        "COMMENT"
    ]

    # Try ACCOUNT_USAGE first
    try:
        query = """
        SELECT
            FUNCTION_CATALOG,
            FUNCTION_SCHEMA,
            FUNCTION_NAME,
            FUNCTION_OWNER,
            CREATED,
            LAST_ALTERED,
            FUNCTION_LANGUAGE,
            ARGUMENT_SIGNATURE,
            DATA_TYPE AS RETURNS,
            COMMENT
        FROM SNOWFLAKE.ACCOUNT_USAGE.FUNCTIONS
        WHERE DELETED IS NULL
        """
        df = session.sql(query).to_pandas()
        if not df.empty:
            df.columns = [c.upper() for c in df.columns]
            df["CREATED"] = pd.to_datetime(df["CREATED"], errors="coerce")
            df["LAST_ALTERED"] = pd.to_datetime(df["LAST_ALTERED"], errors="coerce")
            return ensure_columns(df, required_cols)
    except Exception:
        pass

    # Fallback to SHOW USER FUNCTIONS IN ACCOUNT
    try:
        df = session.sql("SHOW USER FUNCTIONS IN ACCOUNT").to_pandas()
        if df.empty:
            return pd.DataFrame(columns=required_cols)

        df.columns = [c.upper() for c in df.columns]

        rename_map = {
            "CATALOG_NAME": "FUNCTION_CATALOG",
            "SCHEMA_NAME": "FUNCTION_SCHEMA",
            "NAME": "FUNCTION_NAME",
            "OWNER": "FUNCTION_OWNER",
            "DESCRIPTION": "COMMENT",
            "LANGUAGE": "FUNCTION_LANGUAGE",
            "RETURN_TYPE": "RETURNS",
            "ARGUMENTS": "ARGUMENT_SIGNATURE"
        }
        df = df.rename(columns=rename_map)

        if "CREATED_ON" in df.columns:
            df["CREATED"] = pd.to_datetime(df["CREATED_ON"], errors="coerce")
        else:
            df["CREATED"] = pd.NaT

        df["LAST_ALTERED"] = pd.NaT
        return ensure_columns(df, required_cols)
    except Exception:
        return pd.DataFrame(columns=required_cols)

# -----------------------------------------------------------------------------
# Load metadata
# -----------------------------------------------------------------------------
with st.spinner("Loading Snowflake metadata..."):
    obj_df = load_table_view_metadata()
    proc_df = load_procedures()
    func_df = load_functions()

if obj_df.empty:
    st.warning("No metadata returned. Check access to SNOWFLAKE.ACCOUNT_USAGE views.")
    st.stop()

# -----------------------------------------------------------------------------
# Sidebar filters
# -----------------------------------------------------------------------------
st.sidebar.header("Filters")

all_dbs = sorted(obj_df["TABLE_CATALOG"].dropna().unique().tolist())

selected_dbs = st.sidebar.multiselect(
    "Database",
    options=["All"] + all_dbs,
    default=["All"]
)

if "All" in selected_dbs or not selected_dbs:
    selected_dbs = all_dbs

filtered_df = obj_df[obj_df["TABLE_CATALOG"].isin(selected_dbs)].copy()

search_text = st.sidebar.text_input("Search tables/views")
if search_text:
    filtered_df = filtered_df[
        filtered_df["TABLE_NAME"].str.contains(search_text, case=False, na=False)
    ].copy()

filtered_proc_df = proc_df.copy()
if not filtered_proc_df.empty:
    filtered_proc_df = filtered_proc_df[
        filtered_proc_df["PROCEDURE_CATALOG"].isin(selected_dbs)
    ].copy()

filtered_func_df = func_df.copy()
if not filtered_func_df.empty:
    filtered_func_df = filtered_func_df[
        filtered_func_df["FUNCTION_CATALOG"].isin(selected_dbs)
    ].copy()

# -----------------------------------------------------------------------------
# KPI card metrics
# -----------------------------------------------------------------------------
tables_count = int(
    filtered_df["TABLE_TYPE"].astype(str).str.contains("TABLE", case=False, na=False).sum()
)

views_count = int(
    filtered_df["TABLE_TYPE"].astype(str).str.contains("VIEW", case=False, na=False).sum()
)

procedures_count = int(len(filtered_proc_df)) if not filtered_proc_df.empty else 0
functions_count = int(len(filtered_func_df)) if not filtered_func_df.empty else 0

total_rows = int(filtered_df["ROW_COUNT"].sum()) if not filtered_df.empty else 0
total_bytes = float(filtered_df["BYTES"].sum()) if not filtered_df.empty else 0

# -----------------------------------------------------------------------------
# KPI cards
# -----------------------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)

with c1:
    metric_card("TABLES", f"{tables_count:,}", "Selected tables")

with c2:
    metric_card("VIEWS", f"{views_count:,}", "Selected views")

with c3:
    metric_card("PROCEDURES", f"{procedures_count:,}", "Stored procedures in selected scope")

with c4:
    metric_card("FUNCTIONS", f"{functions_count:,}", "Functions in selected scope")

st.markdown("")
a1, a2 = st.columns(2)
with a1:
    st.info(f"**Total Rows:** {total_rows:,}")
with a2:
    st.info(f"**Total Data Size:** {format_bytes(total_bytes)}")

# -----------------------------------------------------------------------------
# Debug panel
# -----------------------------------------------------------------------------
with st.expander("Debug counts"):
    st.write("Loaded tables/views rows:", len(obj_df))
    st.write("Loaded procedures rows:", len(proc_df))
    st.write("Loaded functions rows:", len(func_df))
    st.write("Filtered procedures rows:", len(filtered_proc_df))
    st.write("Filtered functions rows:", len(filtered_func_df))

# -----------------------------------------------------------------------------
# Tabs
# -----------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Overview",
    "Tables & Views",
    "Schemas",
    "Procedures",
    "Functions"
])

# -----------------------------------------------------------------------------
# Tab 1 - Overview
# -----------------------------------------------------------------------------
with tab1:
    st.subheader("Database Overview")

    db_summary = (
        filtered_df.groupby("TABLE_CATALOG", dropna=False)
        .agg(
            SCHEMAS=("TABLE_SCHEMA", "nunique"),
            OBJECTS=("TABLE_NAME", "count"),
            TABLES=("TABLE_TYPE", lambda s: int(s.astype(str).str.contains("TABLE", case=False, na=False).sum())),
            VIEWS=("TABLE_TYPE", lambda s: int(s.astype(str).str.contains("VIEW", case=False, na=False).sum())),
            ROWS=("ROW_COUNT", "sum"),
            BYTES=("BYTES", "sum"),
            COLUMNS=("COLUMN_COUNT", "sum")
        )
        .reset_index()
        .rename(columns={"TABLE_CATALOG": "DATABASE"})
    )

    if not filtered_proc_df.empty:
        proc_summary = (
            filtered_proc_df.groupby("PROCEDURE_CATALOG")
            .size()
            .reset_index(name="PROCEDURES")
            .rename(columns={"PROCEDURE_CATALOG": "DATABASE"})
        )
        db_summary = db_summary.merge(proc_summary, on="DATABASE", how="left")
    else:
        db_summary["PROCEDURES"] = 0

    if not filtered_func_df.empty:
        func_summary = (
            filtered_func_df.groupby("FUNCTION_CATALOG")
            .size()
            .reset_index(name="FUNCTIONS")
            .rename(columns={"FUNCTION_CATALOG": "DATABASE"})
        )
        db_summary = db_summary.merge(func_summary, on="DATABASE", how="left")
    else:
        db_summary["FUNCTIONS"] = 0

    db_summary["PROCEDURES"] = db_summary["PROCEDURES"].fillna(0).astype(int)
    db_summary["FUNCTIONS"] = db_summary["FUNCTIONS"].fillna(0).astype(int)
    db_summary["SIZE"] = db_summary["BYTES"].apply(format_bytes)

    st.dataframe(
        db_summary[[
            "DATABASE", "SCHEMAS", "OBJECTS", "TABLES", "VIEWS",
            "PROCEDURES", "FUNCTIONS", "ROWS", "COLUMNS", "SIZE"
        ]].sort_values(["OBJECTS", "ROWS"], ascending=[False, False]),
        use_container_width=True,
        hide_index=True
    )

# -----------------------------------------------------------------------------
# Tab 2 - Tables & Views
# -----------------------------------------------------------------------------
with tab2:
    st.subheader("Tables and Views Detail")

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
# Tab 3 - Schemas
# -----------------------------------------------------------------------------
with tab3:
    st.subheader("Schema Summary")

    schema_summary = (
        filtered_df.groupby(["TABLE_CATALOG", "TABLE_SCHEMA"], dropna=False)
        .agg(
            OBJECTS=("TABLE_NAME", "count"),
            TABLES=("TABLE_TYPE", lambda s: int(s.astype(str).str.contains("TABLE", case=False, na=False).sum())),
            VIEWS=("TABLE_TYPE", lambda s: int(s.astype(str).str.contains("VIEW", case=False, na=False).sum())),
            ROWS=("ROW_COUNT", "sum"),
            BYTES=("BYTES", "sum"),
            COLUMNS=("COLUMN_COUNT", "sum"),
            LAST_ALTERED=("LAST_ALTERED", "max")
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
            .reset_index(name="PROCEDURES")
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
        schema_summary["PROCEDURES"] = 0

    if not filtered_func_df.empty:
        func_schema_summary = (
            filtered_func_df.groupby(["FUNCTION_CATALOG", "FUNCTION_SCHEMA"])
            .size()
            .reset_index(name="FUNCTIONS")
            .rename(columns={
                "FUNCTION_CATALOG": "DATABASE",
                "FUNCTION_SCHEMA": "SCHEMA"
            })
        )
        schema_summary = schema_summary.merge(
            func_schema_summary,
            on=["DATABASE", "SCHEMA"],
            how="left"
        )
    else:
        schema_summary["FUNCTIONS"] = 0

    schema_summary["PROCEDURES"] = schema_summary["PROCEDURES"].fillna(0).astype(int)
    schema_summary["FUNCTIONS"] = schema_summary["FUNCTIONS"].fillna(0).astype(int)
    schema_summary["SIZE"] = schema_summary["BYTES"].apply(format_bytes)

    st.dataframe(
        schema_summary[[
            "DATABASE", "SCHEMA", "OBJECTS", "TABLES", "VIEWS",
            "PROCEDURES", "FUNCTIONS", "ROWS", "COLUMNS", "SIZE", "LAST_ALTERED"
        ]].sort_values(["DATABASE", "OBJECTS"], ascending=[True, False]),
        use_container_width=True,
        hide_index=True
    )

# -----------------------------------------------------------------------------
# Tab 4 - Procedures
# -----------------------------------------------------------------------------
with tab4:
    st.subheader("Stored Procedures")

    if filtered_proc_df.empty:
        st.info("No procedures found for the selected scope.")
    else:
        proc_search = st.text_input("Search procedure name", key="proc_search")

        proc_display = filtered_proc_df.copy()
        if proc_search:
            proc_display = proc_display[
                proc_display["PROCEDURE_NAME"].astype(str).str.contains(proc_search, case=False, na=False)
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
                "LAST_ALTERED",
                "COMMENT"
            ]].rename(columns={
                "PROCEDURE_CATALOG": "DATABASE",
                "PROCEDURE_SCHEMA": "SCHEMA",
                "PROCEDURE_NAME": "PROCEDURE",
                "PROCEDURE_LANGUAGE": "LANGUAGE",
                "PROCEDURE_OWNER": "OWNER"
            }).sort_values(["DATABASE", "SCHEMA", "PROCEDURE"]),
            use_container_width=True,
            hide_index=True
        )

# -----------------------------------------------------------------------------
# Tab 5 - Functions
# -----------------------------------------------------------------------------
with tab5:
    st.subheader("Functions")

    if filtered_func_df.empty:
        st.info("No functions found for the selected scope.")
    else:
        func_search = st.text_input("Search function name", key="func_search")

        func_display = filtered_func_df.copy()
        if func_search:
            func_display = func_display[
                func_display["FUNCTION_NAME"].astype(str).str.contains(func_search, case=False, na=False)
            ].copy()

        st.dataframe(
            func_display[[
                "FUNCTION_CATALOG",
                "FUNCTION_SCHEMA",
                "FUNCTION_NAME",
                "ARGUMENT_SIGNATURE",
                "RETURNS",
                "FUNCTION_LANGUAGE",
                "FUNCTION_OWNER",
                "CREATED",
                "LAST_ALTERED",
                "COMMENT"
            ]].rename(columns={
                "FUNCTION_CATALOG": "DATABASE",
                "FUNCTION_SCHEMA": "SCHEMA",
                "FUNCTION_NAME": "FUNCTION",
                "FUNCTION_LANGUAGE": "LANGUAGE",
                "FUNCTION_OWNER": "OWNER"
            }).sort_values(["DATABASE", "SCHEMA", "FUNCTION"]),
            use_container_width=True,
            hide_index=True
        )

# -----------------------------------------------------------------------------
# Footer
# -----------------------------------------------------------------------------
st.markdown("---")
st.caption(
    "Tables and views come from SNOWFLAKE.ACCOUNT_USAGE.TABLES/COLUMNS. "
    "Procedures and functions try ACCOUNT_USAGE first, then fall back to SHOW USER ... IN ACCOUNT."
)
