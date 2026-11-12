from django.contrib import admin
from .models import Calendar, Event

@admin.register(Calendar)
class CalendarAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'is_internal', 'is_public', 'uuid')
    list_filter = ('is_internal', 'is_public')
    search_fields = ('name', 'user__username')

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'calendar', 'user', 'start_date', 'end_date', 'all_day')
    list_filter = ('calendar__is_internal', 'start_date')
    search_fields = ('title', 'description', 'location')
