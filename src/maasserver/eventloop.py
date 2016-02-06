# Copyright 2014-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Event-loop support for the MAAS Region Controller.

This helps start up a background event loop (using Twisted, via crochet)
to handle communications with Cluster Controllers, and any other tasks
that are not tied to an HTTP reqoest.

.. py:data:: loop

   The single instance of :py:class:`RegionEventLoop` that's all a
   process needs.

.. py:data:: services

   The :py:class:`~twisted.application.service.MultiService` which forms
   the root of this process's service tree.

   This is a convenient reference to :py:attr:`.loop.services`.

.. py:data:: start

   Start all the services in :py:data:`services`.

   This is a convenient reference to :py:attr:`.loop.start`.

.. py:data:: stop

   Stop all the services in :py:data:`services`.

   This is a convenient reference to :py:attr:`.loop.stop`.

"""

__all__ = [
    "loop",
    "reset",
    "services",
    "start",
    "stop",
]

from errno import ENOPROTOOPT
from logging import getLogger
from os import getpid
import socket
from socket import (
    error as socket_error,
    gethostname,
)

from maasserver.utils.orm import disable_all_database_connections
from provisioningserver.utils.twisted import asynchronous
from twisted.application.service import MultiService
from twisted.internet import reactor
from twisted.internet.endpoints import AdoptedStreamServerEndpoint

# Default port for regiond.
DEFAULT_PORT = 5240

logger = getLogger(__name__)


reactor.addSystemEventTrigger(
    "before", "startup", disable_all_database_connections)


def make_DatabaseTaskService():
    from maasserver.utils import dbtasks
    return dbtasks.DatabaseTasksService()


def make_RegionService():
    # Import here to avoid a circular import.
    from maasserver.rpc import regionservice
    return regionservice.RegionService()


def make_RegionAdvertisingService():
    # Import here to avoid a circular import.
    from maasserver.rpc import regionservice
    return regionservice.RegionAdvertisingService()


def make_NonceCleanupService():
    from maasserver import nonces_cleanup
    return nonces_cleanup.NonceCleanupService()


def make_StatusMonitorService():
    from maasserver import status_monitor
    return status_monitor.StatusMonitorService()


def make_ImportResourcesService():
    from maasserver import bootresources
    return bootresources.ImportResourcesService()


def make_ImportResourcesProgressService():
    from maasserver import bootresources
    return bootresources.ImportResourcesProgressService()


def make_PostgresListenerService():
    from maasserver.listener import PostgresListenerService
    return PostgresListenerService()


def make_WebApplicationService(postgresListener):
    from maasserver.webapp import WebApplicationService
    site_port = DEFAULT_PORT  # config["port"]
    # Make a socket with SO_REUSEPORT set so that we can run multiple web
    # applications. This is easier to do from outside of Twisted as there's
    # not yet official support for setting socket options.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except socket_error as e:
        # Python's socket module was compiled using modern headers
        # thus defining SO_REUSEPORT would cause issues as it might
        # running in older kernel that does not support SO_REUSEPORT.

        # XXX andreserl 2015-04-08 bug=1441684: We need to add a warning
        # log message when we see this error, and a test for it.
        if e.errno != ENOPROTOOPT:
            raise e
    s.bind(('0.0.0.0', site_port))
    # Use a backlog of 50, which seems to be fairly common.
    s.listen(50)
    # Adopt this socket into Twisted's reactor.
    site_endpoint = AdoptedStreamServerEndpoint(reactor, s.fileno(), s.family)
    site_endpoint.port = site_port  # Make it easy to get the port number.
    site_endpoint.socket = s  # Prevent garbage collection.
    site_service = WebApplicationService(site_endpoint, postgresListener)
    return site_service


class RegionEventLoop:
    """An event loop running in a region controller process.

    Typically several processes will be running the web application --
    chiefly Django -- across several machines, with multiple threads of
    execution in each process.

    This class represents a single event loop for each *process*,
    allowing convenient control of the event loop -- a Twisted reactor
    running in a thread -- and to which to attach and query services.

    :cvar factories: A sequence of ``(name, factory)`` tuples. Used to
        populate :py:attr:`.services` at start time.

    :ivar services:
        A :py:class:`~twisted.application.service.MultiService` which
        forms the root of the service tree.

    """

    factories = {
        "database-tasks": {
            "factory": make_DatabaseTaskService,
            "requires": [],
        },
        "rpc": {
            "factory": make_RegionService,
            "requires": [],
        },
        "rpc-advertise": {
            "factory": make_RegionAdvertisingService,
            "requires": [],
        },
        "nonce-cleanup": {
            "factory": make_NonceCleanupService,
            "requires": [],
        },
        "status-monitor": {
            "factory": make_StatusMonitorService,
            "requires": [],
        },
        "import-resources": {
            "factory": make_ImportResourcesService,
            "requires": [],
        },
        "import-resources-progress": {
            "factory": make_ImportResourcesProgressService,
            "requires": [],
        },
        "postgres-listener": {
            "factory": make_PostgresListenerService,
            "requires": [],
        },
        "web": {
            "factory": make_WebApplicationService,
            "requires": ["postgres-listener"],
        },
    }

    def __init__(self):
        super(RegionEventLoop, self).__init__()
        self.services = MultiService()
        self.handle = None

    @asynchronous
    def populateService(self, name):
        """Prepare a service."""
        factoryInfo = self.factories[name]
        try:
            service = self.services.getServiceNamed(name)
        except KeyError:
            # Get all dependent services for this services.
            dependencies = []
            for require in factoryInfo["requires"]:
                dependencies.append(self.populateService(require))

            # Create the service with dependencies.
            service = factoryInfo["factory"](*dependencies)
            service.setName(name)
            service.setServiceParent(self.services)
        return service

    @asynchronous
    def populate(self):
        """Prepare all services."""
        for name in self.factories.keys():
            self.populateService(name)

    @asynchronous
    def prepare(self):
        """Perform start_up of the region process."""
        from maasserver.start_up import start_up
        return start_up()

    @asynchronous
    def startMultiService(self, result):
        """Start the multi service."""
        self.services.startService()

    @asynchronous
    def start(self):
        """start()

        Start all services in the region's event-loop.
        """
        self.populate()
        self.handle = reactor.addSystemEventTrigger(
            'before', 'shutdown', self.services.stopService)
        return self.prepare().addCallback(
            self.startMultiService)

    @asynchronous
    def stop(self):
        """stop()

        Stop all services in the region's event-loop.
        """
        if self.handle is not None:
            handle, self.handle = self.handle, None
            reactor.removeSystemEventTrigger(handle)
        return self.services.stopService()

    @asynchronous
    def reset(self):
        """reset()

        Stop all services, then disown them all.
        """
        def disown_all_services(_):
            for service in list(self.services):
                service.disownServiceParent()

        def reset_factories(_):
            try:
                # Unshadow class attribute.
                del self.factories
            except AttributeError:
                # It wasn't shadowed.
                pass

        d = self.stop()
        d.addCallback(disown_all_services)
        d.addCallback(reset_factories)
        return d

    @property
    def name(self):
        """A name for identifying this service in a distributed system."""
        return "%s:pid=%d" % (gethostname(), getpid())

    @property
    def running(self):
        """Is this running?"""
        return bool(self.services.running)


loop = RegionEventLoop()
reset = loop.reset
services = loop.services
start = loop.start
stop = loop.stop
