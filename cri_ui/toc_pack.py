"""
The drum packer - pure arithmetic, no database, no Django.

This is the 'exploit' step of drum-buffer-rope and the only genuinely
clever part of TOC: lay the constraint's jobs end to end so it never idles.
Everything else is bookkeeping around it.

It lives here, dependency-free, because two callers need it: the planner's
Constraints screen (inside the container, via the Django ORM) and
loader/toc_run.py (on the host, via docker exec). One algorithm, so the
button and the command line can never drift apart.
"""

from datetime import datetime, timedelta


def slots(days, shifts):
    """
    Turn working DAYS plus shift windows into concrete (start, hours) slots.

    out_resourceplan knows only that "this day has 16 hours". Pack against
    that alone and the first job of every day starts at midnight, hours
    before anyone is on site - so the shift windows have to come in too.

    days   : ["2026-10-01", ...]
    shifts : [(start_time, end_time, {weekday numbers})], Monday = 0
    """
    out = []
    for d in days:
        day = d if isinstance(d, datetime) else datetime.strptime(d, "%Y-%m-%d")
        for start_t, end_t, weekdays in shifts:
            if weekdays and day.weekday() not in weekdays:
                continue
            sh, sm, ss = (int(x) for x in str(start_t).split(":"))
            eh, em, es = (int(x) for x in str(end_t).split(":"))
            hours = ((eh * 3600 + em * 60 + es) - (sh * 3600 + sm * 60 + ss)) / 3600.0
            if hours > 0:
                out.append((day.replace(hour=sh, minute=sm, second=ss), hours))
    out.sort()
    return out


class Lane:
    """One machine of the drum, and how far into its calendar we have got."""

    def __init__(self, name, day_slots):
        self.name = name
        self.slots = day_slots
        self.i = 0
        self.cursor = day_slots[0][0] if day_slots else None
        self.left = day_slots[0][1] if day_slots else 0.0

    def _next(self):
        self.i += 1
        if self.i >= len(self.slots):
            self.cursor = None
            return False
        self.cursor, self.left = self.slots[self.i]
        return True

    def advance_to(self, when):
        """
        Skip forward to the first working moment at or after `when`.

        THE ROPE. A drum job cannot start before its material has come
        through everything upstream. Pull one earlier and it becomes an
        impossible fixed point - which is not a theoretical worry: the
        first version of this ignored it, froze jobs ahead of their own
        material, and the replan then churned at 99% CPU for an hour
        without ever converging.
        """
        while self.cursor is not None and self.cursor < when:
            slot_end = self.cursor + timedelta(hours=self.left)
            if when < slot_end:
                self.left = (slot_end - when).total_seconds() / 3600.0
                self.cursor = when
                return True
            if not self._next():
                return False
        return self.cursor is not None

    def place(self, hours):
        """Consume `hours`, spanning slots if needed. Returns (start, end)."""
        start = self.cursor
        remaining = hours
        while remaining > self.left + 1e-9:
            remaining -= self.left
            if not self._next():
                return None
        self.cursor = self.cursor + timedelta(hours=remaining)
        self.left -= remaining
        end = self.cursor
        if self.left <= 1e-9:
            self._next()
        return start, end


def pack(jobs, calendar, rate=None):
    """
    Lay the drum's jobs end to end across its machines' working slots.

    jobs     : [(ref, quantity, hours, earliest_start_or_None)] in the order
               they should be served - normally due date first
    calendar : {machine: [(slot_start, hours)]}
    rate     : pieces per day for the WHOLE GROUP, or None

    Jobs go to whichever of the drum's machines frees up first, so a work
    centre behaves as one resource with several lanes - which is what a
    work centre is.

    The rate caps pieces per day across the group, not per machine. Nine
    welding machines with a 50/day group rate is a drum that beats fifty
    times, not four hundred and fifty. That distinction is the whole
    reason a rate exists.

    Returns (placed, starved) where placed is
    [(ref, machine, start, end)] and starved counts jobs the rope held back.
    """
    lanes = [Lane(m, s) for m, s in calendar.items() if s]
    if not lanes:
        return [], 0

    placed, per_day, starved = [], {}, 0

    for ref, qty, hours, earliest in jobs:
        live = [l for l in lanes if l.cursor is not None]
        if not live:
            break
        lane = min(live, key=lambda l: l.cursor)

        if earliest and lane.cursor < earliest:
            starved += 1
            if not lane.advance_to(earliest):
                continue

        if rate:
            while lane.cursor is not None:
                used = per_day.get(lane.cursor.date(), 0.0)
                if used + qty <= rate or used == 0.0:
                    break
                lane._next()
            if lane.cursor is None:
                continue

        day = lane.cursor.date()
        spot = lane.place(hours)
        if spot is None:
            continue
        per_day[day] = per_day.get(day, 0.0) + qty
        placed.append((ref, lane.name, spot[0], spot[1]))

    return placed, starved
