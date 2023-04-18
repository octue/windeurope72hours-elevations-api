import functions_framework
from octue.cloud.pub_sub.service import Service
from octue.resources.service_backends import GCPPubSubBackend


ELEVATIONS_POPULATOR_PROJECT = "windeurope72-private"
ELEVATIONS_POPULATOR_SERVICE_SRUID = "octue/elevations-populator-private:0-2-2"


@functions_framework.http
def get_elevations(request):
    elevations = get_elevations_from_database(request.args)

    if not elevations:
        service = Service(backend=GCPPubSubBackend(project_name=ELEVATIONS_POPULATOR_PROJECT))
        service.ask(service_id=ELEVATIONS_POPULATOR_SERVICE_SRUID)
        return 202

    return elevations, 200


def get_elevations_from_database(cells):
    pass
