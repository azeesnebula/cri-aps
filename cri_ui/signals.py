"""
Re-stamp shipment tags the moment a replan finishes.

The solver erases and rebuilds the plan on every run, which wipes the
stamped tags the grids filter on. Tasks live in the database, and the
worker that runs the plan shares this Django instance - so a post-save
signal on the runplan Task refreshes the stamps with no cron, no
manual step, and nothing for a planner to remember.
"""

import logging

from django.db.models.signals import post_save

logger = logging.getLogger(__name__)


def _runplan_done(sender, instance, **kwargs):
    if getattr(instance, "name", "") != "runplan":
        return
    if getattr(instance, "status", "") != "Done":
        return
    db = instance._state.db or "default"
    try:
        from .stamp import stamp_shipments

        ops, rows = stamp_shipments(db)
        logger.info(
            "sieger_ui: stamped %s operation plans and %s constraints "
            "with their DSQ tag on %s", ops, rows, db,
        )
    except Exception:
        # A stamping failure must never take the worker down - the plan
        # itself is already safe; only the shipment filter goes stale.
        logger.exception("sieger_ui: DSQ stamping failed on %s", db)


def connect():
    from freppledb.execute.models import Task

    post_save.connect(_runplan_done, sender=Task)
