#!/usr/bin/env python3
"""
A/B тестирование моделей регрессии
"""
import sys
import os
import argparse
from datetime import datetime
from pyspark.sql import SparkSession
import numpy as np
from scipy.stats import ttest_ind

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--s3-endpoint-url", required=True)
parser.add_argument("--s3-access-key", required=True)
parser.add_argument("--s3-secret-key", required=True)
parser.add_argument("--tracking-uri", required=True)
parser.add_argument("--s3-bucket-name", required=True)
parser.add_argument("--experiment-name", default="query_latency_prediction")
args = parser.parse_args()

log_lines = []
def log(msg):
    print(msg, flush=True)
    log_lines.append(msg)

log("=== A/B ТЕСТИРОВАНИЕ МОДЕЛЕЙ РЕГРЕССИИ ===")

spark = SparkSession.builder.appName("ABTest").getOrCreate()

try:
    # Настройка окружения
    os.environ['MLFLOW_S3_ENDPOINT_URL'] = args.s3_endpoint_url
    os.environ['AWS_ACCESS_KEY_ID'] = args.s3_access_key
    os.environ['AWS_SECRET_ACCESS_KEY'] = args.s3_secret_key
    
    import mlflow
    import mlflow.spark
    from pyspark.ml.evaluation import RegressionEvaluator
    from pyspark.sql.functions import col, hour, dayofweek, month, to_timestamp, when
    from pyspark.ml.feature import VectorAssembler
    from mlflow.tracking import MlflowClient
    
    mlflow.set_tracking_uri(args.tracking_uri)
    
    # === 1. Загрузка и подготовка данных ===
    log("=== ШАГ 1: ЗАГРУЗКА ДАННЫХ ===")
    df = spark.read.csv(args.input, header=True, inferSchema=True)
    _, test_df = df.randomSplit([0.8, 0.2], seed=42)
    test_size = test_df.count()
    log(f"Тестовая выборка: {test_size} записей")
    
    # Подготовка признаков
    log("Подготовка признаков...")
    target_col = "query_latency"
    
    if "timestamp" in test_df.columns:
        test_df = test_df.withColumn("hour", hour(to_timestamp(col("timestamp"))))
        test_df = test_df.withColumn("day_of_week", dayofweek(to_timestamp(col("timestamp"))))
        test_df = test_df.withColumn("month", month(to_timestamp(col("timestamp"))))
        test_df = test_df.drop("timestamp")
    
    test_df = test_df.withColumn("cpu_memory_product", col("cpu_utilization") * col("memory_utilization"))
    test_df = test_df.withColumn("load_per_core",
        when(col("cpu_utilization") > 0, col("system_load") / col("cpu_utilization")).otherwise(0))
    
    feature_cols = [c for c in test_df.columns if c != target_col]
    assembler = VectorAssembler(inputCols=feature_cols, outputCol="features", handleInvalid="keep")
    test_df = assembler.transform(test_df)
    test_df = test_df.select("features", col(target_col).alias("label"))
    log(f"Подготовлено {len(feature_cols)} признаков")
    
    # === 2. Загрузка моделей ===
    log("=== ШАГ 2: ЗАГРУЗКА МОДЕЛЕЙ ===")
    model_name = f"{args.experiment_name}_model"
    
    # Загрузка champion
    champion_uri = f"models:/{model_name}@champion"
    log(f"Загрузка champion: {champion_uri}")
    champion_model = mlflow.spark.load_model(champion_uri)
    log("✓ Champion модель загружена")
    
    # Загрузка challenger (если есть)
    challenger_model = None
    try:
        challenger_uri = f"models:/{model_name}@challenger"
        log(f"Загрузка challenger: {challenger_uri}")
        challenger_model = mlflow.spark.load_model(challenger_uri)
        log("✓ Challenger модель загружена")
    except Exception as e:
        log(f"⚠ Challenger не найден: {str(e)}")
        log("Пропускаем A/B тест, нет модели для сравнения")
        # Сохраняем логи
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        from pyspark.sql import Row
        log_df = spark.createDataFrame([Row(line=line) for line in log_lines])
        log_df.write.mode("overwrite").text(f"s3a://{args.s3_bucket_name}/debug_logs/ab_test_course_{timestamp}")
        spark.stop()
        sys.exit(0)
    
    # === 3. Оценка метрик ===
    log("=== ШАГ 3: ОЦЕНКА МЕТРИК ===")
    evaluator_rmse = RegressionEvaluator(labelCol="label", metricName="rmse")
    evaluator_mae = RegressionEvaluator(labelCol="label", metricName="mae")
    evaluator_r2 = RegressionEvaluator(labelCol="label", metricName="r2")
    
    prod_pred = champion_model.transform(test_df)
    cand_pred = challenger_model.transform(test_df)
    
    prod_rmse = evaluator_rmse.evaluate(prod_pred)
    prod_mae = evaluator_mae.evaluate(prod_pred)
    prod_r2 = evaluator_r2.evaluate(prod_pred)
    
    cand_rmse = evaluator_rmse.evaluate(cand_pred)
    cand_mae = evaluator_mae.evaluate(cand_pred)
    cand_r2 = evaluator_r2.evaluate(cand_pred)
    
    log("Метрики моделей на тестовой выборке:")
    log(f"  Champion - RMSE: {prod_rmse:.4f} | MAE: {prod_mae:.4f} | R2: {prod_r2:.4f}")
    log(f"  Challenger - RMSE: {cand_rmse:.4f} | MAE: {cand_mae:.4f} | R2: {cand_r2:.4f}")
    
    # === 4. Принятие решения ===
    log("=== ШАГ 4: ПРИНЯТИЕ РЕШЕНИЯ ===")
    
    # Сравниваем по RMSE (чем меньше, тем лучше)
    if cand_rmse < prod_rmse:
        decision = "PROMOTE"
        improvement = ((prod_rmse - cand_rmse) / prod_rmse) * 100
        log(f"Challenger лучше champion на {improvement:.2f}% по RMSE")
    else:
        decision = "KEEP_CHAMPION"
        log("Challenger не превосходит champion")
    
    log(f"РЕШЕНИЕ: {decision}")
    
    # === 5. Логирование в MLflow ===
    log("=== ШАГ 5: ЛОГИРОВАНИЕ В MLFLOW ===")
    with mlflow.start_run(run_name=f"ab_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"):
        mlflow.log_metrics({
            "champion_rmse": prod_rmse,
            "champion_mae": prod_mae,
            "champion_r2": prod_r2,
            "challenger_rmse": cand_rmse,
            "challenger_mae": cand_mae,
            "challenger_r2": cand_r2
        })
        mlflow.set_tag("ab_test_decision", decision)
        
        if decision == "PROMOTE":
            client = MlflowClient()
            # Получаем версию challenger
            versions = client.get_latest_versions(model_name)
            for version in versions:
                if hasattr(version, 'aliases') and "challenger" in version.aliases:
                    client.set_registered_model_alias(model_name, "champion", version.version)
                    log(f"✓ Версия {version.version} продвинута в champion")
                    break
    
    log("✓✓✓ A/B ТЕСТИРОВАНИЕ ЗАВЕРШЕНО УСПЕШНО!")
    
except Exception as e:
    log(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {type(e).__name__}")
    log(f"Сообщение: {str(e)}")
    import traceback
    log(traceback.format_exc())
    raise
finally:
    # Сохранение логов в S3
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    from pyspark.sql import Row
    log_df = spark.createDataFrame([Row(line=line) for line in log_lines])
    log_df.write.mode("overwrite").text(f"s3a://{args.s3_bucket_name}/debug_logs/ab_test_course_{timestamp}")
    log(f"Логи сохранены: debug_logs/ab_test_course_{timestamp}")
    spark.stop()