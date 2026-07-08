from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save)
def on_save(sender, instance, **kwargs):
    return "invoked through the signal system, never called directly"
