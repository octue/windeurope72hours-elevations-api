import functions_framework


@functions_framework.http
def get_elevations(request):
    return "Hello world!"
