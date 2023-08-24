import json
import logging
import os

import functions_framework
import jsonschema
from cachetools import TTLCache
from h3 import H3CellError
from h3.api.basic_int import geo_to_h3, h3_is_valid, polyfill
from jsonschema import ValidationError
from neo4j import GraphDatabase
from octue.cloud.pub_sub.service import Service
from octue.resources.service_backends import GCPPubSubBackend


ELEVATIONS_POPULATOR_PROJECT = "windeurope72-private"
ELEVATIONS_POPULATOR_SERVICE_SRUID = "octue/elevations-populator:0-2-5"
DATABASE_NAME = "neo4j"
TTL_CACHE_TIME = 3600
APPROXIMATE_DATABASE_POPULATION_WAIT_TIME = 240  # 4 minutes.
SINGLE_REQUEST_CELL_LIMIT = 15

MINIMUM_RESOLUTION = 8
MAXIMUM_RESOLUTION = 12

INPUT_SCHEMA_URI = "https://jsonschema.registry.octue.com/octue/h3-elevations-input/0.1.0.json"
OUTPUT_SCHEMA_URI = "https://jsonschema.registry.octue.com/octue/h3-elevations-output/0.1.6.json"
OUTPUT_SCHEMA_INFO_URL = "https://strands.octue.com/octue/h3-elevations-output"


logger = logging.getLogger(__name__)

driver = GraphDatabase.driver(
    uri=os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
)

# A TTL cache is used to avoid sending the same cells to the elevations populator service again before it's had time to
# process them. This avoids unnecessary computation and duplicate nodes in the database. As the database population wait
# time is less than the alive time for a Cloud Function instance, it's ok to run this cache in instance memory rather
# than using an external data store like Redis. Note that this isn't a substitute for rate limiting the cloud function.
recently_requested_for_database_population_cache = TTLCache(maxsize=1024, ttl=TTL_CACHE_TIME)


@functions_framework.http
def get_or_request_elevations(request):
    """For the input H3 cells in the request body that are already in the database, get their elevations; for those that
    aren't, request that they're added. If any of the input cells were recently requested for database population (i.e.
    within the database population wait time), they won't be re-requested until the wait time has been exceeded.

    The response to a successful request always contains the available cells mapped to their elevations and, if any cell
    elevations weren't available at request time, the indexes of these cells and an `instructions` field.

    Note that this endpoint will only accept POST requests.

    :param flask.Request request: the request sent to the Google Cloud Function
    :return flask.Response: a response containing the cell elevations
    """
    if request.method == "OPTIONS":
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "3600",
        }

        return "", 204, headers

    if request.method != "POST":
        return "This endpoint only accepts POST or OPTIONS requests.", 405, {"Access-Control-Allow-Origin": "*"}

    # Set CORS headers for the main request.
    headers = {"Access-Control-Allow-Origin": "*"}
    data = request.get_json()

    try:
        requested_cells, cells_and_coordinates = _parse_and_validate_data(data)
    except (ValueError, H3CellError, ValidationError) as error:
        message = str(error)
        logger.error(message)
        return message, 400, headers

    available_cells_and_elevations = _get_available_elevations_from_database(requested_cells)
    unavailable_cells = requested_cells - available_cells_and_elevations.keys()
    cells_to_populate = _extract_cells_to_populate(unavailable_cells)

    if cells_to_populate:
        _add_cells_to_ttl_cache(cells_to_populate)
        _populate_database(cells_to_populate)

    logger.info("Sending response.")

    response = _format_response(data, available_cells_and_elevations, unavailable_cells, cells_and_coordinates)
    jsonschema.validate(response, {"$ref": OUTPUT_SCHEMA_URI})
    return response, 200, headers


def _parse_and_validate_data(data):
    """Parse and validate the input data. The data must be a dictionary taking one of the following forms:
    1. An 'h3_cells' key mapped to a list of H3 cells in integer form.
    2. A 'polygon' key mapped to a list of lat/lng coordinates that define the points of a polygon, and a 'resolution'
       key mapped to the resolution (an integer) at which to get the H3 cells contained within the polygon.

    :param dict data: the body of the request containing either the key 'h3_cells', the keys 'coordinates' and optionally 'resolution', or the keys 'polygon' and optionally 'resolution'
    :return set(int), dict(int, tuple(float, float))|None: the cell indexes to get the elevations for and, if lat/lng coordinates were the input for this request, a mapping of cell indexes to lat/lng coordinates
    """
    jsonschema.validate(data, {"$ref": INPUT_SCHEMA_URI})
    resolution = data.get("resolution", MAXIMUM_RESOLUTION)

    if resolution > MAXIMUM_RESOLUTION or resolution < MINIMUM_RESOLUTION:
        raise ValueError(
            f"Request for resolution {resolution} rejected - the resolution must be between {MINIMUM_RESOLUTION} and "
            f"{MAXIMUM_RESOLUTION} inclusively."
        )

    cells_and_coordinates = None

    if "h3_cells" in data:
        requested_cells = set(data["h3_cells"])
        _validate_h3_cells(requested_cells)

    elif "coordinates" in data:
        requested_cells, cells_and_coordinates = _convert_coordinates_to_cells_and_validate(
            data["coordinates"],
            resolution,
        )

    else:
        requested_cells = _get_cells_within_polygon_and_validate(data["polygon"], resolution)

        # Validate that polygon is large enough to contain cells.
        if not requested_cells:
            raise ValueError("Request for zero cells rejected.")

    return requested_cells, cells_and_coordinates


def _get_available_elevations_from_database(cells):
    """Get the elevations of the given cells from the database if they're available.

    :param iter(int) cells: the indexes of the cells to attempt getting the elevations for
    :return dict(int, float): a mapping of cell index to elevation for cells that have elevations in the database. The elevation is measured in meters.
    """
    logger.info("Checking database for elevation data...")
    indexes = " or ".join(f"c.index = {cell}" for cell in cells)

    query = f"""
    MATCH (c:Cell)-[:HAS_ELEVATION]->(e:Elevation)
    WHERE {indexes}
    RETURN c.index, e.value
    """

    with driver:
        with driver.session(database=DATABASE_NAME) as session:
            result = dict(session.run(query).values())
            logger.info("Found %d of %d elevations in the database.", len(result), len(cells))
            return result


def _extract_cells_to_populate(unavailable_cells):
    """Extract the cells to request database population for from the set of cells that don't have elevations in the
    database. This filters out the cells that have recently had database population requested for them to avoid
    requesting population for any cells twice.

    :param set(int) unavailable_cells: the set of cell indexes that aren't in the database
    :return set(int): the subset of the unavailable cells that haven't recently had database population requested for them
    """
    cells_to_await = unavailable_cells & recently_requested_for_database_population_cache.keys()

    if cells_to_await:
        logger.info("Still waiting for %d cells to be populated in database.", len(cells_to_await))

    cells_to_populate = unavailable_cells - cells_to_await
    return cells_to_populate


def _add_cells_to_ttl_cache(cells):
    """Add the cells to the cache of cell indexes that have recently been requested for database population. These cells
    will remain in the cache until the population wait time has been exceeded and will not be re-requested for database
    population in that time.

    :param iter(int) cells: the cells to add to the cache for the population wait time
    :return None:
    """
    recently_requested_for_database_population_cache.update((cell, None) for cell in cells)


def _populate_database(cells):
    """Request that the given cells elevations are found and added to the database.

    :param iter(int) cells: the cells to request database population for
    :return None:
    """
    logger.info("Requesting database population for cells %r.", cells)
    service = Service(backend=GCPPubSubBackend(project_name=ELEVATIONS_POPULATOR_PROJECT))
    service.ask(service_id=ELEVATIONS_POPULATOR_SERVICE_SRUID, input_values={"h3_cells": list(cells)})


def _format_response(data, available_cells_and_elevations, unavailable_cells, cells_and_coordinates):
    """Format the API's JSON-ready response to send in answer to the current request.

    :param dict data: the body of the request containing either the key 'h3_cells', the keys 'coordinates' and optionally 'resolution', or the keys 'polygon' and optionally 'resolution'
    :param dict(int, float) available_cells_and_elevations: a mapping of cell index to elevation for cells that have elevations in the database. The elevation is measured in meters.
    :param set(int) unavailable_cells: the set of cell indexes that aren't in the database
    :param dict(int, tuple(float, float))|None: if lat/lng coordinates were the input for this request, a mapping of cell indexes to lat/lng coordinates
    :return dict: the JSON-ready response
    """
    if "coordinates" in data:
        available_cells_and_elevations = {
            json.dumps(cells_and_coordinates[cell]): elevation
            for cell, elevation in available_cells_and_elevations.items()
        }
    else:
        available_cells_and_elevations = {
            str(index): elevation for index, elevation in available_cells_and_elevations.items()
        }

    if unavailable_cells:
        if "coordinates" in data:
            later = {
                "later": [cells_and_coordinates[cell] for cell in unavailable_cells],
                "estimated_wait_time": APPROXIMATE_DATABASE_POPULATION_WAIT_TIME * len(data["coordinates"]),
            }
        else:
            later = {
                "later": list(unavailable_cells),
                "estimated_wait_time": APPROXIMATE_DATABASE_POPULATION_WAIT_TIME * len(data["h3_cells"]),
            }
    else:
        later = {}

    return {
        "schema_uri": OUTPUT_SCHEMA_URI,
        "schema_info": OUTPUT_SCHEMA_INFO_URL,
        "data": {"elevations": available_cells_and_elevations, **later},
    }


def _validate_h3_cells(cells):
    """Check that the cell indexes correspond to valid H3 cells and that they don't exceed the cell limit.

    :param set(int) cells: the cell indexes to validate
    :raise h3.H3CellError: if any of the cell indexes are invalid
    :return None:
    """
    _check_cell_limit_not_exceeded(cells)

    for cell in cells:
        if not h3_is_valid(cell):
            raise H3CellError(f"{cell} is not a valid H3 cell - aborting request.")

    logger.info("Accepted request for elevations of the H3 cells: %r.", cells)


def _convert_coordinates_to_cells_and_validate(coordinates, resolution):
    """Convert the given latitude/longitude coordinates to H3 cells, check that they don't exceed the cell limit, and
    return them along with a mapping of cell index to latitude/longitude coordinate so the input coordinates' elevations
    can be exactly matched back to them later. We have to do this because `(h3_to_geo(geo_to_h3(lat, lng, resolution))`
    does not give `(lat, lng)` exactly because the centrepoint of each H3 cell is returned, not the original lat/lng
    coordinate that simply fell somewhere within that cell.

    :param list(list(float, float)) coordinates: lat/lng coordinates to convert to H3 cells and validate
    :param int resolution: the resolution to convert the lat/lng coordinates to H3 cells at
    :return set(int), dict(int, tuple(float, float)): the cell indexes to get the elevations for and a mapping of cell indexes to lat/lng coordinates
    """
    cells_and_coordinates = {geo_to_h3(lat, lng, resolution): [lat, lng] for lat, lng in coordinates}
    requested_cells = set(cells_and_coordinates.keys())
    _check_cell_limit_not_exceeded(requested_cells)
    logger.info("Accepted request for elevations of the lat/lng coordinates %r.", coordinates)
    return requested_cells, cells_and_coordinates


def _get_cells_within_polygon_and_validate(polygon_coordinates, resolution):
    """Get the H3 cells of the given resolution whose centrepoints fall within the polygon defined by the given
    coordinates and check that the cell limit isn't exceeded. Note that the cell limit is 100 times higher when
    requesting cells within a polygon because they are guaranteed to be next to each other and so require much less
    computation to populate them in the database.

    :param list(list(float, float)) polygon_coordinates: lat/lng coordinates defining the corners of the polygon
    :param int resolution: the resolution of the cells to get within the polygon
    :return set(int): the indexes of the cells whose centrepoints fall within the polygon
    """
    requested_cells = polyfill(geojson={"type": "Polygon", "coordinates": [polygon_coordinates]}, res=resolution)
    _check_cell_limit_not_exceeded(requested_cells, cell_limit=SINGLE_REQUEST_CELL_LIMIT * 100)

    logger.info(
        "Accepted request for elevations of the H3 cells within a polygon at resolution %d, equating to %d cells.",
        resolution,
        len(requested_cells),
    )

    return requested_cells


def _check_cell_limit_not_exceeded(cells, cell_limit=SINGLE_REQUEST_CELL_LIMIT):
    """Check that the number of cells doesn't exceed the cell limit for a single request.

    :param iter(int) cells: the indexes of the cells to validate
    :param int cell_limit: the maximum number of cells allowed in a single request
    :raise ValueError: if the cell limit is exceeded
    :return None:
    """
    if len(cells) > cell_limit:
        raise ValueError(
            f"Request for {len(cells)} cells rejected - only {SINGLE_REQUEST_CELL_LIMIT} cells can be sent per request."
        )
