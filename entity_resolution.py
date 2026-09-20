"""
Supplier entity resolution: deterministic normalization + a small index that
maps a supplier candidate to a stable id, so the same real-world company is
recognized as "the same supplier" across brands and across research runs
instead of creating a duplicate record every time.

Matching is based on normalized domain, phone, and legal name (address is
used as a secondary signal) - see SupplierCandidate in models.py. A match on
any one strong key (domain or phone) is treated as the same entity. Matching
never silently merges two candidates that only share a *brand* - that would
conflate a wholesaler and a manufacturer rep who both happen to carry the
same product line.

This module is pure logic - no I/O. supplier_persistence.py owns loading and
saving the EntityIndex to disk.
"""

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from models import SupplierCandidate

_LEGAL_SUFFIXES = re.compile(
    r"\b(llc|l\.l\.c\.|inc|incorporated|corp|corporation|co|company|ltd|limited|group|holdings)\b\.?",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_company_name(name: str) -> str:
    lowered = name.lower()
    lowered = _LEGAL_SUFFIXES.sub("", lowered)
    lowered = _NON_ALNUM.sub(" ", lowered)
    return " ".join(lowered.split())


def normalize_domain(website: str | None) -> str | None:
    if not website:
        return None
    website = website.strip()
    if "://" not in website:
        website = f"https://{website}"
    netloc = urlparse(website).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc or None


def normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits or None


def normalize_address(address: str | None) -> str | None:
    if not address:
        return None
    lowered = address.lower()
    lowered = _NON_ALNUM.sub(" ", lowered)
    return " ".join(lowered.split()) or None


def match_keys(candidate: SupplierCandidate) -> dict[str, str]:
    """Strong-match keys (domain, phone) plus weak-match keys (name, address)."""
    keys: dict[str, str] = {}
    domain = normalize_domain(candidate.website)
    if domain:
        keys["domain"] = domain
    phone = normalize_phone(candidate.phone)
    if phone:
        keys["phone"] = phone
    name = normalize_company_name(candidate.legal_business_name)
    if name:
        keys["name"] = name
    address = normalize_address(candidate.physical_address)
    if address:
        keys["address"] = address
    return keys


def _new_supplier_id(candidate: SupplierCandidate) -> str:
    basis = normalize_domain(candidate.website) or normalize_company_name(candidate.legal_business_name)
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]
    return f"sup_{digest}"


_STRONG_KEYS = ("domain", "phone")


@dataclass
class EntityIndex:
    """In-memory index: match key -> supplier_id, and supplier_id -> all keys
    ever seen for it. resolve() is the only mutating entry point."""
    key_to_id: dict[str, str] = field(default_factory=dict)
    id_to_keys: dict[str, set[str]] = field(default_factory=dict)

    def resolve(self, candidate: SupplierCandidate) -> str:
        keys = match_keys(candidate)

        for kind in _STRONG_KEYS:
            value = keys.get(kind)
            if value and (composite := f"{kind}:{value}") in self.key_to_id:
                supplier_id = self.key_to_id[composite]
                self._register(supplier_id, keys)
                return supplier_id

        # Fall back to a weak match (name + address both matching an existing
        # entity) only when neither strong key was available or matched.
        name_key = keys.get("name")
        address_key = keys.get("address")
        if name_key and address_key:
            composite_name = f"name:{name_key}"
            composite_address = f"address:{address_key}"
            candidate_id = self.key_to_id.get(composite_name)
            if candidate_id and self.key_to_id.get(composite_address) == candidate_id:
                self._register(candidate_id, keys)
                return candidate_id

        supplier_id = _new_supplier_id(candidate)
        while supplier_id in self.id_to_keys:
            supplier_id += "x"  # extremely unlikely hash collision fallback
        self._register(supplier_id, keys)
        return supplier_id

    def _register(self, supplier_id: str, keys: dict[str, str]) -> None:
        composites = {f"{kind}:{value}" for kind, value in keys.items()}
        for composite in composites:
            self.key_to_id[composite] = supplier_id
        self.id_to_keys.setdefault(supplier_id, set()).update(composites)

    def to_dict(self) -> dict:
        return {sid: sorted(keys) for sid, keys in self.id_to_keys.items()}

    @classmethod
    def from_dict(cls, data: dict) -> "EntityIndex":
        index = cls()
        for supplier_id, composites in data.items():
            index.id_to_keys[supplier_id] = set(composites)
            for composite in composites:
                index.key_to_id[composite] = supplier_id
        return index
