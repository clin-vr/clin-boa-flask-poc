"""Authentication strategies for on-prem SharePoint.

The mechanism is NOT decided. This module exists so that decision can be made
later without touching anything else: every strategy implements the same
interface, and the choice is one config value.

What each option implies, for the design discussion:

  NTLM         Username and password over the wire. Usually a service account.
               Simplest to stand up. Note that NTLM negotiates per request
               (challenge, negotiate, then the actual call), so keep-alive and
               connection reuse matter or every read costs three round trips.
               Weakness for an audit platform: a shared account reads everything
               as itself, so `EXECUTED_AS` is constant and entitlement cannot be
               demonstrated.

  Kerberos     Uses the caller's ticket. Real delegation, and the only option
               that makes `EXECUTED_AS` meaningful. Requires SPNs and, for a
               service acting on someone's behalf, constrained delegation
               configured by the AD team. Infrastructure lead time.

  ADFS/SAML    Forms auth producing a FedAuth cookie. Relevant if the farm is
               claims-based rather than Windows auth. Cookie lifetime and
               renewal become the service's problem.

  Anonymous    Tests and local mocks only.

The right next step is not to pick one here — it is to find out what existing
applications that write to these SharePoint directories already use, and match
it.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class AuthError(RuntimeError):
    """Authentication could not be established."""


class AuthStrategy(ABC):
    """Applies credentials to a requests.Session."""

    name: str = "unknown"

    @abstractmethod
    def apply(self, session: Any) -> Any:
        """Attach auth to the session and return it."""

    @abstractmethod
    def principal(self) -> str:
        """Identity reads execute as. Stored in EXECUTED_AS on every record."""

    def describe(self) -> dict[str, str]:
        return {"mechanism": self.name, "principal": self.principal()}


class NtlmAuth(AuthStrategy):
    name = "ntlm"

    def __init__(self, username: str, password: str, domain: str | None = None) -> None:
        if not username or not password:
            raise AuthError("NTLM requires a username and password")
        self._username = f"{domain}\\{username}" if domain else username
        self._password = password

    def apply(self, session: Any) -> Any:
        try:
            from requests_ntlm import HttpNtlmAuth
        except ImportError as exc:  # pragma: no cover
            raise AuthError(
                "requests-ntlm is not installed. Confirm it is available in the "
                "bank's package mirror before committing to this strategy."
            ) from exc
        session.auth = HttpNtlmAuth(self._username, self._password)
        # NTLM is connection-oriented: the handshake binds to the TCP connection,
        # so dropping keep-alive re-authenticates on every single request.
        session.headers["Connection"] = "keep-alive"
        return session

    def principal(self) -> str:
        return self._username


class KerberosAuth(AuthStrategy):
    name = "kerberos"

    def __init__(self, principal_name: str | None = None) -> None:
        self._principal = principal_name

    def apply(self, session: Any) -> Any:
        try:
            from requests_kerberos import HTTPKerberosAuth, OPTIONAL
        except ImportError as exc:  # pragma: no cover
            raise AuthError(
                "requests-kerberos is not installed. Needs a working krb5 "
                "configuration and SPNs on the SharePoint farm."
            ) from exc
        session.auth = HTTPKerberosAuth(mutual_authentication=OPTIONAL)
        session.headers["Connection"] = "keep-alive"
        return session

    def principal(self) -> str:
        return self._principal or "<current kerberos principal>"


class AdfsAuth(AuthStrategy):
    """Placeholder. Only fill in if the farm turns out to be claims-based."""

    name = "adfs"

    def __init__(self, username: str, password: str, sts_url: str) -> None:
        self._username = username
        self._password = password
        self._sts_url = sts_url

    def apply(self, session: Any) -> Any:  # pragma: no cover
        raise AuthError(
            "ADFS strategy not implemented. Requires a SAML token request to the "
            "STS, exchanged at /_trust/ for a FedAuth cookie."
        )

    def principal(self) -> str:
        return self._username


class AnonymousAuth(AuthStrategy):
    """Tests and local mocks."""

    name = "anonymous"

    def apply(self, session: Any) -> Any:
        return session

    def principal(self) -> str:
        return "anonymous"


_STRATEGIES = {
    "ntlm": NtlmAuth,
    "kerberos": KerberosAuth,
    "adfs": AdfsAuth,
    "anonymous": AnonymousAuth,
}


def build_auth(mechanism: str, **kwargs: Any) -> AuthStrategy:
    try:
        strategy = _STRATEGIES[mechanism.lower()]
    except KeyError as exc:
        raise AuthError(
            f"Unknown auth mechanism {mechanism!r}. Available: {sorted(_STRATEGIES)}"
        ) from exc
    return strategy(**kwargs)
