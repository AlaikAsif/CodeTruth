from django.urls import path

from myapp import views

urlpatterns = [
    path("", views.home, name="home"),
]

LEGACY_HANDLER = "myapp.views.legacy_view"
