from django.urls import path
from .views import BoostRequestListCreateView, BoostRequestStatusUpdateView, GetCategoryView, GetServicePriceView

urlpatterns = [
    path("", BoostRequestListCreateView.as_view(), name="boost-list-create"),
    path("status/", BoostRequestStatusUpdateView.as_view(), name="boost-status-update"),
    path("categories/", GetCategoryView.as_view(), name="get-categories"),
    path("price/", GetServicePriceView.as_view(), name="get-service-price"),
]
