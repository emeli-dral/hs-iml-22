import pandas as pd

from sklearn.metrics import mean_squared_error
from datetime import timedelta

import xgboost as xgb

from prefect import flow, task
from prefect.task_runners import SequentialTaskRunner

import mlflow

@task
def looad_data(path):
    data = pd.read_parquet(path)
    data.lpep_dropoff_datetime = pd.to_datetime(data.lpep_dropoff_datetime)
    data.lpep_pickup_datetime = pd.to_datetime(data.lpep_pickup_datetime)

    data['duration'] = data.lpep_dropoff_datetime - data.lpep_pickup_datetime
    data.duration = data.duration.apply(lambda td: td.total_seconds() / 60)
    data = data[(data.duration >= 1) & (data.duration <= 60)]
    
    data['PULocationID'].astype(str, copy=False)
    data['DOLocationID'].astype(str, copy=False)
    return data

@task(retries=3)
def generate_datasets(train_frame, val_frame):
    num_features = ['trip_distance', 'extra', 'fare_amount']
    cat_features = ['PULocationID', 'DOLocationID']

    X_train = train_frame[num_features + cat_features]
    X_val = val_frame[num_features + cat_features] 

    y_train = train_frame['duration']
    y_val = val_frame['duration'] 
    return X_train, X_val, y_train, y_val

@task
def train_model(X_train, y_train, X_val, y_val):
    mlflow.set_tracking_uri("sqlite:///mlflow.db")
    mlflow.set_experiment("nyc-data-experiment")

    best_params = {
        'max_depth': 5,
        'min_child': 19.345653147972058,
        'objective': 'reg:linear',
        'reg_alpha': 0.031009193638004067,
        'reg_lambda': 0.013053945835415701,
        'seed': 111
    }

    mlflow.log_params(best_params)
    mlflow.log_param('train_data_name', 'green_tripdata_2021-01.parquet')
    mlflow.log_param('validation_data_name', 'green_tripdata_2021-02.parquet')
    mlflow.set_tag("workspace", "prefect")

    train = xgb.DMatrix(X_train, label=y_train)
    validation = xgb.DMatrix(X_val, label=y_val)

    booster = xgb.train(
        params = best_params,
        dtrain = train,
        evals = [(validation, "validation")],
        num_boost_round = 500,
        early_stopping_rounds = 50,
    )

    y_preds = booster.predict(validation)
    rmse = mean_squared_error(y_preds, y_val, squared=False)
    mlflow.log_metric("rmse", rmse)

    mlflow.xgboost.log_model(booster, artifact_path='mlflow_models')

    return booster

@task
def estimate_quality(model, X_val, y_val):
    validation = xgb.DMatrix(X_val, label=y_val)
    y_pred = model.predict(validation)
    return mean_squared_error(y_pred, y_val, squared=False)

@flow(task_runner=SequentialTaskRunner())
def nyc_duration_flow():
    train_frame = looad_data('green_tripdata_2021-01.parquet')
    val_frame = looad_data('green_tripdata_2021-02.parquet')
    X_train, X_val, y_train, y_val = generate_datasets(train_frame, val_frame).result()
    model = train_model(X_train, y_train, X_val, y_val)
    rmse = estimate_quality(model, X_val, y_val)

nyc_duration_flow()
