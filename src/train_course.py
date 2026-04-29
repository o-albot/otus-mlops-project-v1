"""
Script: train_course.py
Description: PySpark script for training query latency prediction model (regression)
"""

import os
import sys
import traceback
import argparse
import mlflow
import mlflow.spark
from mlflow.tracking import MlflowClient
from pyspark.sql import SparkSession
from pyspark.ml import Pipeline
from pyspark.ml.feature import VectorAssembler, StandardScaler
from pyspark.ml.regression import RandomForestRegressor
from pyspark.ml.evaluation import RegressionEvaluator
from pyspark.ml.tuning import ParamGridBuilder, CrossValidator
from datetime import datetime
from pyspark.sql import Row


# Глобальный список для логов
LOG_LINES = []

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    LOG_LINES.append(line)


def save_logs_to_s3(bucket_name, spark_session):
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = f"s3a://{bucket_name}/debug_logs/train_course_{timestamp}.log"
        
        log_df = spark_session.createDataFrame([Row(line=line) for line in LOG_LINES])
        log_df.write.mode("overwrite").text(log_path)
        log(f"Logs saved to: {log_path}")
    except Exception as e:
        print(f"WARNING: Could not save logs to S3: {e}")


def create_spark_session(s3_config=None):
    log("Начинаем создание Spark сессии")
    try:
        builder = SparkSession.builder.appName("QueryLatencyModel")

        if s3_config and all(k in s3_config for k in ['endpoint_url', 'access_key', 'secret_key']):
            log(f"Настраиваем S3 с endpoint_url: {s3_config['endpoint_url']}")
            builder = (builder
                .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
                .config("spark.hadoop.fs.s3a.endpoint", s3_config['endpoint_url'])
                .config("spark.hadoop.fs.s3a.access.key", s3_config['access_key'])
                .config("spark.hadoop.fs.s3a.secret.key", s3_config['secret_key'])
                .config("spark.hadoop.fs.s3a.path.style.access", "true")
                .config("spark.hadoop.fs.s3a.connection.ssl.enabled", "true")
            )

        log("Spark сессия успешно сконфигурирована")
        return builder
    except Exception as e:
        log(f"ERROR: Ошибка создания Spark сессии: {str(e)}")
        log(traceback.format_exc())
        raise


def load_data(spark, input_path, data_type="train"):
    log(f"Загрузка {data_type} данных: {input_path}")
    try:
        df = spark.read.parquet(input_path)
        log(f"{data_type} data loaded: {df.count()} rows")
        return df
    except Exception as e:
        log(f"ERROR: Ошибка загрузки {data_type} данных: {str(e)}")
        log(traceback.format_exc())
        raise


def train_model(train_df, test_df, model_type="rf", run_name="query_latency_model"):
    log(f"Начинаем обучение модели типа {model_type}, run_name: {run_name}")
    try:
        log("Создание преобразователя признаков")
        assembler = VectorAssembler(inputCols=["features"], outputCol="features_raw")
        scaler = StandardScaler(
            inputCol="features_raw",
            outputCol="features_scaled",
            withStd=True,
            withMean=True
        )

        log("Создание регрессора")
        regressor = RandomForestRegressor(
            labelCol="label",
            featuresCol="features_scaled",
            numTrees=50,
            maxDepth=10,
            seed=42
        )
        
        param_grid = (ParamGridBuilder()
            .addGrid(regressor.numTrees, [30, 50, 70])
            .addGrid(regressor.maxDepth, [5, 10, 15])
            .build())
        log(f"Сконфигурирована сетка параметров с {len(param_grid)} комбинациями")

        log("Создание пайплайна")
        pipeline = Pipeline(stages=[assembler, scaler, regressor])

        log("Создание оценщиков")
        evaluator_rmse = RegressionEvaluator(labelCol="label", metricName="rmse")
        evaluator_mae = RegressionEvaluator(labelCol="label", metricName="mae")
        evaluator_r2 = RegressionEvaluator(labelCol="label", metricName="r2")

        log("Создание кросс-валидатора")
        cv = CrossValidator(
            estimator=pipeline,
            estimatorParamMaps=param_grid,
            evaluator=evaluator_rmse,
            numFolds=3
        )

        log(f"Начинаем MLflow run: {run_name}")
        with mlflow.start_run(run_name=run_name) as run:
            run_id = run.info.run_id
            log(f"MLflow Run ID: {run_id}")

            log("Логируем параметры в MLflow")
            mlflow.log_param("numTrees_options", [30, 50, 70])
            mlflow.log_param("maxDepth_options", [5, 10, 15])

            log("Обучаем модель...")
            cv_model = cv.fit(train_df)
            log("Модель успешно обучена")
            best_model = cv_model.bestModel
            log("Получили лучшую модель")

            log("Делаем предсказания на тестовых данных")
            predictions = best_model.transform(test_df)
            log("Предсказания получены")

            log("Рассчитываем метрики")
            rmse = evaluator_rmse.evaluate(predictions)
            mae = evaluator_mae.evaluate(predictions)
            r2 = evaluator_r2.evaluate(predictions)

            log("Логируем метрики в MLflow")
            mlflow.log_metric("rmse", rmse)
            mlflow.log_metric("mae", mae)
            mlflow.log_metric("r2", r2)

            log("Получаем и логируем параметры лучшей модели")
            rf_model = best_model.stages[-1]
            try:
                num_trees = rf_model.getNumTrees
                max_depth = rf_model.getMaxDepth()
                log(f"numTrees={num_trees}, maxDepth={max_depth}")
                mlflow.log_param("best_numTrees", num_trees)
                mlflow.log_param("best_maxDepth", max_depth)
            except Exception as e:
                log(f"WARNING: Ошибка при получении параметров модели: {str(e)}")

            log("Сохраняем модель в MLflow")
            mlflow.spark.log_model(best_model, "model")

            log(f"RMSE: {rmse}")
            log(f"MAE: {mae}")
            log(f"R2: {r2}")

            metrics = {
                "run_id": run_id,
                "rmse": rmse,
                "mae": mae,
                "r2": r2
            }

            return best_model, metrics
    except Exception as e:
        log(f"ERROR: Ошибка обучения модели: {str(e)}")
        log(traceback.format_exc())
        raise


def save_model(model, output_path):
    log(f"Сохраняем модель в: {output_path}")
    try:
        model.write().overwrite().save(output_path)
        log(f"Model saved to: {output_path}")
    except Exception as e:
        log(f"ERROR: Ошибка сохранения модели: {str(e)}")
        log(traceback.format_exc())
        raise


def get_best_model_metrics(experiment_name):
    log(f"Получаем метрики лучшей модели для эксперимента '{experiment_name}'")
    client = MlflowClient()

    try:
        experiment = client.get_experiment_by_name(experiment_name)
        if not experiment:
            log(f"Эксперимент '{experiment_name}' не найден")
            return None
        log(f"Эксперимент найден, ID: {experiment.experiment_id}")
    except Exception as e:
        log(f"ERROR: Ошибка при получении эксперимента: {str(e)}")
        return None

    try:
        model_name = f"{experiment_name}_model"
        log(f"Ищем зарегистрированную модель '{model_name}'")

        try:
            registered_model = client.get_registered_model(model_name)
            log(f"Модель '{model_name}' зарегистрирована")
        except Exception as e:
            log(f"DEBUG: Модель '{model_name}' еще не зарегистрирована: {str(e)}")
            return None

        log("Получаем последние версии модели")
        model_versions = client.get_latest_versions(model_name)
        champion_version = None

        log(f"Найдено {len(model_versions)} версий модели")
        for version in model_versions:
            log(f"Проверяем версию {version.version}")
            if hasattr(version, 'aliases') and "champion" in version.aliases:
                log(f"Найден 'champion' в aliases: {version.aliases}")
                champion_version = version
                break
            elif hasattr(version, 'tags') and version.tags.get('alias') == "champion":
                log(f"Найден 'champion' в тегах: {version.tags}")
                champion_version = version
                break

        if not champion_version:
            log("Модель с алиасом 'champion' не найдена")
            return None

        champion_run_id = champion_version.run_id
        log(f"Run ID для 'champion': {champion_run_id}")

        run = client.get_run(champion_run_id)
        metrics = {
            "run_id": champion_run_id,
            "rmse": run.data.metrics.get("rmse"),
            "mae": run.data.metrics.get("mae"),
            "r2": run.data.metrics.get("r2")
        }

        log(f"Текущая лучшая модель (champion): версия {champion_version.version}")
        log(f"Метрики: RMSE={metrics['rmse']:.4f}, MAE={metrics['mae']:.4f}, R2={metrics['r2']:.4f}")

        return metrics
    except Exception as e:
        log(f"ERROR: Ошибка при получении лучшей модели: {str(e)}")
        return None


def compare_and_register_model(new_metrics, experiment_name):
    log(f"Сравниваем и регистрируем модель для эксперимента {experiment_name}")
    client = MlflowClient()

    best_metrics = get_best_model_metrics(experiment_name)
    model_name = f"{experiment_name}_model"
    log(f"Имя модели: {model_name}")

    try:
        client.get_registered_model(model_name)
        log(f"Модель '{model_name}' уже зарегистрирована")
    except Exception as e:
        log(f"Создаем новую модель: {str(e)}")
        client.create_registered_model(model_name)
        log(f"Создана новая регистрированная модель '{model_name}'")

    run_id = new_metrics["run_id"]
    model_uri = f"runs:/{run_id}/model"
    log(f"Регистрируем модель из {model_uri}")
    model_details = mlflow.register_model(model_uri, model_name)
    new_version = model_details.version
    log(f"Зарегистрирована новая версия: {new_version}")

    should_promote = False

    if not best_metrics:
        should_promote = True
        log("Это первая регистрируемая модель, она будет назначена как 'champion'")
    else:
        log(f"Сравниваем метрики - текущий RMSE: {best_metrics['rmse']}, новый RMSE: {new_metrics['rmse']}")
        if new_metrics["rmse"] < best_metrics["rmse"]:
            should_promote = True
            improvement = (best_metrics["rmse"] - new_metrics["rmse"]) / best_metrics["rmse"] * 100
            log(f"Новая модель лучше на {improvement:.2f}% по RMSE. Установка в качестве 'champion'")
        else:
            log(f"Новая модель не превосходит текущую 'champion' модель по RMSE")

    if should_promote:
        try:
            log("Пытаемся установить алиас 'champion'")
            if hasattr(client, 'set_registered_model_alias'):
                log("Используем set_registered_model_alias")
                client.set_registered_model_alias(model_name, "champion", new_version)
            else:
                log("Используем set_model_version_tag")
                client.set_model_version_tag(model_name, new_version, "alias", "champion")
        except Exception as e:
            log(f"ERROR: Ошибка установки алиаса 'champion': {str(e)}")
            log("Используем set_model_version_tag (запасной вариант)")
            client.set_model_version_tag(model_name, new_version, "alias", "champion")

        log(f"Версия {new_version} модели '{model_name}' установлена как 'champion'")
        return True

    try:
        log("Пытаемся установить алиас 'challenger'")
        if hasattr(client, 'set_registered_model_alias'):
            log("Используем set_registered_model_alias")
            client.set_registered_model_alias(model_name, "challenger", new_version)
        else:
            log("Используем set_model_version_tag")
            client.set_model_version_tag(model_name, new_version, "alias", "challenger")
    except Exception as e:
        log(f"ERROR: Ошибка установки алиаса 'challenger': {str(e)}")
        log("Используем set_model_version_tag (запасной вариант)")
        client.set_model_version_tag(model_name, new_version, "alias", "challenger")

    log(f"Версия {new_version} модели '{model_name}' установлена как 'challenger'")
    return False


def main():
    global LOG_LINES
    log("train_course.py started")
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-train", required=True)
    parser.add_argument("--input-test", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-type", default="rf")
    parser.add_argument("--tracking-uri")
    parser.add_argument("--experiment-name", default="query_latency_prediction")
    parser.add_argument("--auto-register", action="store_true")
    parser.add_argument("--run-name")
    parser.add_argument("--s3-endpoint-url")
    parser.add_argument("--s3-access-key")
    parser.add_argument("--s3-secret-key")
    parser.add_argument("--s3-bucket-name")
    
    args = parser.parse_args()
    log(f"Аргументы: {args}")
    
    os.environ['GIT_PYTHON_REFRESH'] = 'quiet'
    
    s3_config = None
    if args.s3_endpoint_url and args.s3_access_key and args.s3_secret_key:
        s3_config = {
            'endpoint_url': args.s3_endpoint_url,
            'access_key': args.s3_access_key,
            'secret_key': args.s3_secret_key
        }
        os.environ['AWS_ACCESS_KEY_ID'] = args.s3_access_key
        os.environ['AWS_SECRET_ACCESS_KEY'] = args.s3_secret_key
        os.environ['MLFLOW_S3_ENDPOINT_URL'] = args.s3_endpoint_url
        log("S3 config set")
    
    if args.tracking_uri:
        log(f"Устанавливаем MLflow tracking URI: {args.tracking_uri}")
        mlflow.set_tracking_uri(args.tracking_uri)
    
    log(f"Устанавливаем MLflow эксперимент: {args.experiment_name}")
    mlflow.set_experiment(args.experiment_name)
    
    log("Создаем Spark сессию")
    spark = create_spark_session(s3_config).getOrCreate()
    log("Spark сессия создана")
    
    try:
        train_df = load_data(spark, args.input_train, "train")
        test_df = load_data(spark, args.input_test, "test")
        
        run_name = args.run_name or f"query_latency_training_{args.model_type}"
        
        log("Обучаем модель")
        model, metrics = train_model(train_df, test_df, args.model_type, run_name)
        
        log("Сохраняем модель")
        save_model(model, args.output)
        
        if args.auto_register:
            log("Сравниваем и регистрируем модель")
            compare_and_register_model(metrics, args.experiment_name)
        
        log("Training completed successfully!")
        
    except Exception as e:
        log(f"ERROR: Ошибка во время обучения: {str(e)}")
        log(traceback.format_exc())
        sys.exit(1)
    finally:
        if args.s3_bucket_name:
            save_logs_to_s3(args.s3_bucket_name, spark)
        log("Останавливаем Spark сессию")
        spark.stop()
        log("Скрипт завершен")


if __name__ == "__main__":
    main()