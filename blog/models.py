from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify


CATEGORY_CHOICES = [
    ('company', 'Company'),
    ('hrms', 'HRMS'),
    ('restaurant', 'Restaurant'),
    ('property', 'Property'),
    ('gym', 'Gym'),
]

STATUS_CHOICES = [
    ('draft', 'Draft'),
    ('scheduled', 'Scheduled'),
    ('published', 'Published'),
    ('archived', 'Archived'),
    ('expired', 'Expired'),
]


class Post(models.Model):
    # ── Post details ──────────────────────────────
    title = models.CharField(max_length=220)
    slug = models.SlugField(max_length=240, unique=True, blank=True)  # auto-generated, not admin-editable
    excerpt = models.TextField(
        help_text='Short lead paragraph shown at the top of the post and in listings.'
    )
    author = models.CharField(max_length=120)
    category = models.CharField(max_length=40, choices=CATEGORY_CHOICES, default='company')
    category_label = models.CharField(
        max_length=60,
        blank=True,
        help_text='Optional override for the category badge text. Falls back to the category display name.',
    )
    tags = models.JSONField(default=list, blank=True, help_text='List of tag strings, e.g. ["HRMS", "Payroll"].')
    read_time = models.CharField(max_length=20, blank=True, help_text='e.g. "5 min read"')

    # ── Cover image (direct upload, no URL field) ─
    cover_image = models.ImageField(upload_to='blog/covers/', blank=True, null=True)
    cover_image_alt = models.CharField(max_length=200, blank=True)
    cover_image_caption = models.CharField(max_length=200, blank=True)

    # ── Content: block-based editor ───────────────
    # List of content blocks, each with a "type" and type-specific fields.
    # Supported types: heading, paragraph, image, video, quote,
    # bullet_list, numbered_list, code, cta, divider.
    # Image/video blocks store an uploaded file URL (populated via the
    # /api/blog/upload/ endpoint) — never a hand-typed URL.
    # Example:
    # [
    #   {"id": "b1", "type": "heading", "text": "Why it matters"},
    #   {"id": "b2", "type": "paragraph", "text": "..."},
    #   {"id": "b3", "type": "image", "image": "/media/blog/content/foo.jpg", "alt": "...", "caption": "..."},
    #   {"id": "b4", "type": "video", "video": "/media/blog/content/foo.mp4", "caption": "..."},
    #   {"id": "b5", "type": "quote", "text": "..."},
    #   {"id": "b6", "type": "bullet_list", "items": ["...", "..."]},
    #   {"id": "b7", "type": "numbered_list", "items": ["...", "..."]},
    #   {"id": "b8", "type": "code", "code": "...", "language": "js"},
    #   {"id": "b9", "type": "cta", "label": "Learn more", "url": "/pricing"},
    #   {"id": "b10", "type": "divider"}
    # ]
    body = models.JSONField(default=list, blank=True)

    # ── SEO & social ───────────────────────────────
    seo_title = models.CharField(max_length=220, blank=True)
    meta_description = models.CharField(max_length=320, blank=True)
    seo_keywords = models.JSONField(default=list, blank=True)
    canonical_url = models.URLField(blank=True)
    social_share_image = models.ImageField(upload_to='blog/social/', blank=True, null=True)

    # ── Publishing ─────────────────────────────────
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    featured = models.BooleanField(default=False)
    allow_comments = models.BooleanField(default=True)
    publish_at = models.DateTimeField(
        blank=True, null=True,
        help_text='When the post should go (or went) live. Used for scheduling.',
    )
    # Auto-expiry. Leave empty and the post never expires. When this time
    # passes, the post is taken offline and marked "expired" (it is NOT
    # deleted — it stays visible in the admin panel) by
    # blog.scheduling.run_due_actions().
    delete_at = models.DateTimeField(
        blank=True, null=True, db_index=True,
        help_text='Optional. When set, the post is taken offline and marked Expired at this time. Leave empty to keep it live forever.',
    )

    # Kept for backwards compatibility with the existing frontend/API contract.
    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-published_at', '-created_at']

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)[:240]

        now = timezone.now()

        # Past its expiry time -> expired (offline, but kept in the admin).
        if self.delete_at and self.delete_at <= now and self.status in ('published', 'scheduled'):
            self.status = 'expired'

        # Normalise status against the schedule:
        #  - "published" with a future publish_at is really a scheduled post
        #  - "scheduled" whose publish_at has arrived goes live
        if self.status == 'published' and self.publish_at and self.publish_at > now:
            self.status = 'scheduled'
        elif self.status == 'scheduled' and self.publish_at and self.publish_at <= now:
            self.status = 'published'

        # Keep the legacy is_published/published_at fields in sync with status.
        if self.status == 'published':
            self.is_published = True
            self.published_at = self.publish_at or self.published_at or now
        elif self.status == 'scheduled':
            self.is_published = False
            self.published_at = None
        elif self.status == 'expired':
            # Offline, but keep the original publish date for the record.
            self.is_published = False
        else:
            self.is_published = False
            self.published_at = None

        super().save(*args, **kwargs)

    @property
    def display_category_label(self):
        return self.category_label or dict(CATEGORY_CHOICES).get(self.category, self.category)

    @property
    def date(self):
        """Formatted date string for the frontend's `post.date` field."""
        source = self.published_at or self.created_at
        return source.strftime('%b %d, %Y') if source else ''


class AnnouncementQuerySet(models.QuerySet):
    def live(self, now=None):
        """Switched on AND inside its show/hide window right now."""
        now = now or timezone.now()
        return (
            self.filter(is_active=True)
            .filter(Q(publish_at__isnull=True) | Q(publish_at__lte=now))
            .filter(Q(delete_at__isnull=True) | Q(delete_at__gt=now))
        )


class Announcement(models.Model):
    """
    Site-wide banner shown at the very top of the public navbar.
    Only one announcement is live at a time. It can be scheduled:
    publish_at = when it appears, delete_at = when it is automatically
    taken down (it stays in the admin list as "expired", not deleted).
    """

    message = models.CharField(
        max_length=300,
        help_text='Banner text. Wrap words in **double asterisks** to make them bold.',
    )
    link_label = models.CharField(max_length=80, blank=True, help_text='e.g. "Explore HRMS"')
    link_url = models.CharField(
        max_length=500, blank=True,
        help_text='Full URL (https://...) or a site path (/blog).',
    )
    is_active = models.BooleanField(default=False)

    # ── Scheduling (both optional) ─────────────────
    publish_at = models.DateTimeField(
        blank=True, null=True,
        help_text='Optional. The banner appears at this time. Empty = as soon as it is switched on.',
    )
    delete_at = models.DateTimeField(
        blank=True, null=True, db_index=True,
        help_text='Optional. The banner is automatically taken down at this time. Empty = stays until turned off.',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = AnnouncementQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.message[:60]

    @property
    def status(self):
        """hidden | scheduled | live | expired — computed, so it is always current."""
        now = timezone.now()
        if not self.is_active:
            return 'hidden'
        if self.delete_at and self.delete_at <= now:
            return 'expired'
        if self.publish_at and self.publish_at > now:
            return 'scheduled'
        return 'live'

    @classmethod
    def current(cls, now=None):
        """The one banner visitors should see right now (or None)."""
        live = list(cls.objects.live(now))
        if not live:
            return None
        # If two overlap, the one that started most recently wins.
        return max(live, key=lambda a: (a.publish_at or a.created_at, a.pk))

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Going live now replaces whatever is live now. A banner scheduled
        # for later leaves the current one alone until its time comes.
        if self.status == 'live':
            Announcement.objects.live().exclude(pk=self.pk).update(is_active=False)