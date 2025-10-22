from django.urls import path, include

urlpatterns = [
    path('api/', include('users.urls')),
    path("api/deposit/", include("payments.urls")),
    path("api/support/", include("support.urls")),
    path("api/boost/", include("boost.urls")),
   path("api/virtualnumbers/", include("virtualnumbers.urls")),

]
