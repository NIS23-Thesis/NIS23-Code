"""Read-only packet delivery inspector for CLIVE debug JSONL files.

A packet record is a *datagram attempt*, not an entire FLAME model update.
Only a receiver's own `receive` observation proves NodeSocket reception;
application-level admission, reassembly and aggregation are separate events.
This module never imports or changes FLAME and does not alter radio timing.
"""
from __future__ import annotations

import bisect
import json
from collections import defaultdict

PACKET_NAMES = ('HELLO', 'LEAVE', 'SENSOR_DATA', 'SENSOR_DATA_ACK',
                'MODEL_UPDATE', 'MODEL_UPDATE_ACK', 'JOIN', 'JOIN_ACCEPT')
PACKET_NAME_IDS = {name: i for i, name in enumerate(PACKET_NAMES)}
STAGE_ORDER = {'attempt': 0, 'admit': 1, 'transmit': 2, 'delivery_attempt': 3,
               'scheduled_delivery': 4, 'kernel_send': 5, 'receive': 6,
               'delivery_drop': 7, 'local_drop': 8, 'socket_error': 9}
MAX_SCAN_EVENTS = 120_000


def _packet_type(value):
    if value is None or str(value).strip() == '':
        return None
    token = str(value).strip().upper()
    if token in PACKET_NAME_IDS:
        return PACKET_NAME_IDS[token]
    try:
        number = int(token)
    except ValueError as exc:
        raise ValueError('Unknown FLAME packet type') from exc
    if number not in range(len(PACKET_NAMES)):
        raise ValueError('Packet type must be 0–7')
    return number


def _read_events(run, start, end):
    """Seek each node's ordered log by its sparse on-disk time index."""
    events = []
    scanned = 0
    truncated = False
    paths = [(p, k) for p, k in run._event_log_sources() if k == 'packet']
    with run._window_index_lock:
        for path, kind in paths:
            index = run._update_window_index(path)
            marks = index['marks']
            # The sparse index stores times alongside byte offsets; no per-request
            # reconstruction of a list is needed when inspecting a large run.
            times = index.get('mark_times')
            if times is None:
                times = [x[0] for x in marks]  # legacy RunData indexes
            pos = bisect.bisect_right(times, start) - 1
            begin = marks[pos][1] if pos >= 0 else 0
            try:
                with path.open('rb') as reader:
                    reader.seek(begin)
                    while True:
                        offset = reader.tell()
                        line = reader.readline()
                        if not line or not line.endswith(b'\n'):
                            break
                        match = run._LOG_TIME.search(line)
                        if not match:
                            continue
                        timestamp = float(match.group(1))
                        if timestamp < start:
                            continue
                        if timestamp > end:
                            break
                        scanned += 1
                        if scanned > MAX_SCAN_EVENTS:
                            truncated = True
                            break
                        try:
                            raw = json.loads(line)
                        except (UnicodeDecodeError, ValueError):
                            continue
                        event = run._normalize_event(raw, path.name, kind, assign_seq=False)
                        event['archive_id'] = f'{path.parent.name}/{path.name}@{offset}'
                        if event['event'] in STAGE_ORDER:
                            events.append(event)
            except OSError:
                continue
            if truncated:
                break
    events.sort(key=lambda e: (e['time_s'], STAGE_ORDER.get(e['event'], 99), e['archive_id']))
    return events, len(paths), truncated, scanned


def build_traces(events, start, end, *, node='', packet_type=None,
                 sequence=None, offset=0, limit=60):
    """Correlate radio stages using sender-local request ID + wire fields.

    A digest plus an unambiguous sender/type/sequence match identifies a
    receiver observation. Retransmitted identical bytes cannot be uniquely
    attributed to one emission; those RX records remain unassigned.
    """
    groups = {}
    for ev in events:
        if ev['event'] == 'receive':
            continue
        packet_id = ev.get('packet_id')
        # packet_id is allocated independently by each sending NodeSocket.
        key = (ev['node'], str(packet_id)) if packet_id is not None else (
            ev['node'], ev.get('archive_id', str(ev['time_s'])))
        row = groups.setdefault(key, {'source': ev['node'], 'packet_id': packet_id,
                                     'events': [], 'received': [], 'uncertain': [],
                                     'first': ev['time_s'], 'packet_type': ev.get('packet_type'),
                                     'sequence_number': ev.get('sequence_number'),
                                     'fingerprint': ev.get('payload_fingerprint'),
                                     'target': ev.get('target'),
                                     'is_broadcast': ev.get('is_broadcast', False)})
        row['events'].append(ev)
        row['first'] = min(row['first'], ev['time_s'])
        if row['packet_type'] is None:
            row['packet_type'] = ev.get('packet_type')
        if row['sequence_number'] is None:
            row['sequence_number'] = ev.get('sequence_number')
        if row['fingerprint'] is None:
            row['fingerprint'] = ev.get('payload_fingerprint')
        if not row['target'] and ev.get('target'):
            row['target'] = ev['target']

    # Each candidate bucket is split into broadcasts/unspecified targets and
    # targeted sends. Sparse ordered timestamp arrays allow exact candidate
    # counts in O(log N), including *all* ambiguous retransmissions; merely
    # stopping at the second match would silently under-report ambiguities.
    def bucket():
        return {'any': [], 'targets': defaultdict(list)}

    tx_index = {}
    for group in groups.values():
        if not any(ev['event'] == 'transmit' for ev in group['events']):
            continue
        ident = (group['source'], group['packet_type'], group['sequence_number'])
        entry = tx_index.setdefault(ident, {'all': bucket(),
                                            'by_hash': defaultdict(bucket),
                                            'without_hash': bucket()})
        eligible_buckets = [entry['all']]
        digest = group['fingerprint']
        eligible_buckets.append(entry['by_hash'][digest] if digest else entry['without_hash'])
        for source in eligible_buckets:
            if group['target'] and not group['is_broadcast']:
                source['targets'][group['target']].append(group)
            else:
                source['any'].append(group)

    # Store each bucket's sorted timestamps once, not once per receiving packet.
    def index_bucket(source):
        for key in ('any', 'targets'):
            if key == 'any':
                groups_here = sorted(source['any'], key=lambda g: g['first'])
                source['any'] = ([g['first'] for g in groups_here], groups_here)
            else:
                for target, items in source['targets'].items():
                    items.sort(key=lambda g: g['first'])
                    source['targets'][target] = ([g['first'] for g in items], items)

    for entry in tx_index.values():
        index_bucket(entry['all'])
        index_bucket(entry['without_hash'])
        for source in entry['by_hash'].values():
            index_bucket(source)

    orphan = []
    for ev in events:
        if ev['event'] != 'receive':
            continue
        ident = (ev.get('source'), ev.get('packet_type'), ev.get('sequence_number'))
        entry = tx_index.get(ident)
        matches, sole = 0, None
        if entry:
            digest = ev.get('payload_fingerprint')
            # Old logs without fingerprints match any digest; missing sender
            # digests remain eligible even when a receiving digest is present.
            sources = ((entry['by_hash'].get(digest), entry['without_hash'])
                       if digest else (entry['all'],))
            for source in sources:
                if source is None:
                    continue
                for times, candidates in (source['any'], source['targets'].get(ev['node'], ([], []))):
                    n = bisect.bisect_right(times, ev['time_s'] + 0.001)
                    if n:
                        matches += n
                        sole = candidates[0] if matches == 1 else None
        if matches == 1:
            sole['received'].append(ev)
            if not (sole['fingerprint'] and ev.get('payload_fingerprint')):
                sole['uncertain'].append(ev['archive_id'])
        else:
            orphan.append((ev, matches))

    # Store only lightweight references until AFTER pagination. Building/sorting
    # every packet's detailed stage rows on each request used to allocate tens
    # of thousands of dictionaries even when the browser requested just 60.
    selected = []
    for group in groups.values():
        if not start <= group['first'] <= end:
            continue
        if node and node not in (str(group['source']).lower(), str(group['target'] or '').lower()) and not any(
                node == str(e['node']).lower() for e in group['received']):
            continue
        if packet_type is not None and group['packet_type'] != packet_type:
            continue
        if sequence is not None and group['sequence_number'] != sequence:
            continue
        selected.append((group['first'], group['source'], str(group['packet_id']), 'group', group))
    for ev, ambiguous_count in orphan:
        if not start <= ev['time_s'] <= end:
            continue
        if node and node not in (str(ev.get('source') or '').lower(), str(ev['node']).lower()):
            continue
        if packet_type is not None and ev['packet_type'] != packet_type:
            continue
        if sequence is not None and ev['sequence_number'] != sequence:
            continue
        # Original result uses the receiver node as 'target' and no packet ID.
        selected.append((ev['time_s'], ev.get('source') or 'unknown', str(None),
                         'orphan', (ev, ambiguous_count)))
    selected.sort(key=lambda row: row[:3])
    total = len(selected)

    def detailed_row(selection):
        _, _, _, kind, item = selection
        if kind == 'orphan':
            ev, ambiguous_count = item
            typ = ev['packet_type']
            return {
                'id': f"rx:{ev['archive_id']}", 'time_s': ev['time_s'],
                'source': ev.get('source') or 'unknown', 'target': ev['node'],
                'packet_type': typ,
                'packet_name': PACKET_NAMES[typ] if isinstance(typ, int) and typ in range(8) else 'UNKNOWN',
                'sequence_number': ev['sequence_number'], 'packet_id': None,
                'status': 'ambiguous' if ambiguous_count else 'receiver_only',
                'emissions': 0, 'receiver_count': 1, 'receiver_nodes': [ev['node']],
                'kernel_sends': 0, 'drop_count': 0, 'fingerprint_confirmed': False,
                'possible_match_count': ambiguous_count,
                'stages': [{'time_s': ev['time_s'], 'event': 'receive',
                            'at': ev['node'], 'source': ev.get('source'),
                            'target': ev['node'], 'reason': None,
                            'archive_id': ev['archive_id'],
                            'fingerprint': ev.get('payload_fingerprint')}],
            }

        group = item
        evs = sorted(group['events'] + group['received'],
                     key=lambda e: (e['time_s'], STAGE_ORDER.get(e['event'], 99), e['archive_id']))
        emissions = [e for e in group['events'] if e['event'] == 'transmit']
        drops = [e for e in group['events'] if e['event'] in ('local_drop', 'delivery_drop')]
        handed = [e for e in group['events'] if e['event'] == 'kernel_send']
        if group['received']:
            status = 'received' if not group['uncertain'] else 'likely_received'
            if drops:
                status = 'partial' if group['is_broadcast'] else 'received_with_drop'
        elif not emissions and drops:
            status = 'not_transmitted'
        elif drops and not group['is_broadcast']:
            status = 'dropped'
        elif drops and group['is_broadcast']:
            status = 'broadcast_drop'
        else:
            status = 'unverified'
        typ = group['packet_type']
        return {
            'id': f"{group['source']}:{group['packet_id']}:{group['first']:.9f}",
            'time_s': group['first'], 'source': group['source'],
            'target': 'broadcast' if group['is_broadcast'] else (group['target'] or 'unknown'),
            'packet_type': typ,
            'packet_name': PACKET_NAMES[typ] if isinstance(typ, int) and typ in range(8) else 'UNKNOWN',
            'sequence_number': group['sequence_number'],
            'packet_id': group['packet_id'], 'status': status,
            'emissions': len(emissions), 'receiver_count': len(group['received']),
            'receiver_nodes': sorted({e['node'] for e in group['received']}),
            'dropped_nodes': sorted({e['target'] for e in drops if e.get('target')}),
            'kernel_sends': len(handed), 'drop_count': len(drops),
            'fingerprint_confirmed': bool(group['received'] and not group['uncertain']),
            'possible_match_count': len(group['uncertain']),
            'stages': [{'time_s': e['time_s'], 'event': e['event'],
                        'at': e['node'], 'source': e.get('source'), 'target': e.get('target'),
                        'reason': e.get('reason'), 'archive_id': e['archive_id'],
                        'fingerprint': e.get('payload_fingerprint')}
                       for e in evs],
        }

    page = [detailed_row(row) for row in selected[offset:offset + limit]]
    # Orphan receptions are separate evidence, never mislabeled as packet loss.
    return {'packets': page, 'total': total,
            'orphan_receives': sum(1 for event, _ in orphan if start <= event['time_s'] <= end),
            'ambiguous_receives': sum(1 for event, n in orphan if n > 1 and start <= event['time_s'] <= end),
            'offset': offset, 'limit': limit, 'has_more': offset + limit < total}


def read_packet_traces(run, *, from_s=0, to_s=1, node='', packet_type='',
                       sequence='', offset=0, limit=60):
    start = float(from_s)
    end = float(to_s)
    if not (0 <= start <= end and end - start <= 5 and end < 1e9):
        raise ValueError('Select a valid packet window of five seconds or less')
    offset = max(0, min(int(offset), 1_000_000))
    limit = max(1, min(int(limit), 80))
    node = str(node or '').strip().lower()[:50]
    typ = _packet_type(packet_type)
    try:
        seq = int(sequence) if str(sequence).strip() else None
    except ValueError as exc:
        raise ValueError('Packet sequence must be an integer') from exc
    if seq is not None and not 0 <= seq <= 65535:
        raise ValueError('Packet sequence must be 0 through 65535')
    # Include a short lookahead to match later RX to the selected TX.
    events, source_files, truncated, inspected = _read_events(run, max(0, start - .5), end + 2)
    data = build_traces(events, start, end, node=node, packet_type=typ,
                        sequence=seq, offset=offset, limit=limit)
    data.update({'run': run.name, 'from_s': start, 'to_s': end,
                 'packet_logs_available': bool(source_files),
                 'scan_truncated': truncated, 'scanned_records': inspected,
                 'note': ('Radio packet records are observational. A receiver-local receive event '
                          'confirms NodeSocket reception; it does not prove complete FLAME '
                          'reassembly. Identical retransmissions cannot always be '
                          'uniquely matched. No RX record does not prove packet loss. '
                          'Search is bounded; widen or move the time window as needed.')})
    return data
