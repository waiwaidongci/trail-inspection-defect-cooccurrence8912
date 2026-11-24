import json
from pathlib import Path

DEMO = Path(__file__).resolve().parents[2] / 'demo'
BUSINESS = json.loads((DEMO / 'business.json').read_text(encoding='utf-8'))
TYPES = {'str': 'string', 'float': 'number', 'choice': 'select'}


def api_fields(items):
    return [{**item, 'type': TYPES.get(item['type'], item['type'])} for item in items]


business = BUSINESS
page_names = business['pages']
ledger = page_names[0]
supplement_ledger = page_names[1] if len(page_names) >= 5 else '补传巡检台账'
run, result, delivery = page_names[-3], page_names[-2], page_names[-1]
method = business['method']
SPEC = {
    'title': business['title'], 'short_name': 'hxwl-solo2611-7', 'subtitle': business['description'],
    'organization': business['team'], 'workflow': business['description'], 'pains': business['pain_points'],
    'steps': business['script'], 'methodology': method if isinstance(method, list) else [method],
    'sample_name': business['dataset'], 'default_dataset': business['dataset'].removesuffix('.csv'),
    'entity': ledger, 'run': run, 'decision': '业务处理决定', 'action': '保存复核决定',
    'decision_guide': business['decision'], 'recipients': business['recipients'],
    'pages': ['值班概览', ledger, '补传巡检台账', run, result, '业务复核', delivery],
    'fields': api_fields(business['fields']), 'params': api_fields(business['parameters']),
    'scheduled': True, 'schedule_date_param': business.get('schedule_date_param'),
    'strict_test_params': business.get('strict_test_params'),
}
