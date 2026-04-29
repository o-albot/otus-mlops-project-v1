"""
DAG #4: Model Retraining (weekly)
"""

from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.operators.python import PythonOperator
from airflow.models import Variable
from airflow.utils.trigger_rule import TriggerRule

MLFLOW_TRACKING_URI = Variable.get("MLFLOW_TRACKING_URI")
EXPERIMENT_NAME = "query_latency_prediction"
MODEL_NAME = f"{EXPERIMENT_NAME}_model"

def get_champion_rmse():
    from mlflow.tracking import MlflowClient
    client = MlflowClient(MLFLOW_TRACKING_URI)
    try:
        versions = client.get_latest_versions(MODEL_NAME)
        for v in versions:
            if hasattr(v, 'aliases') and "champion" in v.aliases:
                run = client.get_run(v.run_id)
                return run.data.metrics.get("test_rmse")
    except Exception as e:
        print(f"Error getting champion: {e}")
    return None

def notify_retraining_result(**context):
    dag_run = context['dag_run']
    triggered_run_id = dag_run.conf.get('training_run_id')
    
    if triggered_run_id:
        from mlflow.tracking import MlflowClient
        client = MlflowClient(MLFLOW_TRACKING_URI)
        try:
            run = client.get_run(triggered_run_id)
            new_rmse = run.data.metrics.get("test_rmse")
            champion_rmse = get_champion_rmse()
            
            if new_rmse and champion_rmse and new_rmse < champion_rmse:
                print(f"✅ Новая модель лучше! Champion обновлён. RMSE: {champion_rmse:.4f} → {new_rmse:.4f}")
            elif new_rmse and champion_rmse:
                print(f"ℹ️ Champion не обновлён. Текущий RMSE: {champion_rmse:.4f}, новый: {new_rmse:.4f}")
            else:
                print("⚠️ Не удалось получить метрики для сравнения")
        except Exception as e:
            print(f"Error: {e}")
    else:
        print("Нет данных о запуске training_pipeline")

default_args = {
    'owner': 'mlops',
    'depends_on_past': False,
    'start_date': datetime(2025, 1, 1),
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    dag_id="retraining_pipeline",
    default_args=default_args,
    description="Weekly model retraining",
    schedule_interval=timedelta(weeks=1),
    catchup=False,
    tags=['retraining'],
) as dag:

    trigger_training = TriggerDagRunOperator(
        task_id="trigger_training",
        trigger_dag_id="query_latency_training",
        wait_for_completion=True,
        poke_interval=30,
        allowed_states=['success'],
    )

    notify = PythonOperator(
        task_id="notify_result",
        python_callable=notify_retraining_result,
        provide_context=True,
        trigger_rule=TriggerRule.ALL_DONE,
    )

    trigger_training >> notify