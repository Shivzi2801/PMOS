"""
RBAC engine.

The single authority that answers one question: *does this set of roles grant
this permission?* It resolves role inheritance (flattening the role graph),
honours the wildcard grant, and is the only place permission logic lives so it
can be audited and tested in isolation.

Design notes:
  - Roles are looked up from an injectable role provider (a dict by default),
    which lets each tenant carry custom roles in addition to the defaults.
  - Inheritance is resolved depth-first with cycle protection.
  - The engine is pure (no I/O, no tenant logic); tenant isolation is enforced
    one layer up by the tenant guard, keeping responsibilities separate.
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, Optional, Set

from .permission import Permissions, WILDCARD
from .role import DefaultRoles, Role

RoleProvider = Callable[[str, Optional[str]], Optional[Role]]
# (role_name, tenant_id) -> Role | None


class RBACEngine:
    def __init__(self, roles: Optional[Dict[str, Role]] = None,
                 role_provider: Optional[RoleProvider] = None):
        # Global/default roles, always available to every tenant.
        self._defaults: Dict[str, Role] = roles or DefaultRoles.build()
        # Optional provider for tenant-scoped custom roles.
        self._provider = role_provider

    # --- role resolution ----------------------------------------------------
    def _resolve_role(self, name: str, tenant_id: Optional[str]) -> Optional[Role]:
        if self._provider:
            custom = self._provider(name, tenant_id)
            if custom:
                return custom
        return self._defaults.get(name)

    def effective_permissions(self, role_names: Iterable[str],
                              tenant_id: Optional[str] = None) -> Set[str]:
        """Flatten roles + inheritance into the full permission set."""
        resolved: Set[str] = set()
        seen: Set[str] = set()

        def visit(rname: str) -> None:
            if rname in seen:
                return
            seen.add(rname)
            role = self._resolve_role(rname, tenant_id)
            if not role:
                return
            resolved.update(role.permissions)
            for parent in role.inherits:
                visit(parent)

        for r in role_names:
            visit(r)
        return resolved

    # --- the core check -----------------------------------------------------
    def has_permission(self, role_names: Iterable[str], permission: str,
                       tenant_id: Optional[str] = None) -> bool:
        perms = self.effective_permissions(role_names, tenant_id)
        return WILDCARD in perms or permission in perms

    def has_any(self, role_names: Iterable[str], permissions: Iterable[str],
                tenant_id: Optional[str] = None) -> bool:
        perms = self.effective_permissions(role_names, tenant_id)
        if WILDCARD in perms:
            return True
        return any(p in perms for p in permissions)

    def has_all(self, role_names: Iterable[str], permissions: Iterable[str],
                tenant_id: Optional[str] = None) -> bool:
        perms = self.effective_permissions(role_names, tenant_id)
        if WILDCARD in perms:
            return True
        return all(p in perms for p in permissions)

    # --- introspection ------------------------------------------------------
    def known_permissions(self) -> Set[str]:
        return Permissions.all()
