import logging
import os

import functions_framework
from cachetools import TTLCache
from flask import abort, jsonify
from neo4j import GraphDatabase
from octue.cloud.pub_sub.service import Service
from octue.resources.service_backends import GCPPubSubBackend


logger = logging.getLogger(__name__)

driver = GraphDatabase.driver(
    uri=os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
)

POPULATION_WAIT_TIME = 240  # 4 minutes.
recently_requested_for_database_population_cache = TTLCache(maxsize=1024, ttl=POPULATION_WAIT_TIME)


ELEVATIONS_POPULATOR_PROJECT = "windeurope72-private"
ELEVATIONS_POPULATOR_SERVICE_SRUID = "octue/elevations-populator-private:0-2-2"
DATABASE_NAME = "neo4j"


@functions_framework.http
def get_elevations(request):
    if request.method != "POST":
        return abort(405)

    requested_cells = set(request.get_json()["h3_cells"])
    logger.info("Received request for elevations at the H3 cells: %r.", requested_cells)

    available_cells_and_elevations = _get_available_elevations_from_database(requested_cells)
    unavailable_cells = requested_cells - available_cells_and_elevations.keys()
    cells_to_populate = _extract_cells_to_populate(unavailable_cells)

    if cells_to_populate:
        _add_cells_to_ttl_cache(cells_to_populate)
        _populate_database(cells_to_populate)

    logger.info("Sending response.")
    return jsonify({"elevations": available_cells_and_elevations, "later": list(unavailable_cells)})


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
