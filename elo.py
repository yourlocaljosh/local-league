import json
import math


DATA_FILE = 'data.json'
K = 24
ELO_FLOOR = 5
HISTORY_LIMIT = 10
dISPARITY_MIN = 3
dISPARITY_MAX = 200   # maximum diff consideration


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
            'first_5_bonus': 0,
            'streak': 0,
            'head_to_head': {},
            'medals': [],
            'all_time_gain': 0,
            'all_time_loss': 0,
            'match_history': [],
            'peak_elo': 100
        }
    else:
        entry = data[key]
        entry.setdefault('head_to_head', {})
        entry.setdefault('medals', [])
        entry.setdefault('all_time_gain', 0)
        entry.setdefault('all_time_loss', 0)
        entry.setdefault('match_history', [])


def get_stats(data, user_id):
    return data.get(str(user_id), None)


def set_stat(data, user_id, stat, value):
    if str(user_id) in data:
        data[str(user_id)][stat] = value


def expected_score(player_elo, opponent_elo):
    return 1 / (1 + 10 ** ((opponent_elo - player_elo) / 200))


def _append_single_history(entry, *,
                           player_is_winner: bool,
                           winner_id: int,
                           opponent_id: int,
                           score_w: int,
                           score_l: int,
                           player_elo_after: int,
                           opponent_elo_after: int,
                           match_id: str = None,
                           logged_at: str = None):
    """
    Append one perspective history record to `entry['match_history']`.
    `entry` is the player dict (already registered).
    """
    entry.setdefault('match_history', [])
    rec = {
        "winner_id": winner_id,
        "opponent_id": opponent_id,
        "result": 'W' if player_is_winner else 'L',
        "score_w": score_w,
        "score_l": score_l,
        "elo_after": player_elo_after,
        "opponent_elo_after": opponent_elo_after,
    }
    if match_id:
        rec["match_id"] = match_id
    if logged_at:
        rec["logged_at"] = logged_at
    entry['match_history'].insert(0, rec)
    if len(entry['match_history']) > HISTORY_LIMIT:
        entry['match_history'] = entry['match_history'][:HISTORY_LIMIT]


def append_match_history(data,
                         winner_id: int,
                         loser_id: int,
                         score_w: int,
                         score_l: int,
                         winner_elo_after: int,
                         loser_elo_after: int,
                         match_id: str = None,
                         logged_at: str = None):
    """
    Canonical way to log history for BOTH players (newest first).
    Safe to call from process_match *after* updating elos/statistics,
    or from a retroactive logging command (provided elos you pass
    are already post-match).
    """
    w = data[str(winner_id)]
    loser = data[str(loser_id)]

    _append_single_history(
        w,
        player_is_winner=True,
        winner_id=winner_id,
        opponent_id=loser_id,
        score_w=score_w,
        score_l=score_l,
        player_elo_after=winner_elo_after,
        opponent_elo_after=loser_elo_after,
        match_id=match_id,
        logged_at=logged_at
    )
    _append_single_history(
        loser,
        player_is_winner=False,
        winner_id=winner_id,
        opponent_id=winner_id,
        score_w=score_w,
        score_l=score_l,
        player_elo_after=loser_elo_after,
        opponent_elo_after=winner_elo_after,
        match_id=match_id,
        logged_at=logged_at
    )


def process_match(
        data,
        winner_id,
        loser_id,
        score_w=None,
        score_l=None,
        match_id=None,
        logged_at=None):
    wkey, lkey = str(winner_id), str(loser_id)
    winner = data[wkey]
    loser = data[lkey]

    winner.setdefault('head_to_head', {})
    loser .setdefault('head_to_head', {})

    record_w = winner['head_to_head'].setdefault(
        lkey, {'wins': 0, 'losses': 0})
    record_l = loser['head_to_head'].setdefault(wkey, {'wins': 0, 'losses': 0})

    w_before = winner['elo']
    l_before = loser['elo']

    p = expected_score(w_before, l_before)

    base_change = math.ceil(K * (1 - p))

    disparity = abs(w_before - l_before)
    capped = max(dISPARITY_MIN, min(disparity, dISPARITY_MAX))
    factor = (capped - dISPARITY_MIN) / (dISPARITY_MAX - dISPARITY_MIN)

    if w_before < l_before:
        win_scale = 1 + factor
    else:
        win_scale = 1
    if l_before < w_before:
        loss_scale = 1 - factor
    else:
        loss_scale = 1

    elo_gain = math.ceil(base_change * win_scale)
    elo_loss = math.ceil(base_change * loss_scale)

    bonus = 5 if winner['wins'] < 5 else 0
    total_gain = elo_gain + bonus

    winner['elo'] = max(ELO_FLOOR, w_before + total_gain)
    loser['elo'] = max(ELO_FLOOR, l_before - elo_loss)

    w_after = winner['elo']
    l_after = loser['elo']

    winner['all_time_gain'] += total_gain
    loser['all_time_loss'] += elo_loss

    winner['wins'] += 1
    if bonus:
        winner['first_5_bonus'] += 1
    winner['streak'] += 1
    loser['losses'] += 1
    loser['streak'] = 0
    record_w['wins'] += 1
    record_l['losses'] += 1

    append_match_history(
        data,
        winner_id=winner_id,
        loser_id=loser_id,
        score_w=score_w if score_w is not None else 0,
        score_l=score_l if score_l is not None else 0,
        winner_elo_after=w_after,
        loser_elo_after=l_after,
        match_id=match_id,
        logged_at=logged_at
    )

    winner.setdefault('peak_elo', winner['elo'])
    loser.setdefault('peak_elo', loser['elo'])

    if winner['elo'] > winner['peak_elo']:
        winner['peak_elo'] = winner['elo']
    if loser['elo'] > loser['peak_elo']:
        loser['peak_elo'] = loser['elo']

    return {
        'winner_elo_before': w_before,
        'loser_elo_before': l_before,
        'elo_gain': elo_gain,
        'elo_loss': elo_loss,
        'bonus': bonus,
        'total_gain': total_gain,
        'new_streak': winner['streak'],
        'winner_elo_after': w_after,
        'loser_elo_after': l_after
    }
