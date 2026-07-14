"""Root URL configuration for the Credo Health take-home backend."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("fhir_migration.urls")),
]
