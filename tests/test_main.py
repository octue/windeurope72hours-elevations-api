import unittest
from unittest.mock import Mock, patch

from elevations_api.main import get_or_request_elevations


class TestMain(unittest.TestCase):
    def test_error_returned_if_request_method_is_not_post(self):
        """Test that an error response is returned if the request method is not `POST`."""
        request = Mock(method="GET")
        response = get_or_request_elevations(request)
        self.assertEqual(response, ("This endpoint only accepts POST requests.", 405))

    def test_error_response_raised_if_cell_limit_exceeded(self):
        """Test that an error response is returned if the number of cells in the request exceeds the cell limit."""
        data = {"h3_cells": [1, 2]}
        request = Mock(method="POST", get_json=Mock(return_value=data), args=data)
        cell_limit = 1

        with patch("elevations_api.main.CELL_LIMIT", cell_limit):
            response = get_or_request_elevations(request)

        self.assertEqual(response, (f"Only {cell_limit} cells can be sent per request.", 400))
