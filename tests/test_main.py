import time
import unittest
from unittest.mock import Mock, patch

from cachetools import TTLCache

from elevations_api.main import (
    SCHEMA_INFO_URL,
    SCHEMA_URI,
    _add_cells_to_ttl_cache,
    _get_available_elevations_from_database,
    get_or_request_elevations,
)


class TestMain(unittest.TestCase):
    def test_error_returned_if_request_method_is_not_post(self):
        """Test that an error response is returned if the request method is not `POST`."""
        request = Mock(method="GET")
        response = get_or_request_elevations(request)
        self.assertEqual(response, ("This endpoint only accepts POST requests.", 405))

    def test_error_returned_if_cell_limit_exceeded(self):
        """Test that an error response is returned if the number of cells in the request exceeds the cell limit."""
        input = {"h3_cells": [1, 2]}
        request = Mock(method="POST", get_json=Mock(return_value=input), args=input)
        cell_limit = 1

        with patch("elevations_api.main.CELL_LIMIT", cell_limit):
            response = get_or_request_elevations(request)

        self.assertEqual(response, ("Request for 2 cells rejected - only 1 cells can be sent per request.", 400))

    def test_error_returned_if_cells_are_invalid(self):
        """Test that an error response is returned if invalid H3 cells are requested."""
        input = {"h3_cells": [1, 630949280935159295]}
        request = Mock(method="POST", get_json=Mock(return_value=input), args=input)
        response = get_or_request_elevations(request)
        self.assertEqual(response, ("1 is not a valid H3 cell - aborting request.", 400))

    def test_all_cells_available(self):
        """Test that, when all the input cells already have elevations in the database, database population is not
        requested and the response just contains their elevations.
        """
        input = {"h3_cells": [630949280935159295, 630949280220393983]}
        request = Mock(method="POST", get_json=Mock(return_value=input), args=input)
        mock_elevations = {630949280935159295: 32.1, 630949280220393983: 59}

        with patch("elevations_api.main._get_available_elevations_from_database", return_value=mock_elevations):
            with patch("elevations_api.main._populate_database") as mock_populate_database:
                # Mock `jsonify` to avoid needing Flask app context or test app.
                with patch("elevations_api.main.jsonify") as mock_jsonify:
                    get_or_request_elevations(request)

        mock_jsonify.assert_called_with(
            {"schema_uri": SCHEMA_URI, "schema_info": SCHEMA_INFO_URL, "data": {"elevations": mock_elevations}}
        )

        mock_populate_database.assert_not_called()

    def test_all_cells_unavailable(self):
        """Test that, when all the input cells don't have elevations in the database, database population is requested
        and the response contains an empty elevations list and a `later` list of the input cells.
        """
        input = {"h3_cells": [630949280935159295, 630949280220393983]}
        request = Mock(method="POST", get_json=Mock(return_value=input), args=input)

        with patch("elevations_api.main._get_available_elevations_from_database", return_value={}):
            with patch("elevations_api.main._populate_database") as mock_populate_database:
                # Mock `jsonify` to avoid needing Flask app context or test app.
                with patch("elevations_api.main.jsonify") as mock_jsonify:
                    get_or_request_elevations(request)

        response = mock_jsonify.call_args.args[0]
        self.assertEqual(response["data"]["elevations"], {})
        self.assertEqual(set(response["data"]["later"]), {630949280935159295, 630949280220393983})
        mock_populate_database.assert_called_with({630949280935159295, 630949280220393983})

    def test_some_cells_unavailable(self):
        """Test that, when some of the input cells have their elevations in the database, database population is
        requested for those that don't and the response contains an elevations list for those that were available and a
        `later` list of the input cells that weren't.
        """
        input = {"h3_cells": [630949280935159295, 630949280220393983, 630949280220402687, 630949280220390399]}
        request = Mock(method="POST", get_json=Mock(return_value=input), args=input)
        mock_elevations = {630949280935159295: 32.1, 630949280220393983: 59}

        with patch("elevations_api.main._get_available_elevations_from_database", return_value=mock_elevations):
            with patch("elevations_api.main._populate_database") as mock_populate_database:
                # Mock `jsonify` to avoid needing Flask app context or test app.
                with patch("elevations_api.main.jsonify") as mock_jsonify:
                    get_or_request_elevations(request)

        response = mock_jsonify.call_args.args[0]
        self.assertEqual(response["data"]["elevations"], mock_elevations)
        self.assertEqual(set(response["data"]["later"]), {630949280220402687, 630949280220390399})
        mock_populate_database.assert_called_with({630949280220402687, 630949280220390399})

    def test_database_population_not_re_requested_if_cell_in_ttl_cache(self):
        """Test that database population is not re-requested for a cell if it's in the TTL cache."""
        data = {"h3_cells": [630949280935159295]}
        request = Mock(method="POST", get_json=Mock(return_value=data), args=data)

        _add_cells_to_ttl_cache(data["h3_cells"])

        with patch("elevations_api.main._get_available_elevations_from_database", return_value={}):
            with patch("elevations_api.main._populate_database") as mock_populate_database:
                # Mock `jsonify` to avoid needing Flask app context or test app.
                with patch("elevations_api.main.jsonify"):
                    get_or_request_elevations(request)

        mock_populate_database.assert_not_called()

    def test_database_population_is_re_requested_if_cell_in_ttl_cache_but_ttl_has_expired(self):
        """Test that database population is re-requested for a cell if it's in the TTL cache but its TTL has expired."""
        cell = 630949280935159295
        data = {"h3_cells": [cell]}
        request = Mock(method="POST", get_json=Mock(return_value=data), args=data)
        mock_cache = TTLCache(maxsize=1024, ttl=0.1)

        with patch("elevations_api.main.recently_requested_for_database_population_cache", mock_cache):
            _add_cells_to_ttl_cache(data["h3_cells"])
            self.assertIn(cell, mock_cache)
            time.sleep(0.1)
            self.assertNotIn(cell, mock_cache)

            with patch("elevations_api.main._get_available_elevations_from_database", return_value={}):
                with patch("elevations_api.main._populate_database") as mock_populate_database:
                    # Mock `jsonify` to avoid needing Flask app context or test app.
                    with patch("elevations_api.main.jsonify"):
                        get_or_request_elevations(request)

        mock_populate_database.assert_called()

    def test_get_available_elevations_from_database(self):
        """Test that a correct query is formed when getting elevations from the database."""
        with patch("neo4j._sync.driver.Session") as mock_session:
            _get_available_elevations_from_database({630949280935159295, 630949280220393983})

        self.assertEqual(
            mock_session.mock_calls[2][1][0].strip().split("\n"),
            [
                "MATCH (c:Cell)-[:HAS_ELEVATION]->(e:Elevation)",
                "    WHERE c.index = 630949280220393983 or c.index = 630949280935159295",
                "    RETURN c.index, e.value",
            ],
        )
