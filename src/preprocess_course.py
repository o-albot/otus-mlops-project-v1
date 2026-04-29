"""
Script: preprocess_course.py
Description: Data preprocessing for query latency prediction
"""

import os
import sys
import traceback
import argparse
from datetime import datetime
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, hour, dayofweek, month, to_timestamp, when
from pyspark.ml.feature import VectorAssembler, StandardScaler


def create_spark_session(s3_config=None):
    print("DEBUG: Создание Spark сессии")
    builder = SparkSession.builder.appName("QueryLatencyPreprocessing")
    
    if s3_config and all(k in s3_config for k in ['endpoint_url', 'access_key', 'secret_key']):
        print(f"DEBUG: Настраиваем S3 с endpoint_url: {s3_config['endpoint_url']}")
        builder = (builder
            .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
            .config("spark.hadoop.fs.s3a.endpoint", s3_config['endpoint_url'])
            .config("spark.hadoop.fs.s3a.access.key", s3_config['access_key'])
            .config("spark.hadoop.fs.s3a.secret.key", s3_config['secret_key'])
            .config("spark.hadoop.fs.s3a.path.style.access", "true"))
    
    return builder


def load_data(spark, input_path):
    print(f"DEBUG: Загрузка данных из: {input_path}")
    df = spark.read.csv(input_path, header=True, inferSchema=True)
    print(f"Всего записей: {df.count()}")
    train_df, test_df = df.randomSplit([0.8, 0.2], seed=42)
    print(f"Train: {train_df.count()}, Test: {test_df.count()}")
    return train_df, test_df


def prepare_features(train_df, test_df):
    print("DEBUG: Подготовка признаков")
    target_col = "query_latency"
    
    # Извлекаем временные признаки (числовые)
    if "timestamp" in train_df.columns:
        train_df = train_df.withColumn("hour", hour(to_timestamp(col("timestamp"))))
        train_df = train_df.withColumn("day_of_week", dayofweek(to_timestamp(col("timestamp"))))
        train_df = train_df.withColumn("month", month(to_timestamp(col("timestamp"))))
        test_df = test_df.withColumn("hour", hour(to_timestamp(col("timestamp"))))
        test_df = test_df.withColumn("day_of_week", dayofweek(to_timestamp(col("timestamp"))))
        test_df = test_df.withColumn("month", month(to_timestamp(col("timestamp"))))
        # Удаляем исходный timestamp
        train_df = train_df.drop("timestamp")
        test_df = test_df.drop("timestamp")
    
    # Дополнительные признаки
    train_df = train_df.withColumn("cpu_memory_product", col("cpu_utilization") * col("memory_utilization"))
    train_df = train_df.withColumn("load_per_core",
        when(col("cpu_utilization") > 0, col("system_load") / col("cpu_utilization")).otherwise(0))
    test_df = test_df.withColumn("cpu_memory_product", col("cpu_utilization") * col("memory_utilization"))
    test_df = test_df.withColumn("load_per_core",
        when(col("cpu_utilization") > 0, col("system_load") / col("cpu_utilization")).otherwise(0))
    
    # Исключаем целевую переменную из признаков
    feature_cols = [c for c in train_df.columns if c != target_col]
    
    print(f"Выбрано {len(feature_cols)} признаков: {feature_cols}")
    return train_df, test_df, feature_cols, target_col


def prepare_features_for_model(train_df, test_df, feature_cols, target_col):
    print("DEBUG: Сборка признаков в вектор")
    assembler = VectorAssembler(inputCols=feature_cols, outputCol="features_raw", handleInvalid="keep")
    scaler = StandardScaler(inputCol="features_raw", outputCol="features", withStd=True, withMean=True)
    
    train_df = assembler.transform(train_df)
    scaler_model = scaler.fit(train_df)
    train_df = scaler_model.transform(train_df)
    test_df = assembler.transform(test_df)
    test_df = scaler_model.transform(test_df)
    
    train_df = train_df.select("features", col(target_col).alias("label"))
    test_df = test_df.select("features", col(target_col).alias("label"))
    
    return train_df, test_df


def main():
    print("DEBUG: preprocess_course.py started")
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-train", required=True)
    parser.add_argument("--output-test", required=True)
    parser.add_argument("--tracking-uri")
    parser.add_argument("--experiment-name", default="feature_pipeline")
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--s3-endpoint-url")
    parser.add_argument("--s3-access-key")
    parser.add_argument("--s3-secret-key")
    
    args = parser.parse_args()
    print(f"DEBUG: Arguments: {args}")
    
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
    
    import mlflow
    if args.tracking_uri:
        mlflow.set_tracking_uri(args.tracking_uri)
    mlflow.set_experiment(args.experiment_name)
    
    spark = create_spark_session(s3_config).getOrCreate()
    
    try:
        train_df, test_df = load_data(spark, args.input)
        train_df, test_df, feature_cols, target_col = prepare_features(train_df, test_df)
        train_df, test_df = prepare_features_for_model(train_df, test_df, feature_cols, target_col)
        
        train_df.write.mode("overwrite").parquet(args.output_train)
        test_df.write.mode("overwrite").parquet(args.output_test)
        
        with mlflow.start_run(run_name=f"preprocess_{datetime.now().strftime('%Y%m%d_%H%M%S')}"):
            mlflow.log_metric("train_size", train_df.count())
            mlflow.log_metric("test_size", test_df.count())
            mlflow.log_param("num_features", len(feature_cols))
            mlflow.log_param("input_path", args.input)
        
        print("SUCCESS: preprocess_course.py completed")
        
    except Exception as e:
        print(f"ERROR: {str(e)}")
        print(traceback.format_exc())
        sys.exit(1)
    finally:
        spark.stop()
        print("DEBUG: Spark session stopped")


if __name__ == "__main__":
    main()