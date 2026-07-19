# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed ownership graph for wallbox service composition."""

from __future__ import annotations

from typing import Protocol

from .composition_ports import (
    AutoControllerPort as _AutoControllerPort,
)
from .composition_ports import (
    AutoInputControllerPort as _AutoInputControllerPort,
)
from .composition_ports import (
    BootstrapControllerPort as _BootstrapControllerPort,
)
from .composition_ports import (
    CompanionControllerPort as _CompanionControllerPort,
)
from .composition_ports import (
    DbusInputControllerPort as _DbusInputControllerPort,
)
from .composition_ports import (
    PublishControllerPort as _PublishControllerPort,
)
from .composition_ports import (
    RuntimeControllerPort as _RuntimeControllerPort,
)
from .composition_ports import (
    ServiceFunctionsPort as _ServiceFunctionsPort,
)
from .composition_ports import (
    ShellyControllerPort as _ShellyControllerPort,
)
from .composition_ports import (
    StateControllerPort as _StateControllerPort,
)
from .composition_ports import (
    UpdateControllerPort as _UpdateControllerPort,
)
from .composition_ports import (
    WriteControllerPort as _WriteControllerPort,
)


class RuntimeControllerSetPort(Protocol):
    @property
    def runtime(self) -> _RuntimeControllerPort: ...
    @property
    def auto(self) -> _AutoControllerPort: ...
    @property
    def publisher(self) -> _PublishControllerPort: ...
    @property
    def shelly(self) -> _ShellyControllerPort: ...
    @property
    def write(self) -> _WriteControllerPort: ...
    @property
    def auto_input(self) -> _AutoInputControllerPort: ...
    @property
    def dbus_input(self) -> _DbusInputControllerPort: ...
    @property
    def update(self) -> _UpdateControllerPort: ...
    @property
    def companion(self) -> _CompanionControllerPort: ...


class ControllerOwnerPort(Protocol):
    @property
    def functions(self) -> _ServiceFunctionsPort: ...
    @property
    def state(self) -> _StateControllerPort: ...
    @property
    def bootstrap(self) -> _BootstrapControllerPort: ...
    @property
    def runtime(self) -> RuntimeControllerSetPort: ...
