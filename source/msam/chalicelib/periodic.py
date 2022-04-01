# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
This file contains helper functions for updating the cache.
"""

import os
import json
import re
import time
from datetime import datetime, timedelta

import boto3
from botocore.exceptions import ClientError
from botocore.config import Config
from boto3.dynamodb.conditions import Key

import chalicelib.settings as msam_settings
from chalicelib import cache
import chalicelib.cloudwatch as cloudwatch_data
import chalicelib.connections as connection_cache
import chalicelib.nodes as node_cache
from chalicelib import tags

import requests

import defusedxml.ElementTree as ET

# table names generated by CloudFormation
ALARMS_TABLE_NAME = os.environ["ALARMS_TABLE_NAME"]
CONTENT_TABLE_NAME = os.environ["CONTENT_TABLE_NAME"]

# user-agent config
SOLUTION_ID = os.environ['SOLUTION_ID']
USER_AGENT_EXTRA = {"user_agent_extra": SOLUTION_ID}
MSAM_BOTO3_CONFIG = Config(**USER_AGENT_EXTRA)
VERSION = os.environ['VERSION']

SSM_LOG_GROUP_NAME = "MSAM/SSMRunCommand"

METRICS_NAMESPACE = "MSAM"
METRICS_NAME = "Resource Count"
METRICS_ENDPOINT = 'https://metrics.awssolutionsbuilder.com/generic'

UUID_RE = re.compile(
    ('^[0-9A-F]{8}-[0-9A-F]{4}-4[0-9A-F]{3}-[89AB][0-9A-F]{3}-[0-9A-F]{12}$'),
    re.IGNORECASE)

MONITORED_SERVICES = [
    "medialive-input", "medialive-channel", "medialive-multiplex",
    "mediapackage-channel", "mediapackage-origin-endpoint",
    "mediastore-container", "speke-keyserver", "mediaconnect-flow",
    "mediatailor-configuration", "ec2-instances", "link-devices",
    "ssm-managed-instances", "s3", "cloudfront-distribution"
]


def update_alarms():
    """
    Entry point for the CloudWatch scheduled task to update subscribed alarm state.
    """
    try:
        print("update alarms")
        alarm_groups = {}
        # group everything by region
        for alarm in cloudwatch_data.all_subscribed_alarms():
            region_name = alarm["Region"]
            alarm_name = alarm["AlarmName"]
            if region_name not in alarm_groups:
                alarm_groups[region_name] = []
            alarm_groups[region_name].append(alarm_name)
        print(alarm_groups)
        # update each grouped list for a region
        for region_name, alarm_names in alarm_groups:
            cloudwatch_data.update_alarms(region_name, alarm_names)
    except ClientError as error:
        print(error)
    return True


def update_connections():
    """
    Entry point for the CloudWatch scheduled task to discover and cache services.
    """
    try:
        connection_cache.update_connection_ddb_items()
    except ClientError as error:
        print(error)
    return True


def update_nodes():
    """
    This function is responsible for updating nodes for
    one region, or the global services.
    """
    return update_nodes_generic(
        update_global_func=node_cache.update_global_ddb_items,
        update_regional_func=node_cache.update_regional_ddb_items,
        settings_key="cache-next-region")


def update_ssm_nodes():
    """
    This function is responsible for updating SSM nodes
    """
    def skip():
        print("skipping global region")

    return update_nodes_generic(
        update_global_func=skip,
        update_regional_func=node_cache.update_regional_ssm_ddb_items,
        settings_key="ssm-cache-next-region")


def update_nodes_generic(update_global_func, update_regional_func,
                         settings_key):
    """
    Entry point for the CloudWatch scheduled task to discover and cache services.
    """
    try:
        inventory_regions_key = "inventory-regions"
        inventory_regions = msam_settings.get_setting(inventory_regions_key)
        if inventory_regions is None:
            inventory_regions = []
        inventory_regions.sort()
        # get the next region to process
        next_region = msam_settings.get_setting(settings_key)
        # start at the beginning if no previous setting
        if next_region is None and len(inventory_regions):
            next_region = inventory_regions[0]
        region_name = next_region
        # proceed only if we have a region
        if not region_name is None:
            # store the region for the next invocation
            try:
                # use two copies in case we roll off the end
                expanded = inventory_regions + inventory_regions
                position = expanded.index(region_name)
                # process global after the end of the region list
                if position >= 0:
                    next_region = expanded[position + 1]
                else:
                    next_region = expanded[0]
            except (IndexError, ValueError):
                # start over if we don't recognize the region, ex. global
                next_region = expanded[0]
            # store it
            msam_settings.put_setting(settings_key, next_region)
            # update the region
            print(f"updating nodes for region {region_name}")
            if region_name == "global":
                update_global_func()
            else:
                update_regional_func(region_name)
    except ClientError as error:
        print(error)
    return region_name


def update_from_tags():
    """
    Updates MSAM diagrams and tiles from tags on cloud resources. Check for MSAM-Diagram and MSAM-Tile tags.
    """
    tags.update_diagrams()
    tags.update_tiles()


def ssm_run_command():
    """
    Runs all applicable SSM document commands on a given managed instance.
    """
    try:
        table_name = CONTENT_TABLE_NAME
        ssm_client = boto3.client('ssm', config=MSAM_BOTO3_CONFIG)
        db_resource = boto3.resource('dynamodb', config=MSAM_BOTO3_CONFIG)
        db_table = db_resource.Table(table_name)
        instance_ids = {}
        # get all the managed instances from the DB with tag MSAM-NodeType
        response = db_table.query(
            IndexName="ServiceRegionIndex",
            KeyConditionExpression=Key("service").eq("ssm-managed-instance"),
            FilterExpression="contains(#data, :tagname)",
            ExpressionAttributeNames={"#data": "data"},
            ExpressionAttributeValues={":tagname": "MSAM-NodeType"})
        items = response.get("Items", [])
        while "LastEvaluatedKey" in response:
            response = db_table.query(
                IndexName="ServiceRegionIndex",
                KeyConditionExpression=Key("service").eq(
                    "ssm-managed-instance"),
                FilterExpression="contains(#data, :tagname)",
                ExpressionAttributeNames={"#data": "data"},
                ExpressionAttributeValues={":tagname": "MSAM-NodeType"},
                ExclusiveStartKey=response['LastEvaluatedKey'])
            items.append(response.get("Items", []))

        for item in items:
            data = json.loads(item['data'])
            if "MSAM-NodeType" in data["Tags"]:
                instance_ids[data['Id']] = data['Tags']['MSAM-NodeType']

        # get all the SSM documents applicable to MSAM, filtering by MSAM-NodeType tag
        # When we support more than just ElementalLive, add to the list of values for MSAM-NodeType during filtering
        document_list = ssm_client.list_documents(
            Filters=[{
                'Key': 'tag:MSAM-NodeType',
                'Values': [
                    'ElementalLive',
                ]
            }, {
                'Key': 'Owner',
                'Values': ['Self']
            }])
        document_ids = document_list['DocumentIdentifiers']
        while "NextToken" in document_list:
            document_list = ssm_client.list_documents(
                Filters=[{
                    'Key': 'tag:MSAM-NodeType',
                    'Values': [
                        'ElementalLive',
                    ]
                }, {
                    'Key': 'Owner',
                    'Values': ['Self']
                }],
                NextToken=document_list["NextToken"])
            document_ids.append(document_list['DocumentIdentifiers'])

        document_names = {}
        for document in document_ids:
            if "Tags" in document:
                for tag in document["Tags"]:
                    if tag['Key'] == "MSAM-NodeType":
                        document_names[document["Name"]] = tag['Value']

        # loop over all instances and run applicable commands based on node type
        for instance_id, id_type in instance_ids.items():
            for name, doc_type in document_names.items():
                if id_type in doc_type:
                    # maybe eventually doc type could be comma-delimited string if doc applies to more than one type?
                    print(f"running command: {name} on {instance_id}")
                    try:
                        response = ssm_client.send_command(
                            InstanceIds=[
                                instance_id,
                            ],
                            DocumentName=name,
                            TimeoutSeconds=600,
                            Parameters={},
                            MaxConcurrency='50',
                            MaxErrors='0',
                            CloudWatchOutputConfig={
                                'CloudWatchLogGroupName': SSM_LOG_GROUP_NAME,
                                'CloudWatchOutputEnabled': True
                            })
                        print(response)
                    except ClientError as error:
                        print(error)
                        if error.response['Error'][
                                'Code'] == "InvalidInstanceId":
                            continue
    except ClientError as error:
        print(error)


def process_ssm_run_command(event):
    """
    Processes the results from running an SSM command on a managed instance.
    """
    event_dict = event.to_dict()
    instance_id = event_dict['detail']['instance-id']
    command_name = event_dict['detail']['document-name']
    command_status = event_dict['detail']['status']
    cw_client = boto3.client('cloudwatch', config=MSAM_BOTO3_CONFIG)
    log_client = boto3.client('logs', config=MSAM_BOTO3_CONFIG)
    dimension_name = "Instance ID"
    metric_name = command_name
    status = 0

    try:
        # test to make sure stream names are always of this format, esp if you create your own SSM document
        log_stream_name = event_dict['detail'][
            'command-id'] + "/" + instance_id + "/aws-runShellScript/stdout"

        response = log_client.get_log_events(
            logGroupName=SSM_LOG_GROUP_NAME,
            logStreamName=log_stream_name,
        )
        #print(response)
        if command_status == "Success":
            # process document name (command)
            if "MSAMElementalLiveStatus" in command_name:
                metric_name = "MSAMElementalLiveStatus"
                for log_event in response['events']:
                    if "running" in log_event['message']:
                        status = 1
                        break
            elif "MSAMSsmSystemStatus" in command_name:
                metric_name = "MSAMSsmSystemStatus"
                status = 1
            elif "MSAMElementalLiveActiveAlerts" in command_name:
                metric_name = "MSAMElementalLiveActiveAlerts"
                root = ET.fromstring(response['events'][0]['message'])
                status = len(list(root))
                if status == 1 and root[0].tag == "empty":
                    status = 0
            else:
                if "MSAMElementalLiveCompletedEvents" in command_name:
                    metric_name = "MSAMElementalLiveCompletedEvents"
                elif "MSAMElementalLiveErroredEvents" in command_name:
                    metric_name = "MSAMElementalLiveErroredEvents"
                elif "MSAMElementalLiveRunningEvents" in command_name:
                    metric_name = "MSAMElementalLiveRunningEvents"
                root = ET.fromstring(response['events'][0]['message'])
                status = len(root.findall("./live_event"))
        else:
            # for the elemental live status, the command itself returns a failure if process is not running at all
            # which is different than when a command fails to execute altogether
            if command_status == "Failed" and "MSAMElementalLiveStatus" in command_name:
                for log_event in response['events']:
                    if "Not Running" in log_event[
                            'message'] or "Active: failed" in log_event[
                                'message']:
                        metric_name = "MSAMElementalLiveStatus"
                        break
            else:
                # log if command has timed out or failed
                print(
                    f"SSM Command Status: Command {command_name} sent to instance {instance_id} has {command_status}"
                )
                # create a metric for it
                status = 1
                metric_name = "MSAMSsmCommand" + command_status

        cw_client.put_metric_data(Namespace=SSM_LOG_GROUP_NAME,
                                  MetricData=[{
                                      'MetricName':
                                      metric_name,
                                      'Dimensions': [
                                          {
                                              'Name': dimension_name,
                                              'Value': instance_id
                                          },
                                      ],
                                      "Value":
                                      status,
                                      "Unit":
                                      "Count"
                                  }])
    except ClientError as error:
        print(error)
        print(
            f"SSM Command Status: Command {command_name} sent to instance {instance_id} has status {command_status}"
        )
        print(f"Log stream name is {log_stream_name}")


def generate_metrics(stackname):
    """
    This function is responsible for generating a resource count
    of each service type in inventory. It puts the resulting
    data to metrics with dimensions in CloudWatch.
    """
    client = boto3.client('cloudwatch', config=MSAM_BOTO3_CONFIG)
    for resource_type in MONITORED_SERVICES:
        resources = cache.cached_by_service(resource_type)
        client.put_metric_data(Namespace=METRICS_NAMESPACE,
                               MetricData=[
                                   {
                                       'MetricName':
                                       METRICS_NAME,
                                       'Dimensions': [{
                                           'Name': 'Stack Name',
                                           'Value': stackname
                                       }, {
                                           'Name': 'Resource Type',
                                           'Value': resource_type
                                       }],
                                       'Value':
                                       len(resources),
                                       'Unit':
                                       'Count'
                                   },
                               ])


def report_metrics(stackname, hours):
    """
    This function is responsible for reporting anonymous resource counts.
    """
    cloudwatch = boto3.resource('cloudwatch', config=MSAM_BOTO3_CONFIG)
    uuid = msam_settings.get_setting('uuid')
    # verify the uuid format from settings
    if UUID_RE.match(uuid) is None:
        print("uuid in settings does not match required format")
    else:
        # get the metric
        metric = cloudwatch.Metric(METRICS_NAMESPACE, METRICS_NAME)
        # assemble the payload structure
        _, solution_id, _ = SOLUTION_ID.split("/")
        data = {
            "Solution": solution_id,
            "Version": VERSION,
            "UUID": uuid,
            "TimeStamp": str(datetime.fromtimestamp(int(time.time()))),
            "Data": {}
        }
        # get the max count for each resource type and add to payload
        for resource_type in MONITORED_SERVICES:
            response = metric.get_statistics(Dimensions=[{
                'Name': 'Stack Name',
                'Value': stackname
            }, {
                'Name': 'Resource Type',
                'Value': resource_type
            }],
                                             StartTime=datetime.now() -
                                             timedelta(hours=hours),
                                             EndTime=datetime.now(),
                                             Period=(hours * 3600),
                                             Statistics=['Maximum'])
            datapoints = response.get('Datapoints', [])
            if datapoints:
                data["Data"][resource_type] = int(datapoints[0]["Maximum"])
        print(json.dumps(data, default=str, indent=4))
        if data["Data"]:
            # send it
            response = requests.post(METRICS_ENDPOINT, json=data)
            print(f"POST status code = {response.status_code}")
        else:
            print("skipping POST because of empty data")
