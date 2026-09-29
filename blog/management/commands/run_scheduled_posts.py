from django.core.management.base import BaseCommand

from blog.scheduling import run_due_actions


class Command(BaseCommand):
    help = 'Publish scheduled blog posts and delete expired ones.'

    def handle(self, *args, **options):
        published, deleted = run_due_actions()
        self.stdout.write(f'Published: {published}, deleted: {deleted}')
