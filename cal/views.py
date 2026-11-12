from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.db.models import Q
from .models import Event, Calendar
import json
import icalendar
from datetime import datetime, date
import pytz

@login_required
def calendar_view(request):
    # Ensure user has at least one default calendar
    if not request.user.calendars.exists():
        Calendar.objects.create(name=request.user.username, user=request.user, is_public=True)
    
    # Internal CME schedules are automatically distributed to ALL authenticated CME users
    internal_schedules = Calendar.objects.filter(is_internal=True)
    # Personal calendars owned by user (excluding internal schedules)
    personal_calendars = request.user.calendars.filter(is_internal=False)
    subscribed_calendars = request.user.subscribed_calendars.filter(is_internal=False)
    
    selected_calendar_uuid = request.GET.get('calendar_uuid')
    selected_calendar = None
    
    if selected_calendar_uuid:
        # 1. Check if user owns it
        selected_calendar = Calendar.objects.filter(uuid=selected_calendar_uuid, user=request.user).first()
        # 2. Check if it is an internal CME schedule (all authenticated users have read access)
        if not selected_calendar:
            selected_calendar = Calendar.objects.filter(uuid=selected_calendar_uuid, is_internal=True).first()
        # 3. Check if user is subscribed to it
        if not selected_calendar:
            selected_calendar = Calendar.objects.filter(uuid=selected_calendar_uuid, subscribers=request.user).first()
        # 4. Fallback for public preview
        if not selected_calendar:
            selected_calendar = get_object_or_404(Calendar, uuid=selected_calendar_uuid, is_public=True)
    else:
        # Default calendar selection: user's personal first, or first internal schedule
        selected_calendar = personal_calendars.first() or request.user.calendars.first() or internal_schedules.first()

    # Determine edit permissions:
    # Internal CME schedules: Only staff users or creator can edit
    # Personal calendars: Only the calendar creator can edit
    if selected_calendar.is_internal:
        can_edit = bool(request.user.is_staff or selected_calendar.user == request.user)
    else:
        can_edit = bool(selected_calendar.user == request.user)

    events = Event.objects.filter(calendar=selected_calendar)
    events_list = [event.to_dict() for event in events]
    
    context = {
        'initial_events_json': json.dumps(events_list),
        'calendars': personal_calendars,
        'internal_schedules': internal_schedules,
        'subscribed_calendars': subscribed_calendars,
        'selected_calendar': selected_calendar,
        'can_edit': can_edit,
        'is_staff': request.user.is_staff,
    }
    return render(request, 'cal/calendar.html', context)

@login_required
def create_calendar(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        is_internal = (request.POST.get('is_internal') in ('on', 'true', '1')) and request.user.is_staff
        if name:
            new_cal = Calendar.objects.create(
                name=name,
                user=request.user,
                is_public=True if is_internal else False,
                is_internal=is_internal
            )
            if is_internal:
                messages.success(request, f"Internal CME Schedule '{name}' created successfully!")
            else:
                messages.success(request, f"Calendar '{name}' created successfully!")
            return redirect(f"/?calendar_uuid={new_cal.uuid}")
    return redirect('calendar')

@login_required
def import_ics(request):
    if request.method == 'POST' and request.FILES.get('ics_file'):
        calendar_uuid = request.POST.get('calendar_uuid')
        cal_obj = get_object_or_404(Calendar, uuid=calendar_uuid)
        
        # Permission check: must be owner, or if internal, user must be staff
        if not (cal_obj.user == request.user or (cal_obj.is_internal and request.user.is_staff)):
            messages.error(request, "Permission denied to import into this schedule.")
            return redirect('calendar')
        
        ics_file = request.FILES['ics_file']
        cal = icalendar.Calendar.from_ical(ics_file.read())
        
        imported_count = 0
        for component in cal.walk():
            if component.name == "VEVENT":
                title = component.get('summary')
                description = component.get('description')
                location = component.get('location')
                
                dtstart = component.get('dtstart')
                dtend = component.get('dtend')
                
                if not dtstart:
                    continue
                    
                start = dtstart.dt
                end = dtend.dt if dtend else start
                
                # Convert date to datetime if necessary
                if isinstance(start, date) and not isinstance(start, datetime):
                    start = datetime.combine(start, datetime.min.time())
                    start = pytz.utc.localize(start)
                if isinstance(end, date) and not isinstance(end, datetime):
                    end = datetime.combine(end, datetime.min.time())
                    end = pytz.utc.localize(end)
                    
                Event.objects.create(
                    calendar=cal_obj,
                    title=str(title) if title else "Untitled Event",
                    description=str(description) if description else "",
                    location=str(location) if location else "",
                    start_date=start,
                    end_date=end,
                    user=request.user
                )
                imported_count += 1
    
        messages.success(request, f"Successfully imported {imported_count} event(s)!")
        url = f"/?calendar_uuid={calendar_uuid}" if calendar_uuid else "/"
        return redirect(url)
    return redirect('calendar')

@login_required
def export_ics(request):
    calendar_uuid = request.GET.get('calendar_uuid')
    if calendar_uuid:
        cal_obj = get_object_or_404(Calendar, uuid=calendar_uuid)
        # Accessible if owner, subscriber, internal schedule, or public
        if not (cal_obj.user == request.user or cal_obj.is_internal or cal_obj.is_public or request.user in cal_obj.subscribers.all()):
            return redirect('calendar')
        selected_calendar = cal_obj
    else:
        selected_calendar = request.user.calendars.first()
        
    if not selected_calendar:
        return redirect('calendar')
        
    events = Event.objects.filter(calendar=selected_calendar)
    
    cal = icalendar.Calendar()
    cal.add('prodid', '-//CME Calendar//')
    cal.add('version', '2.0')
    
    for event in events:
        ievent = icalendar.Event()
        ievent.add('summary', event.title)
        ievent.add('description', event.description or "")
        ievent.add('location', event.location or "")
        ievent.add('dtstart', event.start_date)
        ievent.add('dtend', event.end_date)
        cal.add_component(ievent)
        
    response = HttpResponse(cal.to_ical(), content_type="text/calendar")
    response['Content-Disposition'] = f'attachment; filename="{selected_calendar.name}.ics"'
    return response

@login_required
def rename_calendar(request, calendar_uuid):
    if request.method == 'POST':
        calendar = get_object_or_404(Calendar, uuid=calendar_uuid)
        if not (calendar.user == request.user or (calendar.is_internal and request.user.is_staff)):
            messages.error(request, "Permission denied.")
            return redirect('calendar')
        new_name = request.POST.get('name')
        if new_name:
            calendar.name = new_name
            calendar.save()
            messages.success(request, f"Schedule renamed to '{new_name}'.")
    return redirect('calendar')

@login_required
def delete_calendar(request, calendar_uuid):
    if request.method == 'POST':
        calendar = get_object_or_404(Calendar, uuid=calendar_uuid)
        if not (calendar.user == request.user or (calendar.is_internal and request.user.is_staff)):
            messages.error(request, "Permission denied.")
            return redirect('calendar')
        default_calendar = request.user.calendars.order_by('id').first()
        if default_calendar and calendar.uuid == default_calendar.uuid:
            messages.error(request, "The default calendar cannot be deleted.")
        else:
            calendar.delete()
            messages.success(request, "Schedule deleted successfully.")
    return redirect('calendar')

@login_required
def browse_calendars(request):
    query = request.GET.get('q', '')
    public_calendars = Calendar.objects.filter(is_public=True, is_internal=False).exclude(user=request.user)
    if query:
        public_calendars = public_calendars.filter(
            Q(name__icontains=query) | Q(user__username__icontains=query)
        )
    else:
        public_calendars = public_calendars.order_by('id')[:10]
    context = {
        'calendars': public_calendars,
        'query': query,
        'subscribed_uuids': request.user.subscribed_calendars.values_list('uuid', flat=True)
    }
    return render(request, 'cal/browse_calendars.html', context)

@login_required
def subscribe_calendar(request, calendar_uuid):
    if request.method == 'POST':
        calendar = get_object_or_404(Calendar, uuid=calendar_uuid, is_public=True)
        calendar.subscribers.add(request.user)
        messages.success(request, f"Subscribed to {calendar.name}.")
        return redirect(request.META.get('HTTP_REFERER', 'browse_calendars'))
    return redirect('browse_calendars')

@login_required
def unsubscribe_calendar(request, calendar_uuid):
    if request.method == 'POST':
        calendar = get_object_or_404(Calendar, uuid=calendar_uuid, subscribers=request.user)
        calendar.subscribers.remove(request.user)
        messages.success(request, f"Unsubscribed from {calendar.name}.")
        return redirect('calendar')
    return redirect('calendar')

@login_required
def preview_calendar(request, calendar_uuid):
    calendar = get_object_or_404(Calendar, uuid=calendar_uuid, is_public=True)
    events = Event.objects.filter(calendar=calendar)
    events_list = [event.to_dict() for event in events]
    
    context = {
        'initial_events_json': json.dumps(events_list),
        'selected_calendar': calendar,
        'is_preview_mode': True,
        'subscribed_calendars': request.user.subscribed_calendars.all()
    }
    return render(request, 'cal/calendar.html', context)

@login_required
def calendar_settings(request):
    calendars = request.user.calendars.all()
    all_internal = Calendar.objects.filter(is_internal=True) if request.user.is_staff else Calendar.objects.none()
    context = {
        'calendars': calendars,
        'all_internal': all_internal,
        'is_staff': request.user.is_staff,
    }
    return render(request, 'cal/settings.html', context)

@login_required
def update_visibility(request, calendar_uuid):
    if request.method == 'POST':
        calendar = get_object_or_404(Calendar, uuid=calendar_uuid)
        if not (calendar.user == request.user or (calendar.is_internal and request.user.is_staff)):
            messages.error(request, "Permission denied.")
            return redirect('calendar_settings')
            
        if 'is_internal' in request.POST and request.user.is_staff:
            is_internal = request.POST.get('is_internal') == 'true'
            calendar.is_internal = is_internal
            if is_internal:
                calendar.is_public = True
            calendar.save()
            messages.success(request, f"'{calendar.name}' internal schedule status updated.")
        else:
            is_public = request.POST.get('is_public') == 'true'
            calendar.is_public = is_public
            calendar.save()
            messages.success(request, f"'{calendar.name}' visibility updated.")
    return redirect('calendar_settings')
