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
                'energy_cost_pareto': ('descending_cumulative_share', 'cost'),
                'unit_energy_dispersion': ('fixed_baseline_dispersion', 'unit_energy'),
                'historical_intensity_difference': ('production_adjusted_difference', 'unit_energy'),
                'energy_difference_conversion': ('full_period_average_conversion', 'tce'),
                'residual_control_band': ('signed_residual_band', 'residual'),
                'historical_cusum_summary': ('stored_cusum_summary', 'cumulative_sigma'),
                'proportional_energy_scenario': ('proportional_reduction', 'tce')}
    if type(value.get('version')) is not int or value['version'] != 3 or set(value['formulas']) != set(expected):
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
    reviewed = {
        'unit_energy_dispersion': {'sigma_multiplier': 2, 'zero_mean': 'null_cv', 'lower_bound': 'clamp_zero'},
        'historical_intensity_difference': {'kgce_per_tce': 1000, 'negative_difference': 'preserve'},
        'energy_difference_conversion': {'zero_energy': 'null_conversion', 'coefficient_source': 'original_totals'},
        'residual_control_band': {'sigma_multiplier': 2, 'lower_bound': 'preserve_negative'},
        'historical_cusum_summary': {'threshold_source': 'Q27', 'classification': 'stored_sql_flags',
                                     'segment_rule': 'observed_false_to_true'},
        'proportional_energy_scenario': {'report_year': 2025, 'report_rates_percent': [3, 5, 10],
                                        'exclude_workshop': 'W07', 'conversion': 'per_energy_observed_cost_and_carbon'},
    }
    for ident, settings in reviewed.items():
        if any(type(value['formulas'][ident].get(key)) is not type(expected_value)
               or value['formulas'][ident][key] != expected_value for key, expected_value in settings.items()):
            raise ValueError('Industrial analytical policy requires review')
    value['sha256'] = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return value


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid analytical measure')
    return value


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Invalid signed analytical measure or overflow')
    return value


def dispersion(mean, sigma):
    settings = contract()['formulas']['unit_energy_dispersion']
    mean, sigma = number(mean), number(sigma)
    width = finite(settings['sigma_multiplier'] * sigma)
    return {'upper': finite(mean + width), 'lower': max(0, mean - width),
            'cv': finite(sigma / mean * 100) if mean else None}


def intensity_difference(quantity, baseline, current):
    scale = contract()['formulas']['historical_intensity_difference']['kgce_per_tce']
    quantity, baseline, current = number(quantity), number(baseline), number(current)
    # Multiply only after the per-unit difference to avoid overflowing two counterfactual totals.
    return finite(quantity * ((baseline - current) / scale))


def convert_difference(delta, totals):
    contract()
    delta = finite(delta)
    tce, carbon, cost = (number(totals[key]) for key in ('tce', 'co2', 'cost'))
    if not tce:
        return {'carbon_factor': None, 'cost_factor': None, 'co2': None, 'cost': None}
    cf, yf = finite(carbon / tce), finite(cost / tce)
    return {'carbon_factor': cf, 'cost_factor': yf,
            'co2': finite(delta * cf), 'cost': finite(delta * yf)}


def residual_band(mean, sigma):
    settings = contract()['formulas']['residual_control_band']
    mean, sigma = finite(mean), number(sigma)
    width = finite(settings['sigma_multiplier'] * sigma)
    return {'upper': finite(mean + width), 'lower': finite(mean - width), 'half_width': width}


def cusum_summary(points, threshold):
    contract()
    threshold = number(threshold)
    if threshold == 0:
        raise ValueError('CUSUM threshold must be positive')
    previous_date, previous_alarm = None, False
    alarms, high, low, segments = 0, 0, 0, 0
    max_hi = max_lo = max_raw = 0
    for point in points:
        stamp = date.fromisoformat(point['d'])
        if stamp.isoformat() != point['d'] or (previous_date is not None and stamp <= previous_date):
            raise ValueError('CUSUM dates must be canonical, unique and sorted')
        if type(point['alarm']) is not bool or point['side'] not in ('', '偏高', '偏低'):
            raise ValueError('Invalid stored CUSUM classification')
        if point['alarm'] != (point['side'] != ''):
            raise ValueError('Inconsistent stored CUSUM alarm direction')
        finite(point['z'])
        hi, lo, raw_hi, raw_lo = (number(point[key]) for key in ('hi', 'lo', 'hi_raw', 'lo_raw'))
        alarms += point['alarm']
        high += point['side'] == '偏高'
        low += point['side'] == '偏低'
        segments += point['alarm'] and not previous_alarm
        max_hi, max_lo, max_raw = max(max_hi, hi), max(max_lo, lo), max(max_raw, raw_hi, raw_lo)
        previous_date, previous_alarm = stamp, point['alarm']
    return {'threshold': threshold, 'observed_points': len(points), 'n_alarm': alarms,
            'n_hi': high, 'n_lo': low, 'n_seg': segments, 'max_hi': max_hi,
            'max_lo': max_lo, 'max_raw': max_raw,
            'side': '偏低' if low > high else ('偏高' if high else '无')}


def proportional_scenario(tce, cost, carbon, rate_percent):
    contract()
    tce, cost, carbon, rate_percent = (number(v) for v in (tce, cost, carbon, rate_percent))
    if rate_percent > 100:
        raise ValueError('Scenario reduction must be between 0 and 100 percent')
    rate = rate_percent / 100
    return {'tce': finite(tce * rate), 'cost': finite(cost * rate), 'co2': finite(carbon * rate)}


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
