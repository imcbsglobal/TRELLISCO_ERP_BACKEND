import json

from django.core.validators import URLValidator
from django.utils import timezone
from rest_framework import serializers
from .models import Announcement, Post, PromoVideo


class JSONStringField(serializers.Field):
    """
    Accepts a real list/dict (JSON requests) OR a JSON-encoded string
    (multipart FormData, which can only send strings). Always stores
    the parsed Python value, never the raw string.
    """

    def __init__(self, expected_type=list, **kwargs):
        self.expected_type = expected_type
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        if isinstance(data, (bytes, bytearray)):
            data = data.decode('utf-8')
        # Unwrap up to twice, in case a value was double-encoded.
        for _ in range(2):
            if isinstance(data, str):
                try:
                    data = json.loads(data) if data.strip() else self.expected_type()
                except ValueError:
                    raise serializers.ValidationError('Invalid JSON.')
        if not isinstance(data, self.expected_type):
            raise serializers.ValidationError(
                f'Expected a {self.expected_type.__name__}.'
            )
        return data

    def to_representation(self, value):
        # Heals rows that were previously saved as a JSON string.
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return self.expected_type()
        return value if value is not None else self.expected_type()


class PostSerializer(serializers.ModelSerializer):
    """
    Field names match what the frontend expects (categoryLabel, readTime,
    coverImage, etc.) so the admin form and public blog need no reshaping.
    Slug is read-only everywhere — it's auto-generated server-side and
    never exposed as an editable admin field.
    """

    categoryLabel = serializers.CharField(source='display_category_label', read_only=True)
    categoryLabelOverride = serializers.CharField(
        source='category_label', required=False, allow_blank=True, max_length=60
    )
    readTime = serializers.CharField(source='read_time', required=False, allow_blank=True, max_length=20)

    tags = JSONStringField(required=False)
    body = JSONStringField(required=False)

    coverImage = serializers.ImageField(source='cover_image', required=False, allow_null=True)
    coverImageAlt = serializers.CharField(source='cover_image_alt', required=False, allow_blank=True, max_length=200)
    coverImageCaption = serializers.CharField(
        source='cover_image_caption', required=False, allow_blank=True, max_length=200
    )

    socialShareImage = serializers.ImageField(source='social_share_image', required=False, allow_null=True)
    seoTitle = serializers.CharField(source='seo_title', required=False, allow_blank=True, max_length=220)
    metaDescription = serializers.CharField(
        source='meta_description', required=False, allow_blank=True, max_length=320
    )
    seoKeywords = JSONStringField(source='seo_keywords', required=False)
    canonicalUrl = serializers.CharField(
        source='canonical_url', required=False, allow_blank=True, max_length=500
    )

    allowComments = serializers.BooleanField(source='allow_comments', required=False)
    publishAt = serializers.DateTimeField(source='publish_at', required=False, allow_null=True)
    deleteAt = serializers.DateTimeField(source='delete_at', required=False, allow_null=True)

    date = serializers.ReadOnlyField()

    class Meta:
        model = Post
        fields = [
            'id',
            'title',
            'slug',
            'excerpt',
            'author',
            'category',
            'categoryLabel',
            'categoryLabelOverride',
            'tags',
            'readTime',
            'coverImage',
            'coverImageAlt',
            'coverImageCaption',
            'body',
            'seoTitle',
            'metaDescription',
            'seoKeywords',
            'canonicalUrl',
            'socialShareImage',
            'status',
            'featured',
            'allowComments',
            'publishAt',
            'deleteAt',
            'date',
            'is_published',
            'published_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'slug', 'categoryLabel', 'date',
            'is_published', 'published_at', 'created_at', 'updated_at',
        ]

    def validate_canonicalUrl(self, value):
        value = (value or '').strip()
        if not value:
            return ''
        URLValidator(message='Enter a valid URL.')(value)
        return value


class PostListSerializer(PostSerializer):
    """Lighter shape for list views — drops the full body block list."""

    class Meta(PostSerializer.Meta):
        fields = [f for f in PostSerializer.Meta.fields if f != 'body']


class AdminLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class ContentUploadSerializer(serializers.Serializer):
    file = serializers.FileField()


class BlankToNullDateTimeField(serializers.DateTimeField):
    """Treats '' like null so clearing a date in the admin form really clears it."""

    def to_internal_value(self, value):
        if value in ('', None):
            return None
        return super().to_internal_value(value)


class AnnouncementSerializer(serializers.ModelSerializer):
    """Camel-cased to match the rest of the frontend contract."""

    linkLabel = serializers.CharField(source='link_label', required=False, allow_blank=True, max_length=80)
    linkUrl = serializers.CharField(source='link_url', required=False, allow_blank=True, max_length=500)
    isActive = serializers.BooleanField(source='is_active', required=False)
    publishAt = BlankToNullDateTimeField(source='publish_at', required=False, allow_null=True)
    deleteAt = BlankToNullDateTimeField(source='delete_at', required=False, allow_null=True)
    status = serializers.ReadOnlyField()  # hidden | scheduled | live | expired

    class Meta:
        model = Announcement
        fields = [
            'id', 'message', 'linkLabel', 'linkUrl', 'isActive',
            'publishAt', 'deleteAt', 'status', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'status', 'created_at', 'updated_at']

    def validate_message(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Announcement text is required.')
        return value

    def validate_linkUrl(self, value):
        value = (value or '').strip()
        if not value:
            return ''
        # Only http(s) URLs or site-relative paths. Blocks javascript:/data: links.
        if value.startswith('/') and not value.startswith('//'):
            return value
        URLValidator(schemes=['http', 'https'], message='Enter a full URL (https://...) or a path like /blog.')(value)
        return value

    def validate(self, attrs):
        if attrs.get('link_label') and not attrs.get('link_url', self.instance.link_url if self.instance else ''):
            raise serializers.ValidationError({'linkUrl': 'Add a link URL, or clear the link text.'})

        publish_at = attrs.get('publish_at', self.instance.publish_at if self.instance else None)
        delete_at = attrs.get('delete_at', self.instance.delete_at if self.instance else None)
        if attrs.get('delete_at') and attrs['delete_at'] <= timezone.now():
            raise serializers.ValidationError({'deleteAt': 'Hide time must be in the future.'})
        if publish_at and delete_at and delete_at <= publish_at:
            raise serializers.ValidationError({'deleteAt': 'Hide time must be after the show time.'})
        return attrs


class PromoVideoSerializer(serializers.ModelSerializer):
    """Camel-cased. Accepts multipart (video upload) or JSON (toggles)."""

    MAX_VIDEO_BYTES = 50 * 1024 * 1024  # 50MB

    video = serializers.FileField(required=False)
    title = serializers.CharField(required=False, allow_blank=True, max_length=80)
    subtitle = serializers.CharField(required=False, allow_blank=True, max_length=80)
    buttonLabel = serializers.CharField(source='button_label', required=False, allow_blank=True, max_length=40)
    buttonUrl = serializers.CharField(source='button_url', required=False, allow_blank=True, max_length=500)
    isActive = serializers.BooleanField(source='is_active', required=False)
    publishAt = BlankToNullDateTimeField(source='publish_at', required=False, allow_null=True)
    deleteAt = BlankToNullDateTimeField(source='delete_at', required=False, allow_null=True)
    status = serializers.ReadOnlyField()  # hidden | scheduled | live | expired

    class Meta:
        model = PromoVideo
        fields = [
            'id', 'video', 'title', 'subtitle', 'buttonLabel', 'buttonUrl',
            'isActive', 'publishAt', 'deleteAt', 'status', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'status', 'created_at', 'updated_at']

    def validate_video(self, value):
        content_type = getattr(value, 'content_type', '') or ''
        if not content_type.startswith('video/'):
            raise serializers.ValidationError('Only video files are allowed (mp4, webm, mov).')
        if value.size > self.MAX_VIDEO_BYTES:
            raise serializers.ValidationError('Video is too large (50MB max).')
        return value

    def validate_buttonUrl(self, value):
        value = (value or '').strip()
        if not value:
            return ''
        if value.startswith('/') and not value.startswith('//'):
            return value
        URLValidator(schemes=['http', 'https'], message='Enter a full URL (https://...) or a path like /contact.')(value)
        return value

    def validate(self, attrs):
        if not self.instance and not attrs.get('video'):
            raise serializers.ValidationError({'video': 'Upload a video.'})

        publish_at = attrs.get('publish_at', self.instance.publish_at if self.instance else None)
        delete_at = attrs.get('delete_at', self.instance.delete_at if self.instance else None)
        if attrs.get('delete_at') and attrs['delete_at'] <= timezone.now():
            raise serializers.ValidationError({'deleteAt': 'Hide time must be in the future.'})
        if publish_at and delete_at and delete_at <= publish_at:
            raise serializers.ValidationError({'deleteAt': 'Hide time must be after the show time.'})
        return attrs