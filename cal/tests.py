from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from cal.models import Calendar, Event
from cal.consumers import CalendarConsumer
from asgiref.sync import async_to_sync
import json
from datetime import datetime, timedelta
import pytz

class CMEInternalScheduleTests(TestCase):
    def setUp(self):
        # Create users: Staff Officer (Admin) and Cadet (Regular User)
        self.staff_user = User.objects.create_user(
            username='officer_sharma',
            password='password123',
            is_staff=True
        )
        self.regular_user = User.objects.create_user(
            username='cadet_rahul',
            password='password123',
            is_staff=False
        )
        self.other_user = User.objects.create_user(
            username='cadet_priya',
            password='password123',
            is_staff=False
        )

        # Create clients
        self.staff_client = Client()
        self.staff_client.login(username='officer_sharma', password='password123')

        self.regular_client = Client()
        self.regular_client.login(username='cadet_rahul', password='password123')

        # Create an Internal CME Schedule
        self.cme_schedule = Calendar.objects.create(
            name='CME Master Training Programme 2026',
            user=self.staff_user,
            is_internal=True,
            is_public=True
        )

        # Create a personal calendar for cadet_rahul
        self.cadet_calendar = Calendar.objects.create(
            name='Rahul Personal',
            user=self.regular_user,
            is_internal=False,
            is_public=False
        )

        # Create a sample event in the CME schedule
        now = datetime.now(pytz.utc)
        self.cme_event = Event.objects.create(
            calendar=self.cme_schedule,
            user=self.staff_user,
            title='Brigade Parade Drill',
            description='Mandatory drill at Parade Ground',
            location='Main Parade Ground, CME Pune',
            start_date=now,
            end_date=now + timedelta(hours=2),
            color='#d50000',
            all_day=False
        )

    def test_internal_schedule_creation_by_staff(self):
        """Staff users should be able to create an Internal CME Schedule."""
        response = self.staff_client.post(reverse('create_calendar'), {
            'name': 'Combat Engineers Field Exercise',
            'is_internal': 'on'
        })
        self.assertEqual(response.status_code, 302)
        created_cal = Calendar.objects.filter(name='Combat Engineers Field Exercise').first()
        self.assertIsNotNone(created_cal)
        self.assertTrue(created_cal.is_internal)
        self.assertTrue(created_cal.is_public)

    def test_internal_schedule_creation_by_regular_user_fails(self):
        """Non-staff users cannot create an internal schedule even if they pass is_internal."""
        response = self.regular_client.post(reverse('create_calendar'), {
            'name': 'Cadet Unauthorized Schedule',
            'is_internal': 'on'
        })
        self.assertEqual(response.status_code, 302)
        created_cal = Calendar.objects.filter(name='Cadet Unauthorized Schedule').first()
        self.assertIsNotNone(created_cal)
        # Should NOT be internal because user is not staff
        self.assertFalse(created_cal.is_internal)

    def test_automatic_internal_schedule_distribution(self):
        """Internal CME schedules should be automatically accessible to all authenticated users without subscribing."""
        response = self.regular_client.get(reverse('calendar'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('internal_schedules', response.context)
        # Internal schedule is in internal_schedules
        internal_uuids = [c.uuid for c in response.context['internal_schedules']]
        self.assertIn(self.cme_schedule.uuid, internal_uuids)

    def test_cadet_can_view_internal_schedule(self):
        """A regular cadet should be able to view the internal CME schedule and see its events."""
        response = self.regular_client.get(f"/?calendar_uuid={self.cme_schedule.uuid}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_calendar'].uuid, self.cme_schedule.uuid)
        # Cadet should have read-only access (can_edit == False)
        self.assertFalse(response.context['can_edit'])
        # The event should be in the initial events JSON
        self.assertIn('Brigade Parade Drill', response.context['initial_events_json'])

    def test_staff_has_edit_permission_on_internal_schedule(self):
        """Staff users should have edit permission (can_edit == True) on internal schedules."""
        response = self.staff_client.get(f"/?calendar_uuid={self.cme_schedule.uuid}")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_edit'])

    def test_cadet_cannot_rename_internal_schedule(self):
        """Non-staff users cannot rename an internal CME schedule."""
        response = self.regular_client.post(reverse('rename_calendar', args=[self.cme_schedule.uuid]), {
            'name': 'Hacked Schedule Name'
        })
        self.cme_schedule.refresh_from_db()
        self.assertEqual(self.cme_schedule.name, 'CME Master Training Programme 2026')

    def test_staff_can_rename_internal_schedule(self):
        """Staff users can rename an internal CME schedule."""
        response = self.staff_client.post(reverse('rename_calendar', args=[self.cme_schedule.uuid]), {
            'name': 'Updated CME Master Programme'
        })
        self.cme_schedule.refresh_from_db()
        self.assertEqual(self.cme_schedule.name, 'Updated CME Master Programme')

    def test_cadet_cannot_delete_internal_schedule(self):
        """Non-staff users cannot delete an internal CME schedule."""
        response = self.regular_client.post(reverse('delete_calendar', args=[self.cme_schedule.uuid]))
        self.assertTrue(Calendar.objects.filter(uuid=self.cme_schedule.uuid).exists())

    def test_consumer_permissions_create_event(self):
        """WebSocket consumer: regular user cannot create events on internal schedule; staff can."""
        consumer = CalendarConsumer()
        now = datetime.now(pytz.utc).isoformat()
        data = {
            'calendar_id': str(self.cme_schedule.uuid),
            'title': 'Cadet Unauthorized Event',
            'start': now,
            'end': now
        }

        # Regular user scope
        consumer.scope = {'user': self.regular_user}
        res_regular = async_to_sync(consumer.create_event)(data)
        self.assertIsNone(res_regular, "Regular user should be rejected from creating event on internal schedule")

        # Staff user scope
        consumer.scope = {'user': self.staff_user}
        staff_data = {
            'calendar_id': str(self.cme_schedule.uuid),
            'title': 'Staff Official Lecture',
            'start': now,
            'end': now
        }
        res_staff = async_to_sync(consumer.create_event)(staff_data)
        self.assertIsNotNone(res_staff, "Staff user should be allowed to create event on internal schedule")
        self.assertEqual(res_staff['title'], 'Staff Official Lecture')

    def test_consumer_permissions_update_event(self):
        """WebSocket consumer: regular user cannot edit an internal schedule event; staff can."""
        consumer = CalendarConsumer()
        now = datetime.now(pytz.utc).isoformat()
        update_data = {
            'id': self.cme_event.id,
            'title': 'Cadet Tampered Title',
            'start': now,
            'end': now
        }

        # Regular user scope
        consumer.scope = {'user': self.regular_user}
        res_regular = async_to_sync(consumer.update_event)(update_data)
        self.assertIsNone(res_regular, "Regular user should be rejected from modifying internal schedule event")
        self.cme_event.refresh_from_db()
        self.assertEqual(self.cme_event.title, 'Brigade Parade Drill')

        # Staff user scope
        consumer.scope = {'user': self.staff_user}
        staff_update = {
            'id': self.cme_event.id,
            'title': 'Brigade Parade Drill - Rescheduled',
            'start': now,
            'end': now
        }
        res_staff = async_to_sync(consumer.update_event)(staff_update)
        self.assertIsNotNone(res_staff)
        self.cme_event.refresh_from_db()
        self.assertEqual(self.cme_event.title, 'Brigade Parade Drill - Rescheduled')

    def test_consumer_permissions_delete_event(self):
        """WebSocket consumer: regular user cannot delete internal schedule event; staff can."""
        consumer = CalendarConsumer()
        del_data = {'id': self.cme_event.id, 'delete_mode': 'all'}

        # Regular user scope
        consumer.scope = {'user': self.regular_user}
        res_regular = async_to_sync(consumer.delete_event)(del_data)
        self.assertIsNone(res_regular)
        self.assertTrue(Event.objects.filter(id=self.cme_event.id).exists())

        # Staff user scope
        consumer.scope = {'user': self.staff_user}
        res_staff = async_to_sync(consumer.delete_event)(del_data)
        self.assertIsNotNone(res_staff)
        self.assertEqual(res_staff['action'], 'delete')
        self.assertFalse(Event.objects.filter(id=self.cme_event.id).exists())

    def test_private_calendar_access_control(self):
        """Private calendar should not be accessible by unauthorized users."""
        consumer = CalendarConsumer()
        consumer.scope = {'user': self.other_user}
        has_access = async_to_sync(consumer.check_calendar_access)(str(self.cadet_calendar.uuid))
        self.assertFalse(has_access, "Other users should not have access to a private calendar")

        # But internal schedule should be accessible to all authenticated users
        has_internal_access = async_to_sync(consumer.check_calendar_access)(str(self.cme_schedule.uuid))
        self.assertTrue(has_internal_access, "All authenticated users should have access to internal CME schedule")
