# Copyright 2012-2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Commissioning Scripts Settings views."""

__all__ = [
    "CommissioningScriptCreate",
    "CommissioningScriptDelete",
    ]

from django.contrib import messages
from django.http import (
    HttpResponseNotFound,
    HttpResponseRedirect,
)
from django.shortcuts import get_object_or_404
from django.views.generic import (
    CreateView,
    DeleteView,
)
from maasserver.audit import create_audit_event
from maasserver.enum import ENDPOINT
from maasserver.forms.script import CommissioningScriptForm
from maasserver.utils.django_urls import reverse
from metadataserver.models import Script
from provisioningserver.events import EVENT_TYPES

# The anchor of the commissioning scripts slot on the settings page.
COMMISSIONING_SCRIPTS_ANCHOR = 'commissioning_scripts'


class CommissioningScriptDelete(DeleteView):

    template_name = (
        'maasserver/settings_confirm_delete_commissioning_script.html')
    context_object_name = 'script_to_delete'

    def get_object(self):
        id = self.kwargs.get('id', None)
        return get_object_or_404(Script, id=id)

    def get_next_url(self):
        return reverse('settings') + '#' + COMMISSIONING_SCRIPTS_ANCHOR

    def delete(self, request, *args, **kwargs):
        script = self.get_object()
        script.delete()
        create_audit_event(
            EVENT_TYPES.SETTINGS, ENDPOINT.UI, request, None, description=(
                "Script %s" % script.name + " deleted for '%(username)s'."))
        messages.info(
            request, "Commissioning script %s deleted." % script.name)
        return HttpResponseRedirect(self.get_next_url())


class CommissioningScriptCreate(CreateView):
    template_name = 'maasserver/settings_add_commissioning_script.html'
    form_class = CommissioningScriptForm
    context_object_name = 'commissioningscript'

    def get_success_url(self):
        return reverse('settings') + '#' + COMMISSIONING_SCRIPTS_ANCHOR

    def form_valid(self, form):
        if form.is_valid():
            form.save(self.request)
            messages.info(self.request, "Commissioning script created.")
            return HttpResponseRedirect(self.get_success_url())
        return HttpResponseNotFound()
