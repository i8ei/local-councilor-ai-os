"""Read-only diagnosis and safe Vault scaffolding for onboarding."""

from .core import apply_scaffold, build_plan, diagnose_environment, verify_scaffold

__all__ = [
    "apply_scaffold",
    "build_plan",
    "diagnose_environment",
    "verify_scaffold",
]
