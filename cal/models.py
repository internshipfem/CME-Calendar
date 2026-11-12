import uuid
from django.db import models
from django.contrib.auth.models import User

class Calendar(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    name = models.CharField(max_length=100)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='calendars')
    is_public = models.BooleanField(default=False)
    is_internal = models.BooleanField(default=False, db_index=True)
    subscribers = models.ManyToManyField(User, related_name='subscribed_calendars', blank=True)

    def __str__(self):
        return f"{self.name} (Internal)" if self.is_internal else self.name

class Event(models.Model):
    calendar = models.ForeignKey(Calendar, on_delete=models.CASCADE, related_name='events', null=True, blank=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    location = models.CharField(max_length=255, blank=True, null=True)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    color = models.CharField(max_length=20, default='#039be5')
    all_day = models.BooleanField(default=False)
    recurring_rule = models.CharField(max_length=255, blank=True, null=True)
    exception_dates = models.TextField(blank=True, null=True)
    recurrence_until = models.DateTimeField(blank=True, null=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    def __str__(self):
        return self.title

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description or "",
            'location': self.location or "",
            'start': self.start_date.isoformat() if self.start_date else None,
            'end': self.end_date.isoformat() if self.end_date else None,
            'color': self.color,
            'allDay': self.all_day,
            'rrule': self.recurring_rule,
            'exdates': self.exception_dates,
            'recurrenceUntil': self.recurrence_until.isoformat() if self.recurrence_until else None,
            'user': self.user.username,
            'calendar_id': str(self.calendar.uuid) if self.calendar else None,
        }
