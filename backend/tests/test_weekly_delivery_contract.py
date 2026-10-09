"""FCM's wire contract and weekly windows at hour/day boundaries."""
import datetime
import json

import httpx
import pytest

from app.models import WeeklyNotePreference
from app.services import weekly_note


@pytest.mark.parametrize('platform', ['android', 'ios'])
def test_fcm_http_v1_message_envelope(platform):
    requests = []
    def receive(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={})
    with httpx.Client(transport=httpx.MockTransport(receive)) as client:
        weekly_note._post_message(client, 'access', 'project', 'device',
                                  platform, '3 things', 'snapshot', 'account')
    assert set(requests[0]) == {'message'}
    message = requests[0]['message']
    assert message['token'] == 'device'
    assert message['notification'] == {
        'title': '3 things', 'body': 'Open FindBack to take another look.'}
    assert message['data'] == {
        'type': 'weekly_note', 'snapshot_id': 'snapshot', 'account_id': 'account'}
    if platform == 'android':
        assert message['android']['notification']['visibility'] == 'PRIVATE'
    else:
        assert 'android' not in message


def test_fcm_unregistered_detail_is_a_dead_token():
    response = httpx.Response(404, json={'error': {
        'status': 'NOT_FOUND', 'details': [{
            '@type': 'type.googleapis.com/google.firebase.fcm.v1.FcmError',
            'errorCode': 'UNREGISTERED'}]}})
    assert weekly_note._failure_reason(response) == 'UNREGISTERED'


def test_invalid_message_does_not_mean_invalid_token():
    response = httpx.Response(400, json={'error': {
        'status': 'INVALID_ARGUMENT', 'details': [{
            '@type': 'type.googleapis.com/google.rpc.BadRequest',
            'fieldViolations': [{'field': 'message.data', 'description': 'bad data'}]}]}})
    assert weekly_note._failure_reason(response) not in weekly_note.DEAD_TOKEN_REASONS


@pytest.mark.parametrize('hour,minute,now', [
    (9, 50, datetime.datetime(2026, 10, 4, 10, 0, tzinfo=datetime.timezone.utc)),
    (23, 50, datetime.datetime(2026, 10, 5, 0, 0, tzinfo=datetime.timezone.utc)),
])
def test_weekly_window_crosses_hour_and_day(hour, minute, now):
    preference = WeeklyNotePreference(weekday=6, hour=hour, minute=minute, time_zone='UTC')
    assert weekly_note.due_now(preference, now)
    assert not weekly_note.due_now(preference, now + datetime.timedelta(minutes=5))
