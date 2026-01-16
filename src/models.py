# Copyright 2024 Canonical
# See LICENSE file for licensing details.

"""Useful dataclass models."""

import dataclasses

from charms.tls_certificates_interface.v4.tls_certificates import (
    CertificateRequestAttributes,
    PrivateKey,
    ProviderCertificate,
)


@dataclasses.dataclass
class TLSConfig:
    """Model ."""

    cr: "CertificateRequestAttributes"
    certificate: "ProviderCertificate"
    key: "PrivateKey"
