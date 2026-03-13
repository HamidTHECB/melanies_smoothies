import streamlit as st
import pandas as pd
from snowflake.snowpark.context import get_active_session

# -----------------------------------------------------------------------------
# Page setup
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="SIS Metadata Explorer",
    layout="wide"
)

st.title("SIS Metadata Explorer")
st.caption("Explore Snowflake table metadata, column details, and sample data for the SIS database.")

session = get_active_session()

TARGET_DATABASE = "SIS"

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def human_bytes(num):
    if num is None:
        return "0 B"
    num = float(num)
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if abs(num) < 1024.0:
            return f"{num:,.1f} {unit}"
        num /= 1024.0
    return f"{num:,.1f} EB"

def human_number(num):
    if num is None:
        return "0"
    num = float(num)
    for unit in ["", "K", "M", "B", "T"]:
        if abs(num) < 1000:
            return f"{num:,.1f}{unit}".replace(".0", "")
        num /= 1000.0
    return f"{num:,.1f}P".replace(".0", "")

@st.cache_data(ttl=600)
def load_table_metadata(database_name: str) -> pd.DataFrame:
    query = f"""
        SELECT
            TABLE_CATALOG,
            TABLE_SCHEMA,
            TABLE_NAME,
            TABLE_OWNER,
            TABLE_TYPE,
            ROW_COUNT,
            BYTES,
            CREATED,
            LAST_ALTERED,
            COMMENT
        FROM {database_name}.INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA NOT IN ('INFORMATION_SCHEMA')
        ORDER BY TABLE_SCHEMA, TABLE_NAME
    """
    return session.sql(query).to_pandas()

@st.cache_data(ttl=600)
def load_column_metadata(database_name: str) -> pd.DataFrame:
    query = f"""
        SELECT
            TABLE_CATALOG,
            TABLE_SCHEMA,
            TABLE_NAME,
            COLUMN_NAME,
            ORDINAL_POSITION,
            DATA_TYPE,
            IS_NULLABLE,
            COLUMN_DEFAULT,
            COMMENT
        FROM {database_name}.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA NOT IN ('INFORMATION_SCHEMA')
        ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
    """
    return session.sql(query).to_pandas()

def get_sample_data(database_name: str, schema_name: str, table_name: str, limit: int = 100):
    full_name = f'"{database_name}"."{schema_name}"."{table_name}"'
    query = f"SELECT * FROM {full_name} LIMIT {limit}"
    return session.sql(query).to_pandas()

# -----------------------------------------------------------------------------
# Load data
# -----------------------------------------------------------------------------
try:
    df_tables = load_table_metadata(TARGET_DATABASE)
    df_columns = load_column_metadata(TARGET_DATABASE)
except Exception as e:
    st.error(f"Could not load metadata from database {TARGET_DATABASE}.")
    st.exception(e)
    st.stop()

if df_tables.empty:
    st.warning(f"No tables found in database {TARGET_DATABASE}.")
    st.stop()

# Ensure datetime columns are parsed
for col in ["CREATED", "LAST_ALTERED"]:
    if col in df_tables.columns:
        df_tables[col] = pd.to_datetime(df_tables[col], errors="coerce")

# Fill null-friendly columns
df_tables["TABLE_OWNER"] = df_tables["TABLE_OWNER"].fillna("Unknown")
df_tables["COMMENT"] = df_tables["COMMENT"].fillna("")
df_tables["ROW_COUNT"] = df_tables["ROW_COUNT"].fillna(0)
df_tables["BYTES"] = df_tables["BYTES"].fillna(0)

# Column count per table
df_col_count = (
    df_columns.groupby(["TABLE_SCHEMA", "TABLE_NAME"])
    .size()
    .reset_index(name="COLUMN_COUNT")
)

df_tables = df_tables.merge(
    df_col_count,
    on=["TABLE_SCHEMA", "TABLE_NAME"],
    how="left"
)
df_tables["COLUMN_COUNT"] = df_tables["COLUMN_COUNT"].fillna(0).astype(int)

# -----------------------------------------------------------------------------
# Sidebar filters
# -----------------------------------------------------------------------------
st.sidebar.header("Filters")

schema_options = ["All"] + sorted(df_tables["TABLE_SCHEMA"].dropna().unique().tolist())
owner_options = ["All"] + sorted(df_tables["TABLE_OWNER"].dropna().unique().tolist())
type_options = sorted(df_tables["TABLE_TYPE"].dropna().unique().tolist())

selected_schema = st.sidebar.selectbox("Schema", schema_options, index=0)
selected_owner = st.sidebar.selectbox("Owner", owner_options, index=0)
selected_types = st.sidebar.multiselect("Table Type", type_options, default=type_options)

search_text = st.sidebar.text_input("Search table name", value="").strip()

max_rows = int(df_tables["ROW_COUNT"].max()) if not df_tables.empty else 0
max_bytes_mb = max(1, int(df_tables["BYTES"].max() / (1024 * 1024))) if not df_tables.empty else 1

row_range = st.sidebar.slider(
    "Row count range",
    min_value=0,
    max_value=max_rows if max_rows > 0 else 1,
    value=(0, max_rows if max_rows > 0 else 1)
)

size_range_mb = st.sidebar.slider(
    "Size range (MB)",
    min_value=0,
    max_value=max_bytes_mb,
    value=(0, max_bytes_mb)
)

show_comments_only = st.sidebar.checkbox("Only tables with comments", value=False)

# -----------------------------------------------------------------------------
# Apply filters
# -----------------------------------------------------------------------------
filtered = df_tables.copy()

if selected_schema != "All":
    filtered = filtered[filtered["TABLE_SCHEMA"] == selected_schema]

if selected_owner != "All":
    filtered = filtered[filtered["TABLE_OWNER"] == selected_owner]

if selected_types:
    filtered = filtered[filtered["TABLE_TYPE"].isin(selected_types)]

if search_text:
    filtered = filtered[
        filtered["TABLE_NAME"].str.contains(search_text, case=False, na=False)
    ]

filtered = filtered[
    (filtered["ROW_COUNT"] >= row_range[0]) &
    (filtered["ROW_COUNT"] <= row_range[1])
]

filtered = filtered[
    (filtered["BYTES"] >= size_range_mb[0] * 1024 * 1024) &
    (filtered["BYTES"] <= size_range_mb[1] * 1024 * 1024)
]

if show_comments_only:
    filtered = filtered[filtered["COMMENT"].str.strip() != ""]

# -----------------------------------------------------------------------------
# Sort options
# -----------------------------------------------------------------------------
sort_label = st.selectbox(
    "Order By",
    [
        "Table Name A-Z",
        "Table Name Z-A",
        "Rows High-Low",
        "Rows Low-High",
        "Size High-Low",
        "Size Low-High",
        "Created Newest",
        "Created Oldest",
        "Last Altered Newest",
        "Last Altered Oldest",
    ]
)

sort_map = {
    "Table Name A-Z": ("TABLE_NAME", True),
    "Table Name Z-A": ("TABLE_NAME", False),
    "Rows High-Low": ("ROW_COUNT", False),
    "Rows Low-High": ("ROW_COUNT", True),
    "Size High-Low": ("BYTES", False),
    "Size Low-High": ("BYTES", True),
    "Created Newest": ("CREATED", False),
    "Created Oldest": ("CREATED", True),
    "Last Altered Newest": ("LAST_ALTERED", False),
    "Last Altered Oldest": ("LAST_ALTERED", True),
}
sort_col, sort_asc = sort_map[sort_label]
filtered = filtered.sort_values(by=sort_col, ascending=sort_asc)

# -----------------------------------------------------------------------------
# Summary metrics
# -----------------------------------------------------------------------------
col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    st.metric("Tables", int((filtered["TABLE_TYPE"] == "BASE TABLE").sum()))
with col2:
    st.metric("Views", int((filtered["TABLE_TYPE"] == "VIEW").sum()))
with col3:
    st.metric("Objects", len(filtered))
with col4:
    st.metric("Rows", human_number(filtered["ROW_COUNT"].sum()))
with col5:
    st.metric("Data Size", human_bytes(filtered["BYTES"].sum()))

st.divider()

# -----------------------------------------------------------------------------
# Main results table
# -----------------------------------------------------------------------------
display_df = filtered.copy()
display_df["SIZE"] = display_df["BYTES"].apply(human_bytes)
display_df["ROWS"] = display_df["ROW_COUNT"].apply(human_number)

result_columns = [
    "TABLE_SCHEMA",
    "TABLE_NAME",
    "TABLE_TYPE",
    "TABLE_OWNER",
    "ROWS",
    "SIZE",
    "COLUMN_COUNT",
    "CREATED",
    "LAST_ALTERED",
    "COMMENT"
]

st.subheader(f"Metadata Results ({len(display_df)})")
st.dataframe(
    display_df[result_columns],
    use_container_width=True,
    hide_index=True
)

# -----------------------------------------------------------------------------
# Detailed explorer
# -----------------------------------------------------------------------------
st.divider()
st.subheader("Table Explorer")

if filtered.empty:
    st.info("No tables match the current filters.")
    st.stop()

filtered["FULL_NAME"] = (
    filtered["TABLE_SCHEMA"] + "." + filtered["TABLE_NAME"]
)

selected_full_name = st.selectbox(
    "Select a table",
    filtered["FULL_NAME"].tolist()
)

selected_row = filtered[filtered["FULL_NAME"] == selected_full_name].iloc[0]
selected_schema_name = selected_row["TABLE_SCHEMA"]
selected_table_name = selected_row["TABLE_NAME"]

meta1, meta2, meta3, meta4 = st.columns(4)
with meta1:
    st.metric("Schema", selected_schema_name)
with meta2:
    st.metric("Type", selected_row["TABLE_TYPE"])
with meta3:
    st.metric("Rows", human_number(selected_row["ROW_COUNT"]))
with meta4:
    st.metric("Size", human_bytes(selected_row["BYTES"]))

with st.expander("Table metadata", expanded=True):
    st.write({
        "Database": selected_row["TABLE_CATALOG"],
        "Schema": selected_schema_name,
        "Table": selected_table_name,
        "Owner": selected_row["TABLE_OWNER"],
        "Type": selected_row["TABLE_TYPE"],
        "Created": str(selected_row["CREATED"]),
        "Last altered": str(selected_row["LAST_ALTERED"]),
        "Comment": selected_row["COMMENT"],
        "Column count": int(selected_row["COLUMN_COUNT"]),
    })

# -----------------------------------------------------------------------------
# Column explorer
# -----------------------------------------------------------------------------
st.subheader("Column Metadata")

col_filtered = df_columns[
    (df_columns["TABLE_SCHEMA"] == selected_schema_name) &
    (df_columns["TABLE_NAME"] == selected_table_name)
].copy()

if not col_filtered.empty:
    st.dataframe(
        col_filtered[
            [
                "ORDINAL_POSITION",
                "COLUMN_NAME",
                "DATA_TYPE",
                "IS_NULLABLE",
                "COLUMN_DEFAULT",
                "COMMENT"
            ]
        ],
        use_container_width=True,
        hide_index=True
    )
else:
    st.info("No column metadata found for this object.")

# -----------------------------------------------------------------------------
# Sample data preview
# -----------------------------------------------------------------------------
st.subheader("Sample Data Preview")

preview_limit = st.selectbox("Preview row limit", [10, 25, 50, 100], index=1)

if st.button("Load Sample Data"):
    try:
        sample_df = get_sample_data(
            TARGET_DATABASE,
            selected_schema_name,
            selected_table_name,
            preview_limit
        )
        st.dataframe(sample_df, use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning("Could not preview data. You may not have access or the object may not support SELECT.")
        st.exception(e)
