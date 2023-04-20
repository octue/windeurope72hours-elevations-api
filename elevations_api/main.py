import logging
import os

import functions_framework
from cachetools import TTLCache
from flask import jsonify
from h3 import H3CellError
from h3.api.basic_int import geo_to_h3, h3_is_valid, h3_to_geo, polyfill
from neo4j import GraphDatabase
from octue.cloud.pub_sub.service import Service
from octue.resources.service_backends import GCPPubSubBackend


ELEVATIONS_POPULATOR_PROJECT = "windeurope72-private"
ELEVATIONS_POPULATOR_SERVICE_SRUID = "octue/elevations-populator-private:0-2-2"
DATABASE_NAME = "neo4j"
DATABASE_POPULATION_WAIT_TIME = 240  # 4 minutes.
SINGLE_REQUEST_CELL_LIMIT = 15
DEFAULT_RESOLUTION = 12

OUTPUT_SCHEMA_URI = "https://jsonschema.registry.octue.com/octue/h3-elevations-output/0.1.0.json"
OUTPUT_SCHEMA_INFO_URL = "https://strands.octue.com/octue/h3-elevations-output"


logger = logging.getLogger(__name__)

driver = GraphDatabase.driver(
    uri=os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
)

# A TTL cache is used to avoid sending the same cells to the elevations populator service again before it's had time to
# process them. This avoids unnecessary computation and duplicate nodes in the database. This isn't a substitute for
# rate limiting the cloud function.
recently_requested_for_database_population_cache = TTLCache(maxsize=1024, ttl=DATABASE_POPULATION_WAIT_TIME)


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
    if request.method != "POST":
        return "This endpoint only accepts POST requests.", 405

    data = request.get_json()

    try:
        requested_cells = _parse_and_validate_data(data)
    except (ValueError, H3CellError) as error:
        message = str(error)
        logger.error(message)
        return message, 400

    available_cells_and_elevations = _get_available_elevations_from_database(requested_cells)
    unavailable_cells = requested_cells - available_cells_and_elevations.keys()
    cells_to_populate = _extract_cells_to_populate(unavailable_cells)

    if cells_to_populate:
        _add_cells_to_ttl_cache(cells_to_populate)
        _populate_database(cells_to_populate)

    if unavailable_cells:
        if "coordinates" in data:
            later = {
                "later": [h3_to_geo(cell) for cell in unavailable_cells],
                "estimated_wait_time": DATABASE_POPULATION_WAIT_TIME,
            }
        else:
            later = {
                "later": list(unavailable_cells),
                "estimated_wait_time": DATABASE_POPULATION_WAIT_TIME,
            }
    else:
        later = {}

    logger.info("Sending response.")

    return jsonify(
        {
            "schema_uri": OUTPUT_SCHEMA_URI,
            "schema_info": OUTPUT_SCHEMA_INFO_URL,
            "data": {"elevations": available_cells_and_elevations, **later},
        }
    )


def _parse_and_validate_data(data):
    """Parse and validate the input data. The data must be a dictionary taking one of the following forms:
    1. An 'h3_cells' key mapped to a list of H3 cells in integer form.
    2. A 'polygon' key mapped to a list of lat/lng coordinates that define the points of a polygon, and a 'resolution'
       key mapped to the resolution (an integer) at which to get the H3 cells contained within the polygon.

    :param dict data: the body of the request containing either the key 'h3_cells' or the keys 'polygon' and 'resolution'
    :return set(int): the cell indexes to get the elevations for
    """
    if not isinstance(data, dict) or not data.keys() & {"h3_cells", "coordinates", "polygon"}:
        raise ValueError(
            "The body must be a JSON object containing either an 'h3_cells' field, a 'coordinates' field and optional "
            "'resolution field', or a 'polygon' and optional 'resolution' field."
        )

    if "h3_cells" in data:
        requested_cells = set(data["h3_cells"])
        logger.info("Received request for elevations at the H3 cells: %r.", requested_cells)
        _check_cell_limit_not_exceeded(requested_cells)
        _validate_cells(requested_cells)
        return requested_cells

    elif "coordinates" in data:
        resolution = data.get("resolution", DEFAULT_RESOLUTION)
        requested_cells = {geo_to_h3(lat, lng, resolution) for lat, lng in data["coordinates"]}
        _check_cell_limit_not_exceeded(requested_cells)

        logger.info(
            "Received request for elevations at the lat/lng coordinates %r, equating to %d cells.",
            data["coordinates"],
            len(requested_cells),
        )

        return requested_cells

    resolution = data.get("resolution", DEFAULT_RESOLUTION)
    requested_cells = polyfill(geojson={"type": "Polygon", "coordinates": [data["polygon"]]}, res=resolution)
    _check_cell_limit_not_exceeded(requested_cells, cell_limit=SINGLE_REQUEST_CELL_LIMIT * 100)

    logger.info(
        "Received request for elevations of H3 cells within a polygon at resolution %d.",
        resolution,
        len(requested_cells),
    )

    return requested_cells


def _validate_cells(cells):
    """Check that cell indexes correspond to valid H3 cells.

    :param iter(int) cells: the indexes of the cells to validate
    :raise h3.H3CellError: if any of the cell indexes are invalid
    :return None:
    """
    for cell in cells:
        if not h3_is_valid(cell):
            raise H3CellError(f"{cell} is not a valid H3 cell - aborting request.")


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
    logger.info("Requesting database population for %d cells.", len(cells))
    service = Service(backend=GCPPubSubBackend(project_name=ELEVATIONS_POPULATOR_PROJECT))
    service.ask(service_id=ELEVATIONS_POPULATOR_SERVICE_SRUID, input_values={"h3_cells": list(cells)})
