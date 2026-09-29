from django.contrib import admin
from .models import Announcement, Post


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'author', 'status', 'featured', 'publish_at', 'delete_at', 'published_at', 'updated_at')
    list_filter = ('category', 'status', 'featured')
    search_fields = ('title', 'excerpt', 'author', 'tags')
    readonly_fields = ('slug', 'created_at', 'updated_at', 'is_published', 'published_at')


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ('message', 'link_label', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('message', 'link_label')