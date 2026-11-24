from src.utils.time import iso


def dataset(item):
    return {'id': item.id, 'name': item.name, 'filename': item.filename, 'row_count': item.row_count,
            'checksum': item.checksum, 'author': item.author, 'created_at': iso(item.created_at)}


def run(item, detail=False):
    item_view = {'id': item.id, 'dataset_id': item.dataset_id, 'name': item.name, 'status': item.status,
                 'author': item.author, 'review': item.review, 'decision': item.decision,
                 'created_at': iso(item.created_at), 'finished_at': iso(item.finished_at),
                 'scheduled_at': iso(item.scheduled_at), 'schedule_id': item.schedule_id,
                 'has_export': bool(item.export_file), 'error': item.error}
    if detail:
        item_view.update(params=item.params, result=item.result, review_note=item.review_note)
    return item_view


def schedule(item):
    from src.domain.spec import SPEC
    return {'id': item.id, 'name': item.name, 'dataset_id': item.dataset_id, 'recipient': item.recipient,
            'next_at': iso(item.next_at), 'repeat_daily': item.repeat_daily, 'enabled': item.enabled,
            'next_report_day': item.params.get(SPEC['schedule_date_param']) if item.enabled else None}


def delivery(item):
    return {'id': item.id, 'run_id': item.run_id, 'recipient': item.recipient,
            'state': item.state, 'detail': item.detail, 'created_at': iso(item.created_at)}


def supplement_batch(item):
    return {
        'id': item.id, 'name': item.name, 'filename': item.filename,
        'idempotency_key': item.idempotency_key, 'status': item.status,
        'row_count': item.row_count, 'changed_segments': item.changed_segments,
        'replaced_versions': item.replaced_versions, 'skipped_versions': item.skipped_versions,
        'checklist_version_id': item.checklist_version_id, 'error': item.error,
        'author': item.author, 'created_at': iso(item.created_at), 'finished_at': iso(item.finished_at),
    }


def ledger_entry(item):
    return {
        'id': item.id, 'route': item.route, 'segment_id': item.segment_id,
        'inspection_date': item.inspection_date.isoformat(), 'version': item.version,
        'defects': item.defects, 'is_active': item.is_active,
        'batch_id': item.batch_id, 'created_at': iso(item.created_at),
    }


def segment_state(item):
    return {
        'id': item.id, 'route': item.route, 'segment_id': item.segment_id,
        'inspection_date': item.inspection_date.isoformat(), 'defects': item.defects,
        'version': item.version, 'batch_id': item.batch_id, 'updated_at': iso(item.updated_at),
    }


def checklist_version(item, include_items=False):
    payload = {
        'id': item.id, 'version_no': item.version_no, 'status': item.status, 'scope': item.scope,
        'params': item.params, 'total_segments': item.total_segments,
        'clean_segments': item.clean_segments, 'rule_count': item.rule_count,
        'changed_segments': item.changed_segments, 'summary': item.summary,
        'rules_snapshot': item.rules_snapshot, 'trigger_batch_id': item.trigger_batch_id,
        'author': item.author, 'created_at': iso(item.created_at),
        'item_count': len(item.items),
    }
    if include_items:
        payload['items'] = [checklist_item(entry) for entry in item.items]
    return payload


def checklist_item(item):
    return {
        'id': item.id, 'route': item.route, 'segment_id': item.segment_id,
        'inspection_date': item.inspection_date.isoformat(), 'antecedent': item.antecedent,
        'consequent': item.consequent, 'action': item.action, 'evidence': item.evidence,
    }
