"""Optional BigQuery integration (set USE_BIGQUERY=true and install requirements-bigquery.txt).
Falls back to the local CSV so the app works without any cloud credentials."""
import logging
import os

import pandas as pd

from backend.paths import ACCIDENTS_CSV

log = logging.getLogger(__name__)


def enabled() -> bool:
    return os.getenv("USE_BIGQUERY", "false").lower() == "true"


def _client():
    from google.cloud import bigquery  # imported lazily: heavy, optional
    return bigquery.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT"))


def upload_accident_data() -> bool:
    try:
        from google.cloud import bigquery
        table = f"{os.getenv('GOOGLE_CLOUD_PROJECT')}.truetraffic_data.accidents"
        df = pd.read_csv(ACCIDENTS_CSV)
        job = _client().load_table_from_dataframe(
            df, table, job_config=bigquery.LoadJobConfig(
                write_disposition="WRITE_TRUNCATE", autodetect=True))
        job.result()
        log.info("Uploaded %d rows to %s", len(df), table)
        return True
    except Exception as exc:
        log.error("BigQuery upload failed: %s", exc)
        return False


def get_accident_stats():
    """Per-road aggregates; BigQuery when enabled, otherwise the CSV."""
    if enabled():
        try:
            q = f"""
                SELECT road_name, AVG(risk_score) AS avg_risk,
                       COUNT(*) AS total_accidents, SUM(fatalities) AS total_fatalities
                FROM `{os.getenv('GOOGLE_CLOUD_PROJECT')}.truetraffic_data.accidents`
                GROUP BY road_name ORDER BY avg_risk DESC"""
            return _client().query(q).to_dataframe().to_dict(orient="records")
        except Exception as exc:
            log.warning("BigQuery query failed (%s); using local CSV", exc)
    df = pd.read_csv(ACCIDENTS_CSV)
    return (df.groupby("road_name")
              .agg(avg_risk=("risk_score", "mean"), total_accidents=("accident_id", "count"),
                   total_fatalities=("fatalities", "sum"))
              .reset_index().to_dict(orient="records"))
