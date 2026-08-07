"""Router-owned OAuth metadata and JWT issuer compatibility seams.

FastMCP stores a bare-origin ``base_url`` as a Pydantic ``AnyHttpUrl``, whose
default string form adds ``/``.  That representation previously leaked into
metadata and router-issued JWT ``iss`` claims.  This module keeps one slashless
issuer identity while accepting only explicitly configured legacy identities
for a fixed migration window.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, cast

from fastmcp.server.auth import OAuthProxy
from fastmcp.server.auth.jwt_issuer import JWTIssuer
from joserfc.errors import JoseError
from mcp.server.auth.handlers.metadata import (
    MetadataHandler,
    ProtectedResourceMetadataHandler,
)
from mcp.server.auth.routes import build_metadata, cors_middleware
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata
from pydantic import AnyHttpUrl, ConfigDict
from starlette.routing import Route


def canonical_issuer(base_url: str) -> str:
    """Return the validated issuer URL without a trailing root slash."""
    return str(AnyHttpUrl(base_url)).rstrip("/")


class CanonicalOAuthMetadata(OAuthMetadata):
    """OAuth metadata whose bare-origin URLs retain an empty path."""

    model_config = ConfigDict(url_preserve_empty_path=True)


class CanonicalProtectedResourceMetadata(ProtectedResourceMetadata):
    """Protected-resource metadata with byte-stable authorization servers."""

    model_config = ConfigDict(url_preserve_empty_path=True)


class TransitionalJWTIssuer(JWTIssuer):
    """Issue canonical JWTs and temporarily verify configured legacy issuers."""

    def __init__(
        self,
        issuer: str,
        audience: str,
        signing_key: bytes,
        *,
        legacy_issuers: Sequence[str],
        legacy_accept_until: datetime,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        canonical = canonical_issuer(issuer)
        super().__init__(issuer=canonical, audience=audience, signing_key=signing_key)
        if legacy_accept_until.tzinfo is None:
            raise ValueError("legacy issuer deadline must include a UTC offset")
        self._legacy_accept_until = legacy_accept_until.astimezone(UTC)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._legacy_verifiers = tuple(
            JWTIssuer(issuer=value, audience=audience, signing_key=signing_key)
            for value in dict.fromkeys(legacy_issuers)
            if value != canonical
        )

    def verify_token(
        self,
        token: str,
        expected_token_use: str = "access",  # noqa: S107 - token class, not a secret
    ) -> dict[str, Any]:
        """Verify canonically, then try configured legacy issuers before the deadline."""
        try:
            return super().verify_token(token, expected_token_use=expected_token_use)
        except JoseError as canonical_error:
            if self._clock().astimezone(UTC) >= self._legacy_accept_until:
                raise

            for verifier in self._legacy_verifiers:
                try:
                    return verifier.verify_token(token, expected_token_use=expected_token_use)
                except JoseError:
                    continue
            raise canonical_error


class GeneFoundryOAuthProxy(OAuthProxy):
    """FastMCP OAuth proxy with canonical metadata and transitional JWT checks."""

    def __init__(
        self,
        *,
        canonical_issuer_url: str,
        legacy_issuer_urls: Sequence[str],
        legacy_issuer_accept_until: datetime,
        **kwargs: Any,
    ) -> None:
        self._canonical_issuer_url = canonical_issuer(canonical_issuer_url)
        self._legacy_issuer_urls = tuple(legacy_issuer_urls)
        self._legacy_issuer_accept_until = legacy_issuer_accept_until
        super().__init__(**kwargs)

    def set_mcp_path(self, mcp_path: str | None) -> None:
        """Install the router issuer after FastMCP computes the resource audience."""
        super().set_mcp_path(mcp_path)
        self._jwt_issuer = TransitionalJWTIssuer(
            issuer=self._canonical_issuer_url,
            audience=str(self._resource_url),
            signing_key=self._jwt_signing_key,
            legacy_issuers=self._legacy_issuer_urls,
            legacy_accept_until=self._legacy_issuer_accept_until,
        )

    def _authorization_server_metadata(self) -> CanonicalOAuthMetadata:
        registration = self.client_registration_options or ClientRegistrationOptions()
        revocation = self.revocation_options or RevocationOptions()
        assert self.base_url is not None
        metadata = build_metadata(
            self.base_url,
            self.service_documentation_url,
            registration,
            revocation,
        )
        metadata.client_id_metadata_document_supported = True
        existing = metadata.token_endpoint_auth_methods_supported or []
        metadata.token_endpoint_auth_methods_supported = [
            *existing,
            "private_key_jwt",
            "none",
        ]
        payload = metadata.model_dump(mode="json")
        payload["issuer"] = self._canonical_issuer_url
        return CanonicalOAuthMetadata.model_validate(payload)

    def _protected_resource_metadata(self) -> CanonicalProtectedResourceMetadata:
        registration = self.client_registration_options
        supported_scopes = (
            registration.valid_scopes
            if registration is not None and registration.valid_scopes
            else self.required_scopes
        )
        assert self._resource_url is not None
        return CanonicalProtectedResourceMetadata(
            resource=self._resource_url,
            # Pydantic validates the string under this model's empty-path-preserving
            # config. The cast satisfies the inherited SDK model's static annotation.
            authorization_servers=[cast(AnyHttpUrl, self._canonical_issuer_url)],
            scopes_supported=supported_scopes,
        )

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        """Replace only authorization-server and protected-resource metadata."""
        routes = super().get_routes(mcp_path)
        authorization_handler = MetadataHandler(self._authorization_server_metadata())
        protected_handler = ProtectedResourceMetadataHandler(self._protected_resource_metadata())
        replaced: list[Route] = []

        for route in routes:
            if route.path.startswith("/.well-known/oauth-authorization-server"):
                endpoint = cors_middleware(authorization_handler.handle, ["GET", "OPTIONS"])
            elif route.path.startswith("/.well-known/oauth-protected-resource"):
                endpoint = cors_middleware(protected_handler.handle, ["GET", "OPTIONS"])
            else:
                replaced.append(route)
                continue
            replaced.append(
                Route(
                    path=route.path,
                    endpoint=endpoint,
                    methods=route.methods or ["GET", "OPTIONS"],
                    name=route.name,
                    include_in_schema=route.include_in_schema,
                )
            )

        return replaced
