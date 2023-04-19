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
        input = {"h3_cells": [1, 2]}
        request = Mock(method="POST", get_json=Mock(return_value=input), args=input)
        cell_limit = 1

        with patch("elevations_api.main.CELL_LIMIT", cell_limit):
            response = get_or_request_elevations(request)

        self.assertEqual(response, (f"Only {cell_limit} cells can be sent per request.", 400))

    def test_all_cells_available(self):
        """Test that, when the input cells all already have elevations for them in the database, the response just
        contains their elevations.
        """
        input = {"h3_cells": [1, 2]}
        request = Mock(method="POST", get_json=Mock(return_value=input), args=input)
        mock_elevations = {1: 32.1, 2: 59}

        with patch("elevations_api.main._get_available_elevations_from_database", return_value=mock_elevations):
            # Mock `jsonify` to avoid needing Flask app context or test app.
            with patch("elevations_api.main.jsonify") as mock_jsonify:
                get_or_request_elevations(request)

        mock_jsonify.assert_called_with({"elevations": mock_elevations})
