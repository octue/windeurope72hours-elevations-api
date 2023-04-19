import logging
import os

import functions_framework
from cachetools import TTLCache
from flask import jsonify
from h3 import H3CellError
from h3.api.basic_int import h3_is_valid
from neo4j import GraphDatabase
from octue.cloud.pub_sub.service import Service
from octue.resources.service_backends import GCPPubSubBackend


ELEVATIONS_POPULATOR_PROJECT = "windeurope72-private"
ELEVATIONS_POPULATOR_SERVICE_SRUID = "octue/elevations-populator-private:0-2-2"
DATABASE_NAME = "neo4j"
DATABASE_POPULATION_WAIT_TIME = 240  # 4 minutes.
CELL_LIMIT = 1e5


logger = logging.getLogger(__name__)

driver = GraphDatabase.driver(
    uri=os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
)

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

    requested_cells = set(request.get_json()["h3_cells"])
    logger.info("Received request for elevations at the H3 cells: %r.", requested_cells)

    try:
        _validate_cells(requested_cells)
    except (ValueError, H3CellError) as error:
        return str(error), 400

    available_cells_and_elevations = _get_available_elevations_from_database(requested_cells)
    unavailable_cells = requested_cells - available_cells_and_elevations.keys()
    cells_to_populate = _extract_cells_to_populate(unavailable_cells)

    if cells_to_populate:
        _add_cells_to_ttl_cache(cells_to_populate)
        _populate_database(cells_to_populate)

    if not unavailable_cells:
        later = {}
    else:
        later = {
            "later": list(unavailable_cells),
            "instructions": (
                "The elevations present in the `elevations` field were available when you made your request. "
                "Elevations for the cell indexes in the `later` field were unavailable at that time but their "
                "elevations are now being added to the database - please re-request them in "
                f"{DATABASE_POPULATION_WAIT_TIME}s."
            ),
        }

    logger.info("Sending response.")
    return jsonify({"elevations": available_cells_and_elevations, **later})


def _validate_cells(cells):
    """Check that cell indexes correspond to valid H3 cells and that the number of cells doesn't exceed the request
    limit.

    :param iter(int) cells: the indexes of the cells to validate
    :raise ValueError: if the cell limit is exceeded
    :raise h3.H3CellError: if any of the cell indexes are invalid
    :return None:
    """
    if len(cells) > CELL_LIMIT:
        message = f"Request for {len(cells)} cells rejected - only {CELL_LIMIT} cells can be sent per request."
        logger.error(message)
        raise ValueError(message)

    for cell in cells:
        if not h3_is_valid(cell):
            message = f"{cell} is not a valid H3 cell - aborting request."
            logger.error(message)
            raise H3CellError(message)


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
