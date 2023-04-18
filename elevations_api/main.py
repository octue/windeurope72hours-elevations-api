import logging
import os

import functions_framework
from flask import abort, jsonify
from neo4j import GraphDatabase
from octue.cloud.pub_sub.service import Service
from octue.resources.service_backends import GCPPubSubBackend


logger = logging.getLogger(__name__)


ELEVATIONS_POPULATOR_PROJECT = "windeurope72-private"
ELEVATIONS_POPULATOR_SERVICE_SRUID = "octue/elevations-populator-private:0-2-2"
DATABASE_NAME = "neo4j"


driver = GraphDatabase.driver(
    uri=os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
)


@functions_framework.http
def get_elevations(request):
    if request.method != "POST":
        return abort(405)

    cells = set(request.get_json()["h3_cells"])
    logger.info("Received request for elevations at the H3 cells: %r.", cells)

    available_elevations = get_elevations_from_database(cells)
    missing_cells = cells - available_elevations.keys()

    if missing_cells:
        logger.info("Elevations are not in the database for %d cells.", len(missing_cells))
        populate_database(missing_cells)

    return jsonify({"elevations": available_elevations, "missing": missing_cells})


def get_elevations_from_database(cells):
    logger.info("Checking database for elevation data.")
    indexes = " or ".join(f"c.index = {cell}" for cell in cells)

    query = f"""
    MATCH (c:Cell)-[:HAS_ELEVATION]->(e:Elevation)
    WHERE {indexes}
    RETURN c.index, e.value
    """

    with driver:
        with driver.session(database=DATABASE_NAME) as session:
            result = session.run(query)
            return dict(result.values())


def populate_database(cells):
    logger.info("Requesting database population for %d cells.", len(cells))
    service = Service(backend=GCPPubSubBackend(project_name=ELEVATIONS_POPULATOR_PROJECT))
    service.ask(service_id=ELEVATIONS_POPULATOR_SERVICE_SRUID, input_values={"h3_cells": cells})
