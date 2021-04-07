import os

from sanic.request import Request
from sanic.response import HTTPResponse, redirect
from sanic_ipware import get_client_ip

from asyncauth.core.config import config


def xss_prevention_middleware(request: Request, response: HTTPResponse):
    """
    Adds a header to all responses to prevent cross site scripting.
    """
    response.headers['x-xss-protection'] = '1; mode=block'


def https_redirect_middleware(request: Request):
    """
    :param request: Sanic request parameter.

    :return: redirect_url
    """
    if request.url.startswith('http://') and config['AUTH']['debug'] == 'false':
        url = request.url.replace('http://', 'https://', 1)
        return redirect(url)


def get_ip(request: Request):
    """
    Retrieves the ip address of the request.

    :param request: Sanic request.
    """
    proxies = config['AUTH']['proxies'].split(',').strip() if config.has_option('AUTH', 'proxies') else None
    proxy_count = int(config['AUTH']['proxy_count']) if config.has_option('AUTH', 'proxy_count') else None
    ip, routable = get_client_ip(request, proxy_trusted_ips=proxies, proxy_count=proxy_count)
    return request.remote_addr


def path_exists(path):
    """
    Checks if path exists and isn't empty, and creates it if it doesn't.

    :param path: Path being checked.

    :return: exists
    """
    exists = os.path.exists(path)
    if not exists:
        os.makedirs(path)
    return exists and os.listdir(path)
