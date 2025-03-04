"""
This module provides unit tests for the content.py module.
"""

# pylint: disable=C0415,W0201

import unittest
from unittest.mock import patch


@patch('os.environ')
@patch('boto3.resource')
@patch('boto3.client')
class TestContent(unittest.TestCase):
    """
    This class extends TestCase with testing functions
    """
    def test_put_ddb_items(self, patched_env, patched_resource,
                                       patched_client):
        """
        Test the put_ddb_item function
        """
        from chalicelib import content
        content.put_ddb_items("us-east-1")
