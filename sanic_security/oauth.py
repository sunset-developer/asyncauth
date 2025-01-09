import functools
import time

import jwt
from httpx_oauth.oauth2 import BaseOAuth2, RefreshTokenError
from jwt import DecodeError
from sanic import Request, HTTPResponse, Sanic
from sanic.log import logger
from tortoise.exceptions import IntegrityError, DoesNotExist

from sanic_security.configuration import config
from sanic_security.exceptions import JWTDecodeError, ExpiredError, CredentialsError
from sanic_security.models import Account, AuthenticationSession
from sanic_security.utils import get_ip

"""
Copyright (c) 2020-present Nicholas Aidan Stewart

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


async def oauth_callback(
    request: Request,
    client: BaseOAuth2,
    redirect_uri: str = config.OAUTH_REDIRECT,
    code_verifier: str = None,
) -> tuple[dict, AuthenticationSession]:
    """
    Requests an access token using the authorization code obtained after the user has authorized the application.
    An account is retrieved if it already exists, created if it doesn't, and the user is logged in.

    Args:
        request (Request): Sanic request parameter.
        client (BaseOAuth2): OAuth provider.
        redirect_uri (str): The URL where the user was redirected after authorization.
        code_verifier (str): Optional code verifier used in the [PKCE](https://datatracker.ietf.org/doc/html/rfc7636)) flow.

    Raises:
        CredentialsError
        GetAccessTokenError
        GetIdEmailError

    Returns:
        oauth_redirect
    """
    token_info = await client.get_access_token(
        request.args.get("code"),
        redirect_uri,
        code_verifier,
    )
    if "expires_at" not in token_info:
        token_info["expires_at"] = time.time() + token_info["expires_in"]
    oauth_id, email = await client.get_id_email(token_info["access_token"])
    try:
        try:
            account = await Account.get(oauth_id=oauth_id)
        except DoesNotExist:
            account = await Account.create(
                email=email,
                username=email.split("@")[0],
                password="",
                oauth_id=oauth_id,
                verified=True,
            )
        authentication_session = await AuthenticationSession.new(
            request,
            account,
        )
        logger.info(
            f"Client {get_ip(request)} has logged in via {client.__class__.__name__} with authentication session {authentication_session.id}."
        )
        return token_info, authentication_session
    except IntegrityError:
        raise CredentialsError(
            f"Account may not be linked to this OAuth provider if it already exists.",
            409,
        )


def oauth_encode(response: HTTPResponse, token_info: dict) -> None:
    """
    Transforms OAuth access token into JWT and then is stored in a cookie.

    Args:
        response (HTTPResponse): Sanic response used to store JWT into a cookie on the client.
        token_info (dict): OAuth access token.
    """
    response.cookies.add_cookie(
        f"{config.SESSION_PREFIX}_oauth",
        str(
            jwt.encode(
                token_info,
                config.SECRET,
                config.SESSION_ENCODING_ALGORITHM,
            ),
        ),
        httponly=config.SESSION_HTTPONLY,
        samesite=config.SESSION_SAMESITE,
        secure=config.SESSION_SECURE,
        domain=config.SESSION_DOMAIN,
        max_age=token_info["expires_in"] + config.AUTHENTICATION_REFRESH_EXPIRATION,
    )


async def oauth_decode(request: Request, client: BaseOAuth2, refresh=False) -> dict:
    """
    Decodes OAuth JWT token from client cookie into an access token.

    Args:
        request (Request): Sanic request parameter.
        client (BaseOAuth2): OAuth provider.
        refresh (bool): Ensures that the decoded access token is refreshed.

    Raises:
        JWTDecodeError
        ExpiredError
        RefreshTokenNotSupportedError

    Returns:
        token_info
    """
    try:
        token_info = jwt.decode(
            request.cookies.get(
                f"{config.SESSION_PREFIX}_oauth",
            ),
            config.PUBLIC_SECRET or config.SECRET,
            config.SESSION_ENCODING_ALGORITHM,
        )
        if time.time() > token_info["expires_at"] or refresh:
            token_info = await client.refresh_token(token_info["refresh_token"])
            token_info["is_refresh"] = True
            if "expires_at" not in token_info:
                token_info["expires_at"] = time.time() + token_info["expires_in"]
        request.ctx.oauth = token_info
        return token_info
    except RefreshTokenError:
        raise ExpiredError
    except DecodeError:
        raise JWTDecodeError


def requires_oauth(client: BaseOAuth2):
    """
    Decodes OAuth JWT token from client cookie into an access token.

    Args:
        client (BaseOAuth2): OAuth provider.

    Example:
        This method is not called directly and instead used as a decorator:

            @app.post('api/oauth')
            @requires_oauth
            async def on_oauth(request):
                return text('OAuth access token retrieved!')

    Raises:
        JWTDecodeError
        ExpiredError
        RefreshTokenNotSupportedError
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(request, *args, **kwargs):
            await oauth_decode(request, client)
            return await func(request, *args, **kwargs)

        return wrapper

    return decorator


def initialize_oauth(app: Sanic) -> None:
    """
    Attaches refresh encoder middleware.

    Args:
        app (Sanic): Sanic application instance.
    """

    @app.on_response
    async def refresh_encoder_middleware(request, response):
        if hasattr(request.ctx, "oauth") and getattr(
            request.ctx.oauth, "is_refresh", False
        ):
            oauth_encode(response, request.ctx.oauth)