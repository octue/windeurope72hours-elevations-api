import time
import unittest
from unittest.mock import Mock, patch

from cachetools import TTLCache

from elevations_api.main import (
    MAXIMUM_RESOLUTION,
    MINIMUM_RESOLUTION,
    OUTPUT_SCHEMA_INFO_URL,
    OUTPUT_SCHEMA_URI,
    SINGLE_REQUEST_CELL_LIMIT,
    _add_cells_to_ttl_cache,
    _get_available_elevations_from_database,
    get_or_request_elevations,
)


class TestErrors(unittest.TestCase):
    def test_error_returned_if_request_method_is_not_post(self):
        """Test that an error response is returned if the request method is not `POST`."""
        request = Mock(method="GET")
        response = get_or_request_elevations(request)

        self.assertEqual(
            response,
            ("This endpoint only accepts POST or OPTIONS requests.", 405, {"Access-Control-Allow-Origin": "*"}),
        )

    def test_error_returned_if_input_data_is_incorrectly_formatted(self):
        """Test that an error is returned if the input data in incorrectly formatted."""
        for data in [
            [],
            [630949280935159295],
            {"incorrect": [630949280935159295]},
            {"resolution": 11},
        ]:
            with self.subTest(data=data):
                request = Mock(method="POST", get_json=Mock(return_value=data), args=data)
                response = get_or_request_elevations(request)

                self.assertEqual(response[1], 400)

    def test_error_returned_if_zero_cells_requested(self):
        """Test that an error is returned if zero cells are requested."""
        data = {"h3_cells": []}
        request = Mock(method="POST", get_json=Mock(return_value=data), args=data)
        response = get_or_request_elevations(request)
        self.assertEqual(response[1], 400)

    def test_error_returned_if_cell_limit_exceeded(self):
        """Test that an error response is returned if the number of cells in the request exceeds the cell limit."""
        data = {"h3_cells": list(range(SINGLE_REQUEST_CELL_LIMIT + 1))}
        request = Mock(method="POST", get_json=Mock(return_value=data), args=data)
        response = get_or_request_elevations(request)

        self.assertEqual(
            response,
            (
                f"Request for 16 cells rejected - only {SINGLE_REQUEST_CELL_LIMIT} cells can be sent per request.",
                400,
                {"Access-Control-Allow-Origin": "*"},
            ),
        )

    def test_error_returned_if_cells_are_invalid(self):
        """Test that an error response is returned if invalid H3 cells are requested."""
        data = {"h3_cells": [1, 630949280935159295]}
        request = Mock(method="POST", get_json=Mock(return_value=data), args=data)
        response = get_or_request_elevations(request)
        self.assertEqual(
            response,
            ("1 is not a valid H3 cell - aborting request.", 400, {"Access-Control-Allow-Origin": "*"}),
        )

    def test_error_raised_if_coordinates_invalid(self):
        """Test that an error is raised if the coordinates are invalid."""
        for invalid_coordinates in ([], [[]], [[1, 2], [3]]):
            with self.subTest(coordinates=invalid_coordinates):
                data = {"coordinates": invalid_coordinates}
                request = Mock(method="POST", get_json=Mock(return_value=data), args=data)
                response = get_or_request_elevations(request)
                self.assertEqual(response[1], 400)

    def test_error_raised_if_polygon_invalid(self):
        """Test that an error is raised if the polygon coordinates are invalid."""
        for invalid_coordinates in ([], [[]], [[1, 2], [3]]):
            with self.subTest(coordinates=invalid_coordinates):
                data = {"polygon": invalid_coordinates}
                request = Mock(method="POST", get_json=Mock(return_value=data), args=data)
                response = get_or_request_elevations(request)
                self.assertEqual(response[1], 400)

    def test_error_raised_if_polygon_contains_no_cells(self):
        """Test that an error is raised if the given polygon doesn't contain any cells."""
        data = {
            "polygon": [[54.53097, 5.96836], [54.53075, 5.96435], [54.52926, 5.96432], [54.52903, 5.96888]],
            "resolution": 8,
        }

        request = Mock(method="POST", get_json=Mock(return_value=data), args=data)
        response = get_or_request_elevations(request)
        self.assertEqual(response[0], "Request for zero cells rejected.")
        self.assertEqual(response[1], 400)

    def test_error_returned_if_resolution_outside_allowed_range(self):
        """Test that an error response is returned if the requested resolution is above the maximum resolution or below
        the minimum resolution.
        """
        for resolution in (1, 13):
            with self.subTest(resolution=resolution):
                data = {"coordinates": [[54.53097, 5.96836]], "resolution": resolution}
                request = Mock(method="POST", get_json=Mock(return_value=data), args=data)
                response = get_or_request_elevations(request)
                self.assertEqual(
                    response,
                    (
                        f"Request for resolution {resolution} rejected - the resolution must be between "
                        f"{MINIMUM_RESOLUTION} and {MAXIMUM_RESOLUTION} inclusively.",
                        400,
                        {"Access-Control-Allow-Origin": "*"},
                    ),
                )


class TestWithH3Cells(unittest.TestCase):
    def test_all_cells_available(self):
        """Test that, when all the input cells already have elevations in the database, database population is not
        requested and the response just contains their elevations.
        """
        data = {"h3_cells": [630949280935159295, 630949280220393983]}
        request = Mock(method="POST", get_json=Mock(return_value=data), args=data)
        mock_elevations = {630949280935159295: 32.1, 630949280220393983: 59}

        with patch("elevations_api.main._get_available_elevations_from_database", return_value=mock_elevations):
            with patch("elevations_api.main._populate_database") as mock_populate_database:
                response = get_or_request_elevations(request)

        self.assertEqual(
            response[0],
            {
                "schema_uri": OUTPUT_SCHEMA_URI,
                "schema_info": OUTPUT_SCHEMA_INFO_URL,
                "data": {"elevations": {str(index): elevation for index, elevation in mock_elevations.items()}},
            },
        )

        mock_populate_database.assert_not_called()

    def test_all_cells_unavailable(self):
        """Test that, when all the input cells don't have elevations in the database, database population is requested
        and the response contains an empty elevations list and a `later` list of the input cells.
        """
        data = {"h3_cells": [630949280935159295, 630949280220393983]}
        request = Mock(method="POST", get_json=Mock(return_value=data), args=data)

        with patch("elevations_api.main._get_available_elevations_from_database", return_value={}):
            with patch("elevations_api.main._populate_database") as mock_populate_database:
                response = get_or_request_elevations(request)

        self.assertEqual(response[0]["data"]["elevations"], {})
        self.assertEqual(set(response[0]["data"]["later"]), {630949280935159295, 630949280220393983})
        mock_populate_database.assert_called_with({630949280935159295, 630949280220393983})

    def test_some_cells_unavailable(self):
        """Test that, when some of the input cells have their elevations in the database, database population is
        requested for those that don't and the response contains an elevations list for those that were available and a
        `later` list of the input cells that weren't.
        """
        data = {"h3_cells": [630949280935159295, 630949280220393983, 630949280220402687, 630949280220390399]}
        request = Mock(method="POST", get_json=Mock(return_value=data), args=data)
        mock_elevations = {630949280935159295: 32.1, 630949280220393983: 59}

        with patch("elevations_api.main._get_available_elevations_from_database", return_value=mock_elevations):
            with patch("elevations_api.main._populate_database") as mock_populate_database:
                response = get_or_request_elevations(request)[0]

        self.assertEqual(
            response["data"]["elevations"],
            {str(index): elevation for index, elevation in mock_elevations.items()},
        )

        self.assertEqual(set(response["data"]["later"]), {630949280220402687, 630949280220390399})
        mock_populate_database.assert_called_with({630949280220402687, 630949280220390399})

    def test_database_population_not_re_requested_if_cell_in_ttl_cache(self):
        """Test that database population is not re-requested for a cell if it's in the TTL cache."""
        data = {"h3_cells": [630949280935159295]}
        request = Mock(method="POST", get_json=Mock(return_value=data), args=data)

        _add_cells_to_ttl_cache(data["h3_cells"])

        with patch("elevations_api.main._get_available_elevations_from_database", return_value={}):
            with patch("elevations_api.main._populate_database") as mock_populate_database:
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
                    get_or_request_elevations(request)

        mock_populate_database.assert_called()


class TestWithPolygon(unittest.TestCase):
    def test_all_cells_available(self):
        """Test requesting elevations as a polygon when all the cells are available."""
        data = {
            "polygon": [[54.53097, 5.96836], [54.53075, 5.96435], [54.52926, 5.96432], [54.52903, 5.96888]],
            "resolution": 10,
        }

        request = Mock(method="POST", get_json=Mock(return_value=data), args=data)
        mock_elevations = {622045820847849471: 1, 622045820847718399: 2, 622045848952471551: 3, 622045848952602623: 4}

        with patch("elevations_api.main._get_available_elevations_from_database", return_value=mock_elevations):
            with patch("elevations_api.main._populate_database") as mock_populate_database:
                response = get_or_request_elevations(request)

        self.assertEqual(
            response[0],
            {
                "schema_uri": OUTPUT_SCHEMA_URI,
                "schema_info": OUTPUT_SCHEMA_INFO_URL,
                "data": {"elevations": {str(index): elevation for index, elevation in mock_elevations.items()}},
            },
        )

        mock_populate_database.assert_not_called()


class TestWithCoordinates(unittest.TestCase):
    def test_all_cells_available(self):
        """Test requesting elevations for lat/lng coordinates when all cells are available."""
        data = {"coordinates": [[54.53097, 5.96836]]}
        request = Mock(method="POST", get_json=Mock(return_value=data), args=data)
        mock_elevations = {631053048207246335: 1}

        with patch("elevations_api.main._get_available_elevations_from_database", return_value=mock_elevations):
            with patch("elevations_api.main._populate_database") as mock_populate_database:
                response = get_or_request_elevations(request)

        self.assertEqual(
            response[0],
            {
                "schema_uri": OUTPUT_SCHEMA_URI,
                "schema_info": OUTPUT_SCHEMA_INFO_URL,
                "data": {"elevations": {"[54.53097, 5.96836]": 1}},
            },
        )

        mock_populate_database.assert_not_called()

    def test_all_cells_unavailable(self):
        """Test requesting elevations for lat/lng coordinates when the elevations for the corresponding cells aren't yet
        available.
        """
        coordinates = [[54.53097, 5.96836]]
        data = {"coordinates": coordinates}
        request = Mock(method="POST", get_json=Mock(return_value=data), args=data)

        with patch("elevations_api.main._get_available_elevations_from_database", return_value={}):
            with patch("elevations_api.main._populate_database") as mock_populate_database:
                response = get_or_request_elevations(request)

        self.assertEqual(
            response[0],
            {
                "schema_uri": OUTPUT_SCHEMA_URI,
                "schema_info": OUTPUT_SCHEMA_INFO_URL,
                "data": {"elevations": {}, "estimated_wait_time": 240, "later": coordinates},
            },
        )

        mock_populate_database.assert_called()


class TestGetAvailableCellsFromDatabase(unittest.TestCase):
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
