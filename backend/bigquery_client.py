from google.cloud import bigquery
from google.oauth2 import service_account
import os
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

def get_client():
    try:
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        client = bigquery.Client(project=project_id)
        return client
    except Exception as e:
        print(f"BigQuery connection error: {e}")
        return None

def upload_accident_data():
    try:
        client = get_client()
        if not client:
            return False

        project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        dataset_id = "truetraffic_data"
        table_id = f"{project_id}.{dataset_id}.accidents"

        df = pd.read_csv("data/vizag_accidents.csv")

        job_config = bigquery.LoadJobConfig(
            write_disposition="WRITE_TRUNCATE",
            autodetect=True
        )

        job = client.load_table_from_dataframe(
            df, table_id, job_config=job_config
        )
        job.result()

        print(f"✅ Uploaded {len(df)} rows to BigQuery")
        return True

    except Exception as e:
        print(f"Upload error: {e}")
        return False

def get_accident_stats():
    try:
        client = get_client()
        if not client:
            return get_local_stats()

        project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        query = f"""
        SELECT 
            road_name,
            AVG(risk_score) as avg_risk,
            COUNT(*) as total_accidents,
            SUM(fatalities) as total_fatalities,
            MAX(hour) as peak_hour
        FROM `{project_id}.truetraffic_data.accidents`
        GROUP BY road_name
        ORDER BY avg_risk DESC
        """

        result = client.query(query).to_dataframe()
        return result.to_dict(orient="records")

    except Exception as e:
        print(f"Query error: {e}, using local data")
        return get_local_stats()

def get_local_stats():
    # Fallback to local CSV if BigQuery unavailable
    df = pd.read_csv("data/vizag_accidents.csv")
    stats = df.groupby("road_name").agg(
        avg_risk=("risk_score", "mean"),
        total_accidents=("accident_id", "count"),
        total_fatalities=("fatalities", "sum")
    ).reset_index()
    return stats.to_dict(orient="records")