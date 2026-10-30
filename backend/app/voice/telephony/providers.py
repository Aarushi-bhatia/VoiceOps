"""Catalog of telephony providers.

A provider module registers a factory under a name, and ``TELEPHONY_PROVIDER``
picks one. Adding a carrier therefore means adding one module here - nothing in
the core registry, config or worker changes:

    class AcmeSettings(ProviderSettings):
        model_config = SettingsConfigDict(env_prefix="ACME_", **ProviderSettings.base_config)
        api_key: str = ""

    @register("acme")
    def build_acme(settings: Settings) -> TelephonyProvider:
        return AcmeTelephonyProvider(AcmeSettings().api_key)

Each provider reads its own prefixed environment variables, so its credentials
never have to be declared on the shared :class:`~app.core.config.Settings`.
Modules outside this package can register too: list them in
``TELEPHONY_PLUGINS`` and they are imported at startup.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable
from typing import Any, ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import Settings
from app.voice.base import TelephonyProvider

logger = logging.getLogger(__name__)

Factory = Callable[[Settings], TelephonyProvider]

_FACTORIES: dict[str, Factory] = {}


class ProviderSettings(BaseSettings):
    """Base for a provider's own settings; subclasses set ``env_prefix``."""

    base_config: ClassVar[dict[str, Any]] = {
        "env_file": (".env", "../.env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "case_sensitive": False,
    }
    model_config = SettingsConfigDict(**base_config)


def register(name: str) -> Callable[[Factory], Factory]:
    """Register a provider factory under ``name`` (decorator)."""

    def decorator(factory: Factory) -> Factory:
        existing = _FACTORIES.get(name)
        if existing is not None and existing is not factory:
            raise ValueError(f"telephony provider {name!r} is already registered")
        _FACTORIES[name] = factory
        return factory

    return decorator


def discover(extra_modules: list[str] | None = None) -> None:
    """Import every module in this package, plus any named plugin modules.

    Importing is what registers a provider. Provider modules import their SDK
    and HTTP dependencies lazily or are light, so loading them all is cheap.
    """
    from app.voice import telephony

    for module in pkgutil.iter_modules(telephony.__path__):
        importlib.import_module(f"{telephony.__name__}.{module.name}")
    for path in extra_modules or []:
        importlib.import_module(path)


def available() -> list[str]:
    return sorted(_FACTORIES)


def build(settings: Settings) -> TelephonyProvider:
    discover(settings.telephony_plugin_list)
    factory = _FACTORIES.get(settings.telephony_provider)
    if factory is None:
        raise ValueError(
            f"unknown TELEPHONY_PROVIDER: {settings.telephony_provider} "
            f"(available: {', '.join(available())})"
        )
    return factory(settings)
