"""
Script: inference_course.py
Description: Batch inference using champion model from MLflow Registry
"""

import os
import sys
import argparse
from datetime import datetime
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, hour, dayofweek, month, to_timestamp, when
from pyspark.ml.feature import VectorAssembler
from pyspark.sql import Row


LOG_LINES = []

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    LOG_LINES.append(line)


def save_logs_to_s3(bucket_name, spark_session):
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = f"s3a://{bucket_name}/debug_logs/inference_course_{timestamp}.log"
        
        log_df = spark_session.createDataFrame([Row(line=line) for line in LOG_LINES])
        log_df.write.mode("overwrite").text(log_path)
        log(f"Logs saved to: {log_path}")
    except Exception as e:
        print(f"WARNING: Could not save logs to S3: {e}")


parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True, help="Input data path (CSV)")
parser.add_argument("--output", required=True, help="Output predictions path")
parser.add_argument("--model-name", required=True, help="MLflow model name")
parser.add_argument("--tracking-uri", required=True, help="MLflow tracking URI")
parser.add_argument("--s3-endpoint-url", required=True)
parser.add_argument("--s3-access-key", required=True)
parser.add_argument("--s3-secret-key", required=True)
parser.add_argument("--s3-bucket-name", required=True)
args = parser.parse_args()

spark = SparkSession.builder.appName("BatchInference").getOrCreate()

os.environ['MLFLOW_S3_ENDPOINT_URL'] = args.s3_endpoint_url
os.environ['AWS_ACCESS_KEY_ID'] = args.s3_access_key
os.environ['AWS_SECRET_ACCESS_KEY'] = args.s3_secret_key

import mlflow
import mlflow.spark

mlflow.set_tracking_uri(args.tracking_uri)

log("=== БАТЧ-ИНФЕРЕНС ===")

try:
    # Загрузка champion модели
    model_uri = f"models:/{args.model_name}@champion"
    log(f"Загрузка champion модели: {model_uri}")
    model = mlflow.spark.load_model(model_uri)
    log("✓ Модель загружена")

    # Загрузка данных
    log(f"Загрузка данных: {args.input}")
    df = spark.read.csv(args.input, header=True, inferSchema=True)
    log(f"Записей: {df.count()}")

    # Подготовка признаков
    log("Подготовка признаков...")
    target_col = "query_latency"

    if "timestamp" in df.columns:
        df = df.withColumn("hour", hour(to_timestamp(col("timestamp"))))
        df = df.withColumn("day_of_week", dayofweek(to_timestamp(col("timestamp"))))
        df = df.withColumn("month", month(to_timestamp(col("timestamp"))))
        df = df.drop("timestamp")

    df = df.withColumn("cpu_memory_product", col("cpu_utilization") * col("memory_utilization"))
    df = df.withColumn("load_per_core",
        when(col("cpu_utilization") > 0, col("system_load") / col("cpu_utilization")).otherwise(0))

    feature_cols = [c for c in df.columns if c != target_col]
    assembler = VectorAssembler(inputCols=feature_cols, outputCol="features", handleInvalid="keep")
    df = assembler.transform(df)

    # Предсказания
    log("Выполнение предсказаний...")
    predictions = model.transform(df)

    # Сохраняем результат
    output_df = predictions.select("features", "prediction")
    output_df.write.mode("overwrite").parquet(args.output)
    log(f"✓ Предсказания сохранены: {args.output}")

except Exception as e:
    log(f"ERROR: {str(e)}")
    import traceback
    log(traceback.format_exc())
    raise
finally:
    if args.s3_bucket_name:
        save_logs_to_s3(args.s3_bucket_name, spark)
    spark.stop()
    log("Инференс завершён")