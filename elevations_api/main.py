import functions_framework


@functions_framework.http
def hello(request):
    return "Hello world!"
