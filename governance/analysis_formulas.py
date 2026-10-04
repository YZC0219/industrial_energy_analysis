"""Reviewed analytical contract and Python reference; no executable YAML expressions."""
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def contract():
    value = yaml.safe_load((ROOT / 'governance/analysis_formulas.yaml').read_text(encoding='utf-8'))
    expected = {'monthly_energy_change': ('adjacent_month_change', 'tce'),
                'daily_energy_average': ('trailing_calendar_mean', 'tce'),
                'energy_cost_pareto': ('descending_cumulative_share', 'cost')}
    if type(value.get('version')) is not int or value['version'] != 1 or set(value['formulas']) != set(expected):
        raise ValueError('Unsupported analytical contract')
    for ident, (operation, field) in expected.items():
        item = value['formulas'][ident]
        if item['operation'] != operation or item['field'] != field:
            raise ValueError('Unreviewed analytical operation')
    average = value['formulas']['daily_energy_average']
    if (type(average['window_days']) is not int or average['window_days'] != 7
            or type(average['minimum_points']) is not int or average['minimum_points'] != 1
            or average['missing_days'] != 'exclude_without_zero_fill'):
        raise ValueError('Seven-day average contract requires review')
    monthly=value['formulas']['monthly_energy_change']
    if monthly['missing_previous'] is not None or monthly['zero_previous'] is not None:
        raise ValueError('Monthly missing/zero policy requires review')
    if value['formulas']['energy_cost_pareto']['threshold_percent'] != 80:
        raise ValueError('Pareto threshold requires review')
    if value['formulas']['energy_cost_pareto']['zero_total'] != 'zero_shares_no_threshold':
        raise ValueError('Pareto zero-total policy requires review')
    value['sha256'] = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return value


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid analytical measure')
    return value


def month_change(rows):
    result = []
    previous = None
    for row in rows:
        month = row['ym']
        stamp = date.fromisoformat(month + '-01')
        if stamp.strftime('%Y-%m') != month:
            raise ValueError('Noncanonical month')
        ordinal = stamp.year * 12 + stamp.month
        value = number(row['tce'])
        if previous and ordinal <= previous[0]:
            raise ValueError('Months must be unique and sorted')
        delta = (value / previous[1] - 1) * 100 if previous and ordinal == previous[0]+1 and previous[1] > 0 else None
        if delta is not None and not math.isfinite(delta):
            raise ValueError('Monthly change overflow')
        result.append(delta)
        previous = (ordinal, value)
    return result


def calendar_average(days, values):
    settings = contract()['formulas']['daily_energy_average']
    if len(days) != len(values):
        raise ValueError('Mismatched daily series')
    stamps = [date.fromisoformat(d).toordinal() for d in days]
    if any(date.fromordinal(s).isoformat() != d for s,d in zip(stamps,days)) or any(a >= b for a,b in zip(stamps,stamps[1:])):
        raise ValueError('Dates must be canonical, unique and sorted')
    checked = [number(v) for v in values]
    return [math.fsum(v for s,v in zip(stamps,checked) if end-settings['window_days']+1 <= s <= end) /
            sum(end-settings['window_days']+1 <= s <= end for s in stamps) for end in stamps]


def pareto(rows):
    definition = contract()['formulas']['energy_cost_pareto']
    costs = [number(r['cost']) for r in rows]
    order = sorted(range(len(rows)), key=lambda i: -costs[i])
    total = math.fsum(costs)
    cumulative = 0
    shares, accumulated = [], []
    threshold = None
    for index in order:
        share = costs[index] / total * 100 if total else 0
        cumulative += share
        shares.append(share)
        accumulated.append(min(100, cumulative))
        if threshold is None and total and cumulative >= definition['threshold_percent'] - 1e-10:
            threshold = len(shares)-1
    return {'rows': [rows[i] for i in order], 'total': total, 'shares': shares,
            'cumulative': accumulated, 'threshold_index': threshold}
