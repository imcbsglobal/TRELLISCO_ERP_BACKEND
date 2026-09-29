from django.urls import path, include
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register('posts', views.PostViewSet, basename='post')
router.register('announcements', views.AnnouncementViewSet, basename='announcement')

urlpatterns = [
    path('admin/login/', views.AdminLoginView.as_view(), name='blog-admin-login'),
    path('admin/logout/', views.AdminLogoutView.as_view(), name='blog-admin-logout'),
    path('upload/', views.ContentUploadView.as_view(), name='blog-content-upload'),
    path('', include(router.urls)),
]