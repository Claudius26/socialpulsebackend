from django.urls import path, include

urlpatterns = [
    path('api/', include('users.urls')),
    path("api/deposit/", include("payments.urls")),
    path("api/support/", include("support.urls")),
    path("api/boost/", include("boost.urls")),
   path("api/virtualnumbers/", include("virtualnumbers.urls")),
    path("api/developer/", include("developer.urls")),
    path("api/v1/", include("developer.urls_v1")),
    path("api/v1/cardpulse/", include("cardpulse.urls")),


]
