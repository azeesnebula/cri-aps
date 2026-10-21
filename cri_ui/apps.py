from django.apps import AppConfig


class CRIPumpsUIConfig(AppConfig):
    name = "cri_ui"
    verbose_name = "C.R.I Pumps Planning"
    default_auto_field = "django.db.models.AutoField"

    def ready(self):
        # Refresh the DSQ stamps whenever a replan finishes, so the
        # shipment filters on the core screens never go stale.
        from . import signals

        signals.connect()
