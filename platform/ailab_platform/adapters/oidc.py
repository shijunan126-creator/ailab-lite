"""OIDC code + S256 PKCE adapter, restricted RS256 verification and bounded discovery/JWKS cache."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode, urlsplit

import httpx
import jwt

from ..config import Settings, settings
from ..ports.identity import Identity, IdentityError


class OIDCIdentityProvider:
    def __init__(self, config: Settings = settings):
        self.config = config
        self._metadata: tuple[float, dict] | None = None
        self._jwks: tuple[float, dict] | None = None
        self._issuer = ""

    def _url(self, url: str) -> str:
        parsed = urlsplit(url)
        local = self.config.env in {"dev", "test", "smoke", "k3s"} and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
        ):
            raise IdentityError("invalid identity provider endpoint")
        return url

    async def _request(self, method: str, url: str, **kwargs) -> dict:
        self._url(url)
        timeout = self.config.oidc_http_timeout_seconds
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(timeout, connect=min(5, timeout)), follow_redirects=False, trust_env=False
                ) as client:
                    async with client.stream(method, url, **kwargs) as response:
                        response.raise_for_status()
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > 128 * 1024:
                                raise IdentityError("identity provider response too large")
                        result = json.loads(data)
                        if not isinstance(result, dict):
                            raise IdentityError("invalid identity provider response")
                        return result
        except (httpx.HTTPError, TimeoutError, ValueError):
            raise IdentityError("identity provider unavailable or invalid response") from None

    async def metadata(self) -> dict:
        issuer = self.config.oidc_issuer.rstrip("/")
        if self._issuer != issuer:
            self._metadata = self._jwks = None
            self._issuer = issuer
        if self._metadata and self._metadata[0] > time.monotonic():
            return self._metadata[1]
        data = await self._request("GET", issuer + "/.well-known/openid-configuration")
        if data.get("issuer") != self.config.oidc_issuer:
            raise IdentityError("issuer mismatch")
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(data.get(field), str):
                raise IdentityError("invalid discovery document")
            self._url(data[field])
        self._metadata = (time.monotonic() + self.config.oidc_jwks_ttl_seconds, data)
        return data

    async def authorization_url(self, state: str, nonce: str, verifier: str) -> str:
        metadata = await self.metadata()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        query = urlencode(
            {
                "client_id": self.config.oidc_client_id,
                "redirect_uri": self.config.oidc_redirect_uri,
                "response_type": "code",
                "scope": "openid profile email",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return metadata["authorization_endpoint"] + ("&" if "?" in metadata["authorization_endpoint"] else "?") + query

    async def _keys(self, uri, *, refresh=False):
        if refresh or self._jwks is None or self._jwks[0] <= time.monotonic():
            data = await self._request("GET", uri)
            if not isinstance(data.get("keys"), list) or len(data["keys"]) > 100:
                raise IdentityError("invalid JWKS")
            self._jwks = (time.monotonic() + self.config.oidc_jwks_ttl_seconds, data)
        return self._jwks[1]["keys"]

    async def authenticate(self, credentials: dict[str, str]) -> Identity:
        try:
            async with asyncio.timeout(self.config.oidc_http_timeout_seconds):
                metadata = await self.metadata()
                data = {
                    "grant_type": "authorization_code",
                    "client_id": self.config.oidc_client_id,
                    "redirect_uri": self.config.oidc_redirect_uri,
                    "code": credentials["code"],
                    "code_verifier": credentials["verifier"],
                }
                secret = self.config.oidc_client_secret.get_secret_value()
                # Keycloak supports client_secret_basic; public clients omit authentication.
                kwargs = {"auth": (self.config.oidc_client_id, secret)} if secret else {}
                response = await self._request("POST", metadata["token_endpoint"], data=data, **kwargs)
                token = response.get("id_token")
                if not isinstance(token, str) or len(token) > 32768:
                    raise IdentityError("missing ID token")
                header = jwt.get_unverified_header(token)
                kid = header.get("kid")
                if header.get("alg") != "RS256" or not isinstance(kid, str):
                    raise IdentityError("unsupported ID token signature")
                keys = await self._keys(metadata["jwks_uri"])
                matches = [k for k in keys if isinstance(k, dict) and k.get("kid") == kid]
                if not matches:
                    keys = await self._keys(metadata["jwks_uri"], refresh=True)
                    matches = [k for k in keys if isinstance(k, dict) and k.get("kid") == kid]
                if len(matches) != 1:
                    raise IdentityError("unknown ID token key")
                key = matches[0]
                if key.get("kty") != "RSA" or key.get("use", "sig") != "sig" or key.get("alg", "RS256") != "RS256":
                    raise IdentityError("invalid ID token key")
                claims = jwt.decode(
                    token,
                    jwt.PyJWK.from_dict(key, algorithm="RS256").key,
                    algorithms=["RS256"],
                    audience=self.config.oidc_client_id,
                    issuer=self.config.oidc_issuer,
                    options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
                )
                if not isinstance(claims["sub"], str) or not 0 < len(claims["sub"]) <= 255:
                    raise IdentityError("invalid subject")
                audience = claims["aud"]
                if (isinstance(audience, list) and len(audience) > 1 and "azp" not in claims) or claims.get(
                    "azp", self.config.oidc_client_id
                ) != self.config.oidc_client_id:
                    raise IdentityError("invalid authorized party")
                if not isinstance(claims["nonce"], str) or not hmac.compare_digest(claims["nonce"], credentials["nonce"]):
                    raise IdentityError("invalid nonce")
                return Identity(claims["iss"], claims["sub"], claims)
        except (jwt.PyJWTError, KeyError, TypeError, ValueError, TimeoutError):
            raise IdentityError("OIDC authentication refused") from None


oidc_provider = OIDCIdentityProvider()
