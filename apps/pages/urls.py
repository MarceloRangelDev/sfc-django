from django.urls import path
from .views import MiscPagesView



urlpatterns = [
    path(
        "error",
        MiscPagesView.as_view(template_name="pages_misc_error.html"),
        name="pages-misc-error",
    ),
    path(
        "under_maintenance",
        MiscPagesView.as_view(template_name="pages_misc_under_maintenance.html"),
        name="pages-misc-under-maintenance",
    ),
    path(
        "comingsoon",
        MiscPagesView.as_view(template_name="pages_misc_comingsoon.html"),
        name="pages-misc-comingsoon",
    ),
    path(
        "not_authorized",
        MiscPagesView.as_view(template_name="pages_misc_not_authorized.html"),
        name="pages-misc-not-authorized",
    ),
]
