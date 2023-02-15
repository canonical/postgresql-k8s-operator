#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import os
import uuid

import boto3 as boto3
import pytest as pytest
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import construct_endpoint

AWS = "AWS"
GCP = "GCP"


@pytest.fixture()
async def cloud_configs(ops_test: OpsTest) -> None:
    # Define some configurations and credentials.
    configs = {
        AWS: {
            "endpoint": "https://s3.amazonaws.com",
            "bucket": "canonical-postgres",
            "path": f"/{uuid.uuid1()}",
            "region": "us-east-2",
        },
        GCP: {
            "endpoint": "https://storage.googleapis.com",
            "bucket": "data-charms-testing",
            "path": f"/postgresql-k8s/{uuid.uuid1()}",
            "region": "",
        },
    }
    credentials = {
        AWS: {
            "access-key": os.environ.get("AWS_ACCESS_KEY"),
            "secret-key": os.environ.get("AWS_SECRET_KEY"),
        },
        GCP: {
            "access-key": os.environ.get("GCP_ACCESS_KEY"),
            "secret-key": os.environ.get("GCP_SECRET_KEY"),
        },
    }
    yield configs, credentials
    # Delete the previously created objects.
    for cloud, config in configs.items():
        session = boto3.session.Session(
            aws_access_key_id=credentials[cloud]["access-key"],
            aws_secret_access_key=credentials[cloud]["secret-key"],
            region_name=config["region"],
        )
        s3 = session.resource(
            "s3", endpoint_url=construct_endpoint(config["endpoint"], config["region"])
        )
        bucket = s3.Bucket(config["bucket"])
        # GCS doesn't support batch delete operation, so delete the objects one by one.
        for bucket_object in bucket.objects.filter(Prefix=config["path"].lstrip("/")):
            bucket_object.delete()
