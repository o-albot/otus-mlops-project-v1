"""
Data preprocessing script for query latency prediction
Reads CSV, cleans, scales, and splits into train/test
"""

import argparse
import logging
from datetime import datetime

from pyspark.sql import SparkSession
from pyspark.sql.functions import col, when, isnan, isnull, count, log, hour, dayofweek, month
from pyspark.ml.feature import StandardScaler, VectorAssembler
from pyspark.ml import Pipeline
from pyspark.sql.types import *

import mlflow
import mlflow.spark

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="Preprocess query latency data")
    parser.add_argument("--input", required=True, help="Input CSV file path in S3")
    parser.add_argument("--output-train", required=True, help="Output train parquet path")
    parser.add_argument("--output-test", required=True, help="Output test parquet path")
    parser.add_argument("--tracking-uri", required=True, help="MLflow tracking URI")
    parser.add_argument("--experiment-name", default="feature_pipeline", help="MLflow experiment name")
    parser.add_argument("--test-size", type=float, default=0.2, help="Test split ratio")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    return parser.parse_args()


def setup_spark_session():
    """Create Spark session"""
    spark = SparkSession.builder \
        .appName("QueryLatencyPreprocessing") \
        .config("spark.sql.adaptive.enabled", "true") \
        .config("spark.sql.adaptive.coalescePartitions.enabled", "true") \
        .getOrCreate()
    
    logger.info("Spark session created successfully")
    return spark


def load_data(spark, input_path):
    """Load CSV data from S3"""
    logger.info(f"Loading data from {input_path}")
    
    df = spark.read.option("header", "true") \
        .option("inferSchema", "true") \
        .csv(input_path)
    
    row_count = df.count()
    col_count = len(df.columns)
    logger.info(f"Loaded {row_count} rows and {col_count} columns")
    
    return df


def clean_data(df):
    """Clean data: handle missing values, remove duplicates"""
    logger.info("Starting data cleaning")
    
    # Drop duplicate rows
    initial_count = df.count()
    df = df.dropDuplicates()
    dup_removed = initial_count - df.count()
    logger.info(f"Removed {dup_removed} duplicate rows")
    
    # Handle missing values
    for col_name in df.columns:
        null_count = df.filter(col(col_name).isNull()).count()
        if null_count > 0:
            logger.info(f"Column {col_name} has {null_count} null values")
            
            # For numeric columns: fill with median
            if df.schema[col_name].dataType in [IntegerType, LongType, FloatType, DoubleType]:
                median_value = df.approxQuantile(col_name, [0.5], 0.01)[0]
                df = df.fillna(median_value, subset=[col_name])
                logger.info(f"Filled {col_name} with median {median_value}")
            else:
                # For string columns: fill with 'unknown'
                df = df.fillna('unknown', subset=[col_name])
                logger.info(f"Filled {col_name} with 'unknown'")
    
    return df


def create_features(df):
    """Create additional features for latency prediction"""
    logger.info("Creating additional features")
    
    # Check for timestamp columns
    timestamp_cols = [c for c in df.columns if 'time' in c.lower() or 'date' in c.lower() or 'timestamp' in c.lower()]
    if timestamp_cols:
        ts_col = timestamp_cols[0]
        df = df.withColumn("hour", hour(col(ts_col)))
        df = df.withColumn("day_of_week", dayofweek(col(ts_col)))
        df = df.withColumn("month", month(col(ts_col)))
        logger.info(f"Added time features from {ts_col}")
    
    # Log transformation for numeric columns (optional)
    numeric_cols = [c for c, t in df.dtypes if t in ['int', 'double', 'float', 'bigint']]
    for col_name in numeric_cols[:3]:  # Apply to first 3 numeric columns
        df = df.withColumn(f"log_{col_name}", when(col(col_name) > 0, log(col(col_name))).otherwise(0))
        logger.info(f"Added log transformation for {col_name}")
    
    return df


def scale_features(df, feature_cols, target_col):
    """Scale numeric features using StandardScaler"""
    logger.info(f"Scaling {len(feature_cols)} features")
    
    # Assemble features into a vector
    assembler = VectorAssembler(inputCols=feature_cols, outputCol="features_unscaled", handleInvalid="keep")
    df = assembler.transform(df)
    
    # Scale features
    scaler = StandardScaler(inputCol="features_unscaled", outputCol="features", 
                            withStd=True, withMean=True)
    scaler_model = scaler.fit(df)
    df = scaler_model.transform(df)
    
    # Keep only features and target
    df = df.select("features", col(target_col).alias("label"))
    
    logger.info("Feature scaling completed")
    return df


def split_data(df, test_size, seed):
    """Split data into train and test sets"""
    logger.info(f"Splitting data with test_size={test_size}, seed={seed}")
    
    train_df, test_df = df.randomSplit([1 - test_size, test_size], seed=seed)
    
    train_count = train_df.count()
    test_count = test_df.count()
    logger.info(f"Train size: {train_count}, Test size: {test_count}")
    
    return train_df, test_df


def log_to_mlflow(tracking_uri, experiment_name, metrics, params):
    """Log preprocessing metrics to MLflow"""
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment_name)
    
    with mlflow.start_run(run_name=f"preprocess_{datetime.now().strftime('%Y%m%d_%H%M%S')}"):
        for key, value in metrics.items():
            mlflow.log_metric(key, value)
        
        for key, value in params.items():
            mlflow.log_param(key, value)
        
        logger.info(f"Logged to MLflow: {metrics}")


def main():
    args = parse_args()
    
    # Setup Spark
    spark = setup_spark_session()
    
    try:
        # Load data
        df = load_data(spark, args.input)
        
        # Clean data
        df = clean_data(df)
        
        # Create features
        df = create_features(df)
        
        # Identify target column (adjust based on your data)
        target_col = "query_latency"  # или "is_fraud", в зависимости от данных
        
        # Check if target column exists
        if target_col not in df.columns:
            # Try alternative names
            possible_targets = ["label", "target", "latency", "query_latency_ms"]
            for t in possible_targets:
                if t in df.columns:
                    target_col = t
                    break
            else:
                logger.error(f"Target column not found. Available columns: {df.columns}")
                raise ValueError(f"Target column not found in data")
        
        logger.info(f"Using target column: {target_col}")
        
        # Identify feature columns (exclude target and non-numeric)
        exclude_cols = [target_col, "features_unscaled"]
        numeric_cols = [c for c, t in df.dtypes if t in ['int', 'double', 'float', 'bigint']]
        feature_cols = [c for c in numeric_cols if c not in exclude_cols]
        
        logger.info(f"Features: {feature_cols}")
        
        # Scale features
        df = scale_features(df, feature_cols, target_col)
        
        # Split data
        train_df, test_df = split_data(df, args.test_size, args.seed)
        
        # Save to S3
        logger.info(f"Saving train data to {args.output_train}")
        train_df.write.mode("overwrite").parquet(args.output_train)
        
        logger.info(f"Saving test data to {args.output_test}")
        test_df.write.mode("overwrite").parquet(args.output_test)
        
        # Log to MLflow
        metrics = {
            "train_size": train_df.count(),
            "test_size": test_df.count(),
            "total_rows": train_df.count() + test_df.count(),
            "num_features": len(feature_cols),
        }
        
        params = {
            "test_size": args.test_size,
            "seed": args.seed,
            "input_path": args.input,
            "target_column": target_col,
        }
        
        log_to_mlflow(args.tracking_uri, args.experiment_name, metrics, params)
        
        logger.info("Preprocessing completed successfully!")
        
    except Exception as e:
        logger.error(f"Error in preprocessing: {str(e)}")
        raise
    finally:
        spark.stop()


if __name__ == "__main__":
    main()