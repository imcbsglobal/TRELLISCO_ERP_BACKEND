import logging

from django.core.cache import cache
from django.utils import timezone

from .models import Announcement, Post

logger = logging.getLogger(__name__)

LAZY_LOCK_KEY = 'blog:scheduler:lock'
LAZY_INTERVAL_SECONDS = 30


def expire_due_posts(now=None):
    """
    Mark published/scheduled posts whose delete_at has passed as 'expired'.
    They go offline for visitors but stay in the admin panel (nothing is
    deleted, images included).
    """
    now = now or timezone.now()
    expired = 0
    for post in Post.objects.filter(
        status__in=('published', 'scheduled'),
        delete_at__isnull=False,
        delete_at__lte=now,
    ):
        try:
            post.status = 'expired'
            post.save()  # Post.save() also clears is_published
            expired += 1
        except Exception:
            logger.exception('Could not expire post id=%s', post.pk)
    return expired


# Backwards-compatible name in case a cron/management command still imports it.
delete_due_posts = expire_due_posts


def publish_due_posts(now=None):
    """Flip scheduled posts to published once publish_at has arrived."""
    now = now or timezone.now()
    published = 0
    for post in Post.objects.filter(status='scheduled', publish_at__lte=now):
        try:
            post.save()  # Post.save() promotes scheduled -> published
            published += 1
        except Exception:
            logger.exception('Could not publish scheduled post id=%s', post.pk)
    return published


def sync_announcements(now=None):
    """
    If a scheduled banner has gone live while an older one is still
    switched on, switch the older one off so only one is ever live.
    Expired banners are not touched (they stay in the admin as 'expired').
    """
    now = now or timezone.now()
    current = Announcement.current(now)
    if current is None:
        return 0
    return Announcement.objects.live(now).exclude(pk=current.pk).update(is_active=False)


def run_due_actions(now=None):
    """
    Runs both jobs. Expiry goes first so a post that is already past its
    delete_at never flashes live for a moment.
    Returns (published_count, expired_count).
    """
    now = now or timezone.now()
    expired = expire_due_posts(now)
    published = publish_due_posts(now)
    try:
        sync_announcements(now)
    except Exception:
        logger.exception('Could not sync announcements')
    return published, expired


def maybe_run_due_actions():
    """
    Safety net used by the API: runs the jobs at most once every
    LAZY_INTERVAL_SECONDS, so schedules still fire (within ~30s) even if
    the cron job is down. cache.add() is atomic, so only one request wins.
    """
    if cache.add(LAZY_LOCK_KEY, 1, LAZY_INTERVAL_SECONDS):
        run_due_actions()