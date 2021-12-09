# Copyright 2018 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
This file contains helper functions related to the content DynamoDB table.
"""

import os

import boto3
from botocore.config import Config

# TTL provided via CloudFormation
CACHE_ITEM_TTL = int(os.environ["CACHE_ITEM_TTL"])

# table names generated by CloudFormation
CONTENT_TABLE_NAME = os.environ["CONTENT_TABLE_NAME"]

# user-agent config
SOLUTION_ID = os.environ['SOLUTION_ID']
USER_AGENT_EXTRA = {"user_agent_extra": SOLUTION_ID}
MSAM_BOTO3_CONFIG = Config(**USER_AGENT_EXTRA)

def put_ddb_items(items):
    """
    Add a list of cache items to the content (cache) DynamoDB table.
    """
    ddb_table_name = CONTENT_TABLE_NAME
    # shared resource and table
    ddb_resource = boto3.resource('dynamodb', config=MSAM_BOTO3_CONFIG)
    ddb_table = ddb_resource.Table(ddb_table_name)
    for item in items:
        ddb_table.put_item(Item=item)
    return True
