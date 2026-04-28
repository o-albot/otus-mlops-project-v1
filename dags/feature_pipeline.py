"""
DAG #1: Feature Pipeline - Data preprocessing
Schedule: Daily
"""

from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.dummy import DummyOperator
from airflow.providers.yandex.operators.dataproc import (
    DataprocCreateClusterOperator,
    DataprocCreatePysparkJobOperator,
    DataprocDeleteClusterOperator
)
from airflow.models import Variable
from airflow.utils.trigger_rule import TriggerRule
from airflow.operators.python import PythonOperator
import os

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ПОЛУЧЕНИЯ ПЕРЕМЕННЫХ ==========

def get_var(name, default=None):
    """Безопасное получение переменной Airflow (не ломает парсинг DAG)"""
    try:
        return Variable.get(name)
    except KeyError:
        if default is not None:
            return default
        raise

def get_env(name, default=None):
    """Безопасное получение переменной окружения"""
    return os.environ.get(name, default)

# ========== DEFAULTS ==========
default_args = {
    'owner': 'mlops',
    'depends_on_past': False,
    'start_date': datetime(2025, 1, 1),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

# ========== DAG DEFINITION ==========
dag = DAG(
    dag_id="feature_pipeline",
    default_args=default_args,
    description="Feature Pipeline: Data preprocessing for query latency prediction",
    schedule_interval=timedelta(days=1),
    catchup=False,
    max_active_runs=1,
    tags=['feature', 'preprocessing', 'mlops'],
)

# ========== TASK 0: ПРОВЕРКА ПЕРЕМЕННЫХ ==========

def check_variables(**context):
    """Проверяет наличие всех необходимых переменных при запуске"""
    required_vars = [
        "YC_ZONE", "YC_FOLDER_ID", "YC_SUBNET_ID", "YC_SSH_PUBLIC_KEY",
        "DP_SA_ID", "DP_SA_AUTH_KEY_PUBLIC_KEY",
        "DP_SECURITY_GROUP_ID", "MLFLOW_TRACKING_URI"
    ]
    
    missing = []
    for var in required_vars:
        try:
            Variable.get(var)
        except KeyError:
            missing.append(var)
    
    if missing:
        raise ValueError(f"Missing Airflow variables: {', '.join(missing)}")
    
    # Проверка переменных окружения
    if not os.environ.get("S3_BUCKET"):
        raise ValueError("S3_BUCKET environment variable not set")
    if not os.environ.get("S3_ENDPOINT"):
        raise ValueError("S3_ENDPOINT environment variable not set")
    
    print("✅ All variables are present")
    return True

check_vars_task = PythonOperator(
    task_id="check_variables",
    python_callable=check_variables,
    dag=dag,
)

# ========== TASK 1: START ==========
start = DummyOperator(
    task_id="start",
    dag=dag,
)

# ========== TASK 2: CREATE CLUSTER (переменные берутся при выполнении) ==========

def get_cluster_config(**context):
    """Получает конфигурацию кластера при выполнении (не при парсинге)"""
    return {
        "folder_id": Variable.get("YC_FOLDER_ID"),
        "subnet_id": Variable.get("YC_SUBNET_ID"),
        "service_account_id": Variable.get("DP_SA_ID"),
        "ssh_public_keys": Variable.get("YC_SSH_PUBLIC_KEY"),
        "zone": Variable.get("YC_ZONE"),
        "s3_bucket": f"s3a://{os.environ.get('S3_BUCKET')}/airflow_logs/",
    }

create_cluster = DataprocCreateClusterOperator(
    task_id="create_dataproc_cluster",
    folder_id="{{ var.value.YC_FOLDER_ID }}",
    cluster_name=f"feature-pipeline-{{{{ ts_nodash }}}}",
    cluster_description="Temporary Dataproc cluster for feature preprocessing",
    subnet_id="{{ var.value.YC_SUBNET_ID }}",
    s3_bucket=f"s3a://{os.environ.get('S3_BUCKET', '')}/airflow_logs/",
    service_account_id="{{ var.value.DP_SA_ID }}",
    ssh_public_keys="{{ var.value.YC_SSH_PUBLIC_KEY }}",
    zone="{{ var.value.YC_ZONE }}",
    cluster_image_version="2.0",
    masternode_resource_preset="s3-c2-m8",
    masternode_disk_type="network-ssd",
    masternode_disk_size=50,
    datanode_resource_preset="s3-c4-m16",
    datanode_disk_type="network-ssd",
    datanode_disk_size=100,
    datanode_count=1,
    computenode_count=0,
    services=["YARN", "SPARK", "HDFS"],
    connection_id="yc_sa_connection",
    dag=dag,
)

# ========== TASK 3: PREPROCESS DATA ==========

preprocess = DataprocCreatePysparkJobOperator(
    task_id="preprocess_data",
    main_python_file_uri=f"s3a://{os.environ.get('S3_BUCKET', '')}/src/preprocess.py",
    connection_id="yc_sa_connection",
    dag=dag,
    args=[
        "--input", f"s3a://{os.environ.get('S3_BUCKET', '')}/data/cloud_query_dataset.csv",
        "--output-train", f"s3a://{os.environ.get('S3_BUCKET', '')}/processed/train.parquet",
        "--output-test", f"s3a://{os.environ.get('S3_BUCKET', '')}/processed/test.parquet",
        "--tracking-uri", "{{ var.value.MLFLOW_TRACKING_URI }}",
        "--experiment-name", "feature_pipeline",
        "--test-size", "0.2",
        "--seed", "42",
    ],
    properties={
        'spark.submit.deployMode': 'cluster',
        'spark.yarn.dist.archives': f"s3a://{os.environ.get('S3_BUCKET', '')}/venvs/venv.tar.gz#.venv",
        'spark.yarn.appMasterEnv.PYSPARK_PYTHON': './.venv/bin/python3',
        'spark.yarn.appMasterEnv.PYSPARK_DRIVER_PYTHON': './.venv/bin/python3',
        'spark.sql.adaptive.enabled': 'true',
        'spark.hadoop.fs.s3a.endpoint': os.environ.get('S3_ENDPOINT', ''),
    },
)

# ========== TASK 4: DELETE CLUSTER ==========
delete_cluster = DataprocDeleteClusterOperator(
    task_id="delete_dataproc_cluster",
    trigger_rule=TriggerRule.ALL_DONE,
    dag=dag,
)

# ========== TASK 5: END ==========
end = DummyOperator(
    task_id="end",
    trigger_rule=TriggerRule.ALL_DONE,
    dag=dag,
)

# ========== DEPENDENCIES ==========
start >> check_vars_task >> create_cluster >> preprocess >> delete_cluster >> end
