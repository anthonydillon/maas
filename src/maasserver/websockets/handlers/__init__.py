# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Handlers for the WebSocket connections."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = [
    "NodeHandler",
    "DeviceHandler",
    ]

from maasserver.utils import ignore_unused
from maasserver.websockets.handlers.device import DeviceHandler
from maasserver.websockets.handlers.node import NodeHandler


ignore_unused(DeviceHandler)
ignore_unused(NodeHandler)
