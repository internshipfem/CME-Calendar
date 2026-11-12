import json
import urllib.parse
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import Event, Calendar
from dateutil import parser
from datetime import datetime
import pytz

class CalendarConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # Reject unauthenticated users
        if self.scope["user"].is_anonymous:
            await self.close()
            return

        self.joined_groups = set()
        
        # Parse calendar_id from query string: /ws/calendar/?calendar_id=<uuid>
        query_string = self.scope.get("query_string", b"").decode("utf-8")
        params = urllib.parse.parse_qs(query_string)
        calendar_id = params.get("calendar_id", [None])[0]

        if calendar_id:
            has_access = await self.check_calendar_access(calendar_id)
            if has_access:
                group_name = f"calendar_{calendar_id}"
                await self.channel_layer.group_add(group_name, self.channel_name)
                self.joined_groups.add(group_name)

        # Also join fallback default group
        self.default_group = 'calendar_updates'
        await self.channel_layer.group_add(self.default_group, self.channel_name)
        self.joined_groups.add(self.default_group)

        await self.accept()

    async def disconnect(self, close_code):
        for group in getattr(self, "joined_groups", []):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        action = data.get('action')

        if action == 'join':
            cal_id = data.get('calendar_id')
            if cal_id:
                has_access = await self.check_calendar_access(cal_id)
                if has_access:
                    group_name = f"calendar_{cal_id}"
                    if group_name not in self.joined_groups:
                        await self.channel_layer.group_add(group_name, self.channel_name)
                        self.joined_groups.add(group_name)
            return

        if action == 'create':
            event = await self.create_event(data)
            if event and event.get('calendar_id'):
                target_group = f"calendar_{event['calendar_id']}"
                await self.channel_layer.group_send(
                    target_group,
                    {
                        'type': 'calendar_update',
                        'action': 'create',
                        'event': event
                    }
                )
        elif action == 'update':
            event = await self.update_event(data)
            if event and event.get('calendar_id'):
                target_group = f"calendar_{event['calendar_id']}"
                await self.channel_layer.group_send(
                    target_group,
                    {
                        'type': 'calendar_update',
                        'action': 'update',
                        'event': event
                    }
                )
        elif action == 'delete':
            result = await self.delete_event(data)
            if result:
                cal_id = data.get('calendar_id') or result.get('calendar_id')
                target_group = f"calendar_{cal_id}" if cal_id else self.default_group
                if result['action'] == 'delete':
                    await self.channel_layer.group_send(
                        target_group,
                        {
                            'type': 'calendar_update',
                            'action': 'delete',
                            'event': {'id': result['event_id'], 'calendar_id': cal_id}
                        }
                    )
                else:
                    await self.channel_layer.group_send(
                        target_group,
                        {
                            'type': 'calendar_update',
                            'action': 'update',
                            'event': result['event_dict']
                        }
                    )

    async def calendar_update(self, event):
        await self.send(text_data=json.dumps({
            'action': event['action'],
            'event': event['event']
        }))

    @database_sync_to_async
    def check_calendar_access(self, calendar_id):
        user = self.scope["user"]
        try:
            cal = Calendar.objects.get(uuid=calendar_id)
            # Internal CME schedule: all authenticated users have access
            if cal.is_internal:
                return True
            # Public calendar: all authenticated users have access
            if cal.is_public:
                return True
            # Private calendar: only owner or subscribers
            if cal.user == user or user in cal.subscribers.all():
                return True
            return False
        except Calendar.DoesNotExist:
            return False

    @database_sync_to_async
    def create_event(self, data):
        calendar_id = data.get('calendar_id')
        user = self.scope["user"]
        if not calendar_id:
            return None
            
        try:
            calendar = Calendar.objects.get(uuid=calendar_id)
        except Calendar.DoesNotExist:
            return None

        # Permission check:
        # Internal CME schedule: staff or calendar owner only
        if calendar.is_internal:
            if not (user.is_staff or calendar.user == user):
                return None
        else:
            # Personal calendar: calendar owner only
            if calendar.user != user:
                return None

        start = parser.isoparse(data['start'])
        end = parser.isoparse(data['end']) if data.get('end') else start

        event = Event.objects.create(
            calendar=calendar,
            title=data.get('title', 'Untitled'),
            description=data.get('description', ''),
            location=data.get('location', ''),
            start_date=start,
            end_date=end,
            color=data.get('color', '#039be5'),
            all_day=data.get('all_day', False),
            recurring_rule=data.get('recurring_rule', ''),
            user=user
        )
        return event.to_dict()

    @database_sync_to_async
    def update_event(self, data):
        user = self.scope["user"]
        try:
            event = Event.objects.get(id=data['id'])
            calendar = event.calendar
            if not calendar:
                return None

            # Permission check:
            if calendar.is_internal:
                if not (user.is_staff or calendar.user == user):
                    return None
            else:
                if calendar.user != user:
                    return None

            event.title = data.get('title', event.title)
            event.description = data.get('description', event.description)
            event.location = data.get('location', event.location)
            event.start_date = parser.isoparse(data['start'])
            event.end_date = parser.isoparse(data['end']) if data.get('end') else event.start_date

            if 'color' in data:
                event.color = data['color']
            if 'all_day' in data:
                event.all_day = data['all_day']
            if 'recurring_rule' in data:
                event.recurring_rule = data['recurring_rule']

            # If calendar reassignment attempted, check target permissions
            new_calendar_id = data.get('calendar_id')
            if new_calendar_id and str(calendar.uuid) != str(new_calendar_id):
                try:
                    new_cal = Calendar.objects.get(uuid=new_calendar_id)
                    if new_cal.is_internal and (user.is_staff or new_cal.user == user):
                        event.calendar = new_cal
                    elif not new_cal.is_internal and new_cal.user == user:
                        event.calendar = new_cal
                except Calendar.DoesNotExist:
                    pass

            event.save()
            return event.to_dict()
        except Event.DoesNotExist:
            return None

    @database_sync_to_async
    def delete_event(self, data):
        user = self.scope["user"]
        try:
            event = Event.objects.get(id=data['id'])
            calendar = event.calendar
            if not calendar:
                return None

            # Permission check:
            if calendar.is_internal:
                if not (user.is_staff or calendar.user == user):
                    return None
            else:
                if calendar.user != user:
                    return None

            cal_id = str(calendar.uuid)
            delete_mode = data.get('delete_mode', 'all')
            if delete_mode == 'all' or not event.recurring_rule:
                event_id = event.id
                event.delete()
                return {'action': 'delete', 'event_id': event_id, 'calendar_id': cal_id}

            occ_date_str = data.get('occurrence_date')
            if occ_date_str:
                occ_date = parser.isoparse(occ_date_str)
                if delete_mode == 'this':
                    exdates = []
                    if event.exception_dates:
                        exdates = event.exception_dates.split(',')
                    date_iso = occ_date.isoformat()
                    if date_iso not in exdates:
                        exdates.append(date_iso)
                    event.exception_dates = ','.join(exdates)
                    event.save()
                    return {'action': 'update', 'event_dict': event.to_dict(), 'calendar_id': cal_id}
                elif delete_mode == 'following':
                    event.recurrence_until = occ_date
                    event.save()
                    return {'action': 'update', 'event_dict': event.to_dict(), 'calendar_id': cal_id}

            return None
        except Event.DoesNotExist:
            return None
