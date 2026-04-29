"""
DAG #3: Batch Inference
"""

import uuid
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.models import Variable, Connection
from airflow.settings import Session
from airflow.utils.trigger_rule import TriggerRule
from airflow.providers.yandex.operators.dataproc import (
    DataprocCreateClusterOperator,
    DataprocCreatePysparkJobOperator,
    DataprocDeleteClusterOperator
)

YC_ZONE = Variable.get("YC_ZONE")
YC_FOLDER_ID = Variable.get("YC_FOLDER_ID")
YC_SUBNET_ID = Variable.get("YC_SUBNET_ID")
YC_SSH_PUBLIC_KEY = Variable.get("YC_SSH_PUBLIC_KEY")
DP_SA_ID = Variable.get("DP_SA_ID")
MLFLOW_TRACKING_URI = Variable.get("MLFLOW_TRACKING_URI")

S3_ENDPOINT_URL = Variable.get("S3_ENDPOINT_URL")
S3_ACCESS_KEY = Variable.get("S3_ACCESS_KEY")
S3_SECRET_KEY = Variable.get("S3_SECRET_KEY")
S3_BUCKET_NAME = Variable.get("S3_BUCKET_NAME")

S3_INPUT_DATA = f"s3a://{S3_BUCKET_NAME}/input_data/new_data.csv"
S3_PREDICTIONS = f"s3a://{S3_BUCKET_NAME}/predictions/predictions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.parquet"
S3_SRC_BUCKET = f"s3a://{S3_BUCKET_NAME}/src"
S3_DP_LOGS = f"s3a://{S3_BUCKET_NAME}/airflow_logs/"
S3_VENV_ARCHIVE = f"s3a://{S3_BUCKET_NAME}/venvs/venv.tar.gz"

DP_SA_AUTH_KEY_PUBLIC_KEY = Variable.get("DP_SA_AUTH_KEY_PUBLIC_KEY")
DP_SA_JSON = Variable.get("DP_SA_JSON")

YC_SA_CONNECTION = Connection(
    conn_id="yc-sa",
    conn_type="yandexcloud",
    extra={
        "extra__yandexcloud__public_ssh_key": DP_SA_AUTH_KEY_PUBLIC_KEY,
        "extra__yandexcloud__service_account_json": DP_SA_JSON,
    },
)

def setup_connections():
    session = Session()
    if not session.query(Connection).filter(Connection.conn_id == "yc-sa").first():
        session.add(YC_SA_CONNECTION)
        session.commit()
    session.close()

default_args = {
    'owner': 'mlops',
    'depends_on_past': False,
    'start_date': datetime(2025, 1, 1),
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    dag_id="batch_inference",
    default_args=default_args,
    description="Batch inference using champion model",
    schedule_interval=timedelta(days=1),
    catchup=False,
    tags=['inference'],
) as dag:

    setup = PythonOperator(
        task_id="setup_connections",
        python_callable=setup_connections,
    )

    create_cluster = DataprocCreateClusterOperator(
        task_id="create_cluster",
        folder_id=YC_FOLDER_ID,
        cluster_name=f"inference-{uuid.uuid4()}",
        subnet_id=YC_SUBNET_ID,
        s3_bucket=S3_DP_LOGS,
        service_account_id=DP_SA_ID,
        ssh_public_keys=YC_SSH_PUBLIC_KEY,
        zone=YC_ZONE,
        cluster_image_version="2.0",
        masternode_resource_preset="s3-c2-m8",
        masternode_disk_size=50,
        datanode_resource_preset="s3-c4-m16",
        datanode_disk_size=100,
        datanode_count=1,
        services=["YARN", "SPARK", "HDFS"],
        connection_id="yc-sa",
        dag=dag,
    )

    inference = DataprocCreatePysparkJobOperator(
        task_id="run_inference",
        main_python_file_uri=f"{S3_SRC_BUCKET}/inference_course.py",
        connection_id="yc-sa",
        dag=dag,
        args=[
            "--input", S3_INPUT_DATA,
            "--output", S3_PREDICTIONS,
            "--model-name", "query_latency_prediction_model",
            "--tracking-uri", MLFLOW_TRACKING_URI,
            "--s3-endpoint-url", S3_ENDPOINT_URL,
            "--s3-access-key", S3_ACCESS_KEY,
            "--s3-secret-key", S3_SECRET_KEY,
            "--s3-bucket-name", S3_BUCKET_NAME,
        ],
        properties={
            'spark.submit.deployMode': 'cluster',
            'spark.yarn.dist.archives': f'{S3_VENV_ARCHIVE}#.venv',
            'spark.yarn.appMasterEnv.PYSPARK_PYTHON': './.venv/bin/python3',
            'spark.yarn.appMasterEnv.PYSPARK_DRIVER_PYTHON': './.venv/bin/python3',
        },
    )

    delete_cluster = DataprocDeleteClusterOperator(
        task_id="delete_cluster",
        trigger_rule=TriggerRule.ALL_DONE,
        dag=dag,
    )

    setup >> create_cluster >> inference >> delete_cluster