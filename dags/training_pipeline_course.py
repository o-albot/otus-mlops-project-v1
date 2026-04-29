"""
DAG: query_latency_training
Description: DAG for periodic training of query latency prediction model with Dataproc and PySpark.
"""

import uuid
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.settings import Session
from airflow.models import Connection, Variable
from airflow.utils.trigger_rule import TriggerRule
from airflow.providers.yandex.operators.dataproc import (
    DataprocCreateClusterOperator,
    DataprocCreatePysparkJobOperator,
    DataprocDeleteClusterOperator
)

# Общие переменные
YC_ZONE = Variable.get("YC_ZONE")
YC_FOLDER_ID = Variable.get("YC_FOLDER_ID")
YC_SUBNET_ID = Variable.get("YC_SUBNET_ID")
YC_SSH_PUBLIC_KEY = Variable.get("YC_SSH_PUBLIC_KEY")

# S3 переменные
S3_ENDPOINT_URL = Variable.get("S3_ENDPOINT_URL")
S3_ACCESS_KEY = Variable.get("S3_ACCESS_KEY")
S3_SECRET_KEY = Variable.get("S3_SECRET_KEY")
S3_BUCKET_NAME = Variable.get("S3_BUCKET_NAME")
S3_INPUT_DATA_BUCKET = f"s3a://{S3_BUCKET_NAME}/input_data"
S3_OUTPUT_MODEL_BUCKET = f"s3a://{S3_BUCKET_NAME}/models"
S3_PROCESSED_DATA_BUCKET = f"s3a://{S3_BUCKET_NAME}/processed"
S3_SRC_BUCKET = f"s3a://{S3_BUCKET_NAME}/src"
S3_DP_LOGS_BUCKET = f"s3a://{S3_BUCKET_NAME}/airflow_logs/"
S3_VENV_ARCHIVE = f"s3a://{S3_BUCKET_NAME}/venvs/venv.tar.gz"

# Dataproc переменные
DP_SA_AUTH_KEY_PUBLIC_KEY = Variable.get("DP_SA_AUTH_KEY_PUBLIC_KEY")
DP_SA_JSON = Variable.get("DP_SA_JSON")
DP_SA_ID = Variable.get("DP_SA_ID")
DP_SECURITY_GROUP_ID = Variable.get("DP_SECURITY_GROUP_ID")

# MLflow
MLFLOW_TRACKING_URI = Variable.get("MLFLOW_TRACKING_URI")
MLFLOW_EXPERIMENT_NAME = "query_latency_prediction"

# Connections
YC_S3_CONNECTION = Connection(
    conn_id="yc-s3",
    conn_type="s3",
    host=S3_ENDPOINT_URL,
    extra={
        "aws_access_key_id": S3_ACCESS_KEY,
        "aws_secret_access_key": S3_SECRET_KEY,
        "host": S3_ENDPOINT_URL,
    },
)
YC_SA_CONNECTION = Connection(
    conn_id="yc-sa",
    conn_type="yandexcloud",
    extra={
        "extra__yandexcloud__public_ssh_key": DP_SA_AUTH_KEY_PUBLIC_KEY,
        "extra__yandexcloud__service_account_json": DP_SA_JSON,
    },
)


def setup_airflow_connections(*connections: Connection) -> None:
    session = Session()
    try:
        for conn in connections:
            print("Checking connection:", conn.conn_id)
            if not session.query(Connection).filter(Connection.conn_id == conn.conn_id).first():
                session.add(conn)
                print("Added connection:", conn.conn_id)
        session.commit()
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def run_setup_connections(**kwargs):
    setup_airflow_connections(YC_S3_CONNECTION, YC_SA_CONNECTION)
    return True


default_args = {
    'owner': 'mlops',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 0,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    dag_id="query_latency_training",
    default_args=default_args,
    description="Periodic training of query latency prediction model",
    schedule_interval=timedelta(minutes=60),
    start_date=datetime(2025, 3, 27),
    catchup=False,
    tags=['mlops', 'query_latency'],
) as dag:
    setup_connections = PythonOperator(
        task_id="setup_connections",
        python_callable=run_setup_connections,
    )

    create_spark_cluster = DataprocCreateClusterOperator(
        task_id="spark-cluster-create-task",
        folder_id=YC_FOLDER_ID,
        cluster_name=f"tmp-dp-query-latency-{uuid.uuid4()}",
        cluster_description="YC Temporary cluster for query latency model training",
        subnet_id=YC_SUBNET_ID,
        s3_bucket=S3_DP_LOGS_BUCKET,
        service_account_id=DP_SA_ID,
        ssh_public_keys=YC_SSH_PUBLIC_KEY,
        zone=YC_ZONE,
        cluster_image_version="2.0",
        masternode_resource_preset="s3-c2-m8",
        masternode_disk_type="network-ssd",
        masternode_disk_size=50,
        datanode_resource_preset="s3-c4-m16",
        datanode_disk_type="network-ssd",
        datanode_disk_size=50,
        datanode_count=1,
        computenode_count=0,
        services=["YARN", "SPARK", "HDFS", "MAPREDUCE"],
        connection_id=YC_SA_CONNECTION.conn_id,
        dag=dag,
    )

    preprocess_data = DataprocCreatePysparkJobOperator(
        task_id="preprocess_data",
        main_python_file_uri=f"{S3_SRC_BUCKET}/preprocess_course.py",
        connection_id=YC_SA_CONNECTION.conn_id,
        dag=dag,
        args=[
            "--input", f"{S3_INPUT_DATA_BUCKET}/cloud_query_dataset.csv",
            "--output-train", f"{S3_PROCESSED_DATA_BUCKET}/train_proc.parquet",
            "--output-test", f"{S3_PROCESSED_DATA_BUCKET}/test_proc.parquet",
            "--tracking-uri", MLFLOW_TRACKING_URI,
            "--experiment-name", "feature_pipeline",
            "--test-size", "0.2",
            "--seed", "42",
            "--s3-endpoint-url", S3_ENDPOINT_URL,
            "--s3-access-key", S3_ACCESS_KEY,
            "--s3-secret-key", S3_SECRET_KEY,
        ],
        properties={
            'spark.submit.deployMode': 'cluster',
            'spark.yarn.dist.archives': f'{S3_VENV_ARCHIVE}#.venv',
            'spark.yarn.appMasterEnv.PYSPARK_PYTHON': './.venv/bin/python3',
            'spark.yarn.appMasterEnv.PYSPARK_DRIVER_PYTHON': './.venv/bin/python3',
        },
    )

    train_model = DataprocCreatePysparkJobOperator(
        task_id="train_model",
        main_python_file_uri=f"{S3_SRC_BUCKET}/train_course.py",
        connection_id=YC_SA_CONNECTION.conn_id,
        dag=dag,
        args=[
            "--input-train", f"{S3_PROCESSED_DATA_BUCKET}/train_proc.parquet",
            "--input-test", f"{S3_PROCESSED_DATA_BUCKET}/test_proc.parquet",
            "--output", f"{S3_OUTPUT_MODEL_BUCKET}/model_{datetime.now().strftime('%Y%m%d')}",
            "--tracking-uri", MLFLOW_TRACKING_URI,
            "--experiment-name", MLFLOW_EXPERIMENT_NAME,
            "--model-type", "rf",
            "--auto-register",
            "--run-name", f"training_{datetime.now().strftime('%Y%m%d_%H%M')}",
            "--s3-bucket-name", S3_BUCKET_NAME,
            "--s3-endpoint-url", S3_ENDPOINT_URL,
            "--s3-access-key", S3_ACCESS_KEY,
            "--s3-secret-key", S3_SECRET_KEY,
        ],
        properties={
            'spark.submit.deployMode': 'cluster',
            'spark.yarn.dist.archives': f'{S3_VENV_ARCHIVE}#.venv',
            'spark.yarn.appMasterEnv.PYSPARK_PYTHON': './.venv/bin/python3',
            'spark.yarn.appMasterEnv.PYSPARK_DRIVER_PYTHON': './.venv/bin/python3',
        },
    )

    # A/B тестирование на ТЕХ ЖЕ ДАННЫХ (cloud_query_dataset.csv)
    ab_test_validation = DataprocCreatePysparkJobOperator(
        task_id="ab_test_validation",
        main_python_file_uri=f"{S3_SRC_BUCKET}/ab_test_course.py",
        connection_id=YC_SA_CONNECTION.conn_id,
        dag=dag,
        args=[
            "--input", f"{S3_INPUT_DATA_BUCKET}/cloud_query_dataset.csv",
            "--s3-endpoint-url", S3_ENDPOINT_URL,
            "--s3-access-key", S3_ACCESS_KEY,
            "--s3-secret-key", S3_SECRET_KEY,
            "--tracking-uri", MLFLOW_TRACKING_URI,
            "--s3-bucket-name", S3_BUCKET_NAME,
            "--experiment-name", MLFLOW_EXPERIMENT_NAME,
        ],
        properties={
            'spark.submit.deployMode': 'cluster',
            'spark.yarn.dist.archives': f'{S3_VENV_ARCHIVE}#.venv',
            'spark.yarn.appMasterEnv.PYSPARK_PYTHON': './.venv/bin/python3',
            'spark.yarn.appMasterEnv.PYSPARK_DRIVER_PYTHON': './.venv/bin/python3',
            'spark.yarn.appMasterEnv.PYTHONUNBUFFERED': '1',
        },
    )

    delete_spark_cluster = DataprocDeleteClusterOperator(
        task_id="spark-cluster-delete-task",
        trigger_rule=TriggerRule.ALL_DONE,
        dag=dag,
    )

#    setup_connections >> create_spark_cluster >> preprocess_data >> train_model >> ab_test_validation
    setup_connections >> create_spark_cluster >> preprocess_data >> train_model >> ab_test_validation >> delete_spark_cluster