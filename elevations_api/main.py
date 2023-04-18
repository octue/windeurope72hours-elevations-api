import logging

import functions_framework
from flask import abort, jsonify
from octue.cloud.pub_sub.service import Service
from octue.resources.service_backends import GCPPubSubBackend


logger = logging.getLogger(__name__)


ELEVATIONS_POPULATOR_PROJECT = "windeurope72-private"
ELEVATIONS_POPULATOR_SERVICE_SRUID = "octue/elevations-populator-private:0-2-2"


@functions_framework.http
def get_elevations(request):
    if request.method != "POST":
        return abort(405)

    cells = request.get_json()["h3_cells"]
    logger.info("Received request for elevations at the H3 cells: %r.", cells)
    elevations = get_elevations_from_database(cells)

    if not elevations:
        service = Service(backend=GCPPubSubBackend(project_name=ELEVATIONS_POPULATOR_PROJECT))
        service.ask(service_id=ELEVATIONS_POPULATOR_SERVICE_SRUID)
        return 202

    return jsonify({"elevations": elevations})


def get_elevations_from_database(cells):
    pass
