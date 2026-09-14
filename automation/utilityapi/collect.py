"""One collection per LA calendar month; never retry a possibly accepted POST."""
import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('America/Los_Angeles')

def same_month(stamp, now):
    try:
        return datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone(ZONE).strftime('%Y-%m') == now.strftime('%Y-%m')
    except (ValueError, TypeError, AttributeError):
        return False

def already_collected(meter, now):
    return any(n.get('type') in {'bills_full', 'bills_partial', 'intervals_full', 'intervals_partial', 'intervals_mixed'} and same_month(n.get('ts'), now) for n in meter.get('notes', []))

def api(path, payload=None):
    req = urllib.request.Request('https://utilityapi.com/api/v2/' + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + os.environ['UTILITYAPI_TOKEN'], 'Content-Type': 'application/json'})
    # Deliberately no HTTP retry: a timed-out POST may already have started billing.
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.load(response)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    now = datetime.now(ZONE)
    month = now.strftime('%Y-%m')
    meter_id = os.environ['UTILITYAPI_METER_UID']
    meter = api('meters/' + meter_id)
    if meter.get('utility') != 'PG&E' or meter.get('is_archived') or meter.get('is_stopped'):
        raise RuntimeError('Configured meter is not an active PG&E meter')
    if meter.get('status') == 'pending':
        print('Collection already pending; not starting another.')
        return
    if already_collected(meter, now):
        print('Data already collected this calendar month; skipping to avoid another charge.')
        return
    if args.dry_run:
        print('Read-only validation passed. No collection was triggered.')
        return
    if now.day != 15:
        raise RuntimeError('Collection is only allowed on the 15th in America/Los_Angeles')
    # Create an atomic marker BEFORE POST. A rerun after timeout must not collect again.
    repo = os.environ['GITHUB_REPOSITORY']
    ref = 'refs/tags/utilityapi-collection-' + month
    result = subprocess.run(['gh', 'api', '--method', 'POST', f'repos/{repo}/git/refs',
        '-f', 'ref=' + ref, '-f', 'sha=' + os.environ['GITHUB_SHA']], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError('Month marker already exists or could not be created; no collection attempted')
    try:
        result = api('meters/historical-collection', {'meters': [meter_id], 'collection_duration': 12})
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'Collection HTTP {error.code}; no automatic retry or payment. Check UtilityAPI dashboard.') from None
    if result.get('success') is not True or meter_id not in [str(v) for v in result.get('meters', [])]:
        raise RuntimeError('Collection acceptance uncertain; inspect dashboard before any retry')
    print('One collection accepted. App reads existing data separately; no further collection calls.')

if __name__ == '__main__':
    main()
