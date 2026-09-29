from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.db.models import Q
from django.utils import timezone

from rest_framework import status, viewsets, permissions
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Announcement, Post
from .scheduling import maybe_run_due_actions
from .serializers import (
    AdminLoginSerializer,
    AnnouncementSerializer,
    ContentUploadSerializer,
    PostListSerializer,
    PostSerializer,
)


# ─────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────
class AdminLoginView(APIView):
    """
    POST /api/blog/admin/login/
    Body: { "email": "...", "password": "..." }
    Returns: { "token": "...", "user": {...} }
    Only staff users can sign in here.
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = AdminLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email'].strip().lower()
        password = serializer.validated_data['password']

        try:
            user_record = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            return Response(
                {'detail': 'Invalid email or password.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        except User.MultipleObjectsReturned:
            return Response(
                {'detail': 'Multiple accounts share this email. Contact an administrator.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = authenticate(request, username=user_record.username, password=password)

        if user is None:
            return Response(
                {'detail': 'Invalid email or password.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_staff:
            return Response(
                {'detail': 'This account does not have admin access.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        token, _ = Token.objects.get_or_create(user=user)

        return Response(
            {
                'token': token.key,
                'user': {
                    'id': user.id,
                    'email': user.email,
                    'name': user.get_full_name() or user.username,
                },
            },
            status=status.HTTP_200_OK,
        )


class AdminLogoutView(APIView):
    """POST /api/blog/admin/logout/ — invalidates the caller's token."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────
# POSTS
# ─────────────────────────────────────────────
class IsAdminOrReadOnly(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class PostViewSet(viewsets.ModelViewSet):
    """
    /api/blog/posts/              GET (list, published only unless staff), POST (staff)
    /api/blog/posts/<slug>/       GET, PUT/PATCH, DELETE (staff)
    /api/blog/posts/<slug>/publish/   POST (staff) — toggle publish state
    """

    queryset = Post.objects.all()
    serializer_class = PostSerializer
    permission_classes = [IsAdminOrReadOnly]
    parser_classes = [MultiPartParser, FormParser]
    lookup_field = 'slug'

    def get_queryset(self):
        # Safety net: fire due publishes/deletes even if cron is down
        # (throttled to once per ~30s).
        maybe_run_due_actions()

        qs = super().get_queryset()
        if self.request.user.is_authenticated and self.request.user.is_staff:
            return qs
        # Public: only live posts, and never one that's past its delete time.
        return qs.filter(is_published=True).filter(
            Q(delete_at__isnull=True) | Q(delete_at__gt=timezone.now())
        )

    def get_serializer_class(self):
        if self.action == 'list':
            return PostListSerializer
        return PostSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAdminUser])
    def publish(self, request, slug=None):
        post = self.get_object()
        # Published, scheduled OR expired -> draft (cancels a pending
        # schedule; an expired post needs its expiry date changed before
        # it can go live again). Anything else -> published (the model
        # turns it into "scheduled" automatically if publish_at is still
        # in the future).
        post.status = 'draft' if post.status in ('published', 'scheduled', 'expired') else 'published'
        post.save()
        return Response(self.get_serializer(post).data)


# ─────────────────────────────────────────────
# ANNOUNCEMENTS (navbar banner)
# ─────────────────────────────────────────────
class AnnouncementViewSet(viewsets.ModelViewSet):
    """
    /api/blog/announcements/           GET (staff: all, public: live now only), POST (staff)
    /api/blog/announcements/<id>/      GET, PUT/PATCH, DELETE (staff)
    /api/blog/announcements/active/    GET (public) — the banner to show, or null
    """

    queryset = Announcement.objects.all()
    serializer_class = AnnouncementSerializer
    permission_classes = [IsAdminOrReadOnly]

    def get_queryset(self):
        # Safety net: fire due schedules even if cron is down.
        maybe_run_due_actions()

        qs = super().get_queryset()
        if self.request.user.is_authenticated and self.request.user.is_staff:
            return qs
        # Public: only banners that are live right now (inside their window).
        return qs.live()

    @action(detail=False, methods=['get'], permission_classes=[permissions.AllowAny])
    def active(self, request):
        maybe_run_due_actions()
        announcement = Announcement.current()
        if announcement is None:
            return Response(None)
        return Response(self.get_serializer(announcement).data)


# ─────────────────────────────────────────────
# CONTENT UPLOADS (for image/video content blocks)
# ─────────────────────────────────────────────
class ContentUploadView(APIView):
    """
    POST /api/blog/upload/
    Multipart body: { "file": <image or video> }
    Returns: { "url": "https://.../media/blog/content/<file>" }

    Used by the content block editor so admins upload images/videos
    directly instead of pasting a URL. The returned URL is what gets
    stored inside a post's `body` image/video block.
    """

    permission_classes = [permissions.IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    ALLOWED_PREFIXES = ('image/', 'video/')
    MAX_BYTES = 50 * 1024 * 1024  # 50MB

    def post(self, request):
        serializer = ContentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        uploaded = serializer.validated_data['file']

        content_type = uploaded.content_type or ''
        if not content_type.startswith(self.ALLOWED_PREFIXES):
            return Response(
                {'detail': 'Only image or video files are allowed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if uploaded.size > self.MAX_BYTES:
            return Response(
                {'detail': 'File is too large (50MB max).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        saved_path = default_storage.save(f'blog/content/{uploaded.name}', uploaded)
        url = default_storage.url(saved_path)
        url = request.build_absolute_uri(url)

        return Response(
            {'url': url, 'type': 'video' if content_type.startswith('video/') else 'image'},
            status=status.HTTP_201_CREATED,
        )