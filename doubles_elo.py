import json
import math
from elo import expected_score

DATA_FILE = 'doubles_data.json'
K = 24
ELO_FLOOR = 5
dISPARITY_MIN = 3
dISPARITY_MAX = 200   # max diff for scaling


def get_stats(data, user_id):
    """
    Return the stats dict for a given user_id (or None if not found).
    """
    return data.get(str(user_id), None)


def load_data():
    try:
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def register_user(data, user_id):
    key = str(user_id)
    if key not in data:
        data[key] = {
            'elo': 100,
            'wins': 0,
            'losses': 0,
            'streak': 0,
            'medals': [],
            'all_time_gain': 0,
            'all_time_loss': 0,
            'peak_elo': 100,
            'partners': {},   # track wins WITH each teammate
            'partners_losses': {}  # losses with each partner
        }
    else:
        entry = data[key]
        entry.setdefault('elo', 100)
        entry.setdefault('wins', 0)
        entry.setdefault('losses', 0)
        entry.setdefault('streak', 0)
        entry.setdefault('medals', [])
        entry.setdefault('all_time_gain', 0)
        entry.setdefault('all_time_loss', 0)
        entry.setdefault('peak_elo', entry.get('elo', 100))
        entry.setdefault('partners', {})
        entry.setdefault('partners_losses', {})


def process_doubles_match(data, a1, a2, b1, b2):
    for pid in (a1, a2, b1, b2):
        register_user(data, pid)

    a1_before = data[str(a1)]['elo']
    a2_before = data[str(a2)]['elo']
    b1_before = data[str(b1)]['elo']
    b2_before = data[str(b2)]['elo']

    team_a_elo = (a1_before + a2_before) / 2
    team_b_elo = (b1_before + b2_before) / 2

    pA = expected_score(team_a_elo, team_b_elo)
    base_delta = math.ceil(K * (1 - pA))

    disparity = abs(team_a_elo - team_b_elo)
    capped = max(dISPARITY_MIN, min(disparity, dISPARITY_MAX))
    factor = (capped - dISPARITY_MIN) / (dISPARITY_MAX - dISPARITY_MIN)
    win_scale = 1 + factor if team_a_elo < team_b_elo else 1.0
    loss_scale = 1 - factor if team_b_elo < team_a_elo else 1.0

    delta_win = math.ceil(base_delta * win_scale)
    delta_loss = math.ceil(base_delta * loss_scale)

    for pid in (a1, a2):
        e = data[str(pid)]
        e['elo'] = max(ELO_FLOOR, e['elo'] + delta_win)
        e['wins'] += 1
        e['streak'] += 1
        e['all_time_gain'] += delta_win
        e['peak_elo'] = max(e['peak_elo'], e['elo'])
        other = a2 if pid == a1 else a1
        e['partners'][str(other)] = e['partners'].get(str(other), 0) + 1

    for pid in (b1, b2):
        e = data[str(pid)]
        e['elo'] = max(ELO_FLOOR, e['elo'] - delta_loss)
        e['losses'] += 1
        e['streak'] = 0
        e['all_time_loss'] += delta_loss
        other = b2 if pid == b1 else b1
        e['partners_losses'][str(other)] = e['partners_losses'].get(
            str(other), 0) + 1

    save_data(data)

    a1_after = data[str(a1)]['elo']
    a2_after = data[str(a2)]['elo']
    b1_after = data[str(b1)]['elo']
    b2_after = data[str(b2)]['elo']

    return {
        'delta_win': delta_win,
        'delta_loss': delta_loss,
        'a1_before': a1_before,
        'a2_before': a2_before,
        'b1_before': b1_before,
        'b2_before': b2_before,
        'a1_after': a1_after,
        'a2_after': a2_after,
        'b1_after': b1_after,
        'b2_after': b2_after
    }
