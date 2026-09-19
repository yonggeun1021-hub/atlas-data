"""S6-B5 semantic checklist: deterministic evaluators for the eight B5 briefing checks.

Pure module: no file, network, clock or environment access. Every function takes already-verified bytes (read by the
caller through atlas_authority.read_pinned_input from the frozen input manifest) or values parsed from them, and returns
plain dicts. The caller (semantic_briefing_runtime.admit) decides admission; this module only evaluates.

Source: CLAUDE_CIO_BRIEFING_VERIFICATION_TIMELINE_AUDIT_20260914.md section B5 (items 1-8), failure classes from tables
B1/B2. Each evaluator returns {check_id, status, reason, evidence_refs} with status in PASS | PASS_WITH_CORRECTION |
NOT_VERIFIABLE | HOLD. An evaluator may be restricted to a subset of its named rules (tests isolate audit rows that share
one check).
"""
import datetime as dt
import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CONTRACT = 'atlas_b5_semantic_checklist/1'
PASS, PWC, NV, HOLD = 'PASS', 'PASS_WITH_CORRECTION', 'NOT_VERIFIABLE', 'HOLD'
STATUSES = (PASS, PWC, NV, HOLD)
# Strictness order used for aggregation and for model-vs-deterministic comparison (higher = stricter).
STRICTNESS = {PASS: 0, NV: 1, PWC: 2, HOLD: 3}

SESSION_RECONCILIATION = 'B5-1_SESSION_RECONCILIATION'
TRADING_CALENDAR = 'B5-2_TRADING_CALENDAR'
COMPONENT_AS_OF_DATES = 'B5-3_COMPONENT_AS_OF_DATES'
POINTER_FRESHNESS = 'B5-4_POINTER_FRESHNESS'
DECISION_RELEVANT_OMISSION = 'B5-5_DECISION_RELEVANT_OMISSION'
RECOMPUTABILITY = 'B5-6_RECOMPUTABILITY'
PORTAL_PARITY = 'B5-7_PORTAL_PARITY'
SPOT_RECOMPUTE = 'B5-8_SPOT_RECOMPUTE'
CHECK_IDS = (SESSION_RECONCILIATION, TRADING_CALENDAR, COMPONENT_AS_OF_DATES, POINTER_FRESHNESS,
             DECISION_RELEVANT_OMISSION, RECOMPUTABILITY, PORTAL_PARITY, SPOT_RECOMPUTE)
RULES = {
    SESSION_RECONCILIATION: ('KRX_BOARD_VS_LATEST_KRX', 'DYNAMIC_CLOCK_KOREA_VS_LATEST_KRX', 'US_BOARD_VS_US_REFERENCE',
                             'UNSCOPED_CONFIRMED_EVIDENCE_DATE'),
    TRADING_CALENDAR: ('KRX_LAST_COMPLETED_SESSION', 'KRX_OBSERVED_UNCONFIRMED_NOT_UNKNOWN', 'US_LAST_COMPLETED_SESSION'),
    COMPONENT_AS_OF_DATES: ('FORWARD_ALPHA', 'DART_ROWS', 'OFFICIAL_RELEASE_SUMMARY', 'DYNAMIC_CLOCK_ROWS', 'SENSOR_ROWS'),
    POINTER_FRESHNESS: ('FULL_LIST_POINTERS',),
    DECISION_RELEVANT_OMISSION: ('PAPER_CANDIDATE_REGIME', 'US_ETF_CLOSES'),
    RECOMPUTABILITY: ('KOREA_INDEX_PCT', 'VIXCLS', 'US_BREADTH_MEMBERS', 'US_ETF_SESSION_RETURN'),
    PORTAL_PARITY: ('KRX_FRESHNESS_LABEL', 'US_CARD_SESSION_DATE'),
    SPOT_RECOMPUTE: ('US_ETF_SESSION_RETURN', 'US_BREADTH_MEMBERS', 'VIXCLS', 'CRYPTO_RAW_RECOMPUTE', 'KOREA_INDEX_PCT'),
}

# ------------------------------------------------------------------------------------------------ pinned inputs
# Manifest input ids consumed by the evaluators, with the atlas-data repo path each must be committed at.
# 'pattern' entries carry the dated directory; the runtime additionally binds krx_post_close to the latest dated
# directory at the pinned workspace head.
DATE = r'\d{4}-\d{2}-\d{2}'
B5_INPUTS = {
    'b5_latest_krx': {'path': 'data/latest_krx.json', 'required': True},
    'b5_krx_post_close_index': {'pattern': rf'data/observations/krx_post_close/({DATE})/index\.json', 'required': True},
    'b5_free_market_data': {'path': 'data/latest_free_market_data.json', 'required': True},
    'b5_paper_regime_reference': {'path': 'data/latest_paper_regime_reference.json', 'required': True},
    'b5_dynamic_clock_section': {'path': 'evidence/operational/dynamic_clock/briefing_section.json', 'required': True},
    'b5_korea_symbol_market_review': {'path': 'data/latest_korea_symbol_market_review.json', 'required': True},
    'b5_korea_market_signals_packet': {'pattern': rf'data/observations/korea_market_signals/({DATE})/packet\.json',
                                       'required': True},
    'b5_dart_content': {'path': 'data/latest_dart_content.json', 'required': True},
    'b5_us_breadth_manifest': {'pattern': rf'evidence/us_breadth/raw/({DATE})/_manifest\.json', 'required': True},
    'b5_pilot_evidence_intake': {'path': 'decision/pilot_evidence_intake.py', 'required': True, 'text': True},
    'b5_official_release_summary': {
        'pattern': rf'data/observations/official_release_summary_observations/({DATE})/[A-Za-z0-9._-]+\.json',
        'required': False},
    # Portal-rendered close cards (atlas-portal read model); not an atlas-data blob, pinned under the input root.
    'b5_portal_close_cards': {'path': None, 'required': False},
}
REQUIRED_B5_INPUTS = frozenset(k for k, v in B5_INPUTS.items() if v['required'])


def repo_path_matches(input_id, repo_path):
    spec = B5_INPUTS[input_id]
    if spec.get('pattern'):
        return bool(repo_path) and re.fullmatch(spec['pattern'], repo_path) is not None
    return repo_path == spec['path']


# ------------------------------------------------------------------------------------------- exchange calendars
# KRX 2026 closed days besides weekends. Justification (Korean public-holiday law incl. substitute-holiday rules, KRX
# year-end closing): 01-01 New Year; 02-16..18 Seollal (lunar new year 02-17); 03-02 substitute for Samiljeol (03-01
# Sunday); 05-01 Labour Day (KRX closed); 05-05 Children's Day; 05-25 substitute for Buddha's Birthday (05-24 Sunday);
# 06-03 nationwide local election day; 08-17 substitute for Liberation Day (08-15 Saturday); 09-24..25 Chuseok (lunar
# 08-15 = 09-25; 09-26 is Saturday, no substitute for a Saturday overlap); 10-05 substitute for Gaecheonjeol (10-03
# Saturday); 10-09 Hangul Day; 12-25 Christmas; 12-31 KRX year-end closing day.
KRX_HOLIDAYS = frozenset({
    '2026-01-01', '2026-02-16', '2026-02-17', '2026-02-18', '2026-03-02', '2026-05-01', '2026-05-05', '2026-05-25',
    '2026-06-03', '2026-08-17', '2026-09-24', '2026-09-25', '2026-10-05', '2026-10-09', '2026-12-25', '2026-12-31'})
# Dates whose KRX status could not be justified (Constitution Day 07-17 public-holiday restoration not confirmed).
KRX_UNCERTAIN = frozenset({'2026-07-17'})
# KRX regular close 15:30 KST; 2026 CSAT day (11-19) shifts the session by one hour (close 16:30 KST).
KRX_CLOSE_KST = {'default': (15, 30), '2026-11-19': (16, 30)}
# NYSE 2026 holidays: New Year, MLK (01-19), Washington's Birthday (02-16), Good Friday (04-03, Easter 04-05),
# Memorial Day (05-25), Juneteenth (06-19), Independence Day observed (07-03, 07-04 Saturday), Labor Day (09-07),
# Thanksgiving (11-26), Christmas (12-25). Early closes 13:00 ET: 11-27, 12-24.
US_HOLIDAYS = frozenset({
    '2026-01-01', '2026-01-19', '2026-02-16', '2026-04-03', '2026-05-25', '2026-06-19', '2026-07-03', '2026-09-07',
    '2026-11-26', '2026-12-25'})
US_UNCERTAIN = frozenset()
US_EARLY_CLOSE_ET = {'2026-11-27': (13, 0), '2026-12-24': (13, 0)}
CALENDAR_YEARS = frozenset({2026})
CALENDAR_CONFIDENCE = 'EXCHANGE_TABLE_2026_WEEKENDS_HOLIDAYS_NOT_EXCHANGE_PUBLISHED_COPY'


def _date(value):
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if re.fullmatch(r'\d{8}', text):
        text = f'{text[:4]}-{text[4:6]}-{text[6:]}'
    match = re.match(rf'^({DATE})', text)
    if not match:
        return None
    try:
        return dt.date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _nth_sunday(year, month, n):
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def _us_dst(day):
    return _nth_sunday(day.year, 3, 2) <= day < _nth_sunday(day.year, 11, 1)


def is_session(market, day):
    holidays = KRX_HOLIDAYS if market == 'KRX' else US_HOLIDAYS
    return day.weekday() < 5 and day.isoformat() not in holidays


def session_close_utc(market, day):
    if market == 'KRX':
        hour, minute = KRX_CLOSE_KST.get(day.isoformat(), KRX_CLOSE_KST['default'])
        return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=dt.timezone.utc) - dt.timedelta(hours=9)
    hour, minute = US_EARLY_CLOSE_ET.get(day.isoformat(), (16, 0))
    offset = 4 if _us_dst(day) else 5
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=dt.timezone.utc) + dt.timedelta(hours=offset)


def covered(market, start, end):
    """Calendar confidence for every day in [start, end]: (True, note) only when the table covers each day."""
    uncertain = KRX_UNCERTAIN if market == 'KRX' else US_UNCERTAIN
    day = start
    while day <= end:
        if day.year not in CALENDAR_YEARS:
            return False, f'{market}_CALENDAR_OUT_OF_TABLE:{day.isoformat()}'
        if day.isoformat() in uncertain:
            return False, f'{market}_CALENDAR_UNCERTAIN_DAY:{day.isoformat()}'
        day += dt.timedelta(days=1)
    return True, f'{market}:{CALENDAR_CONFIDENCE}'


def last_completed_session(market, at_utc):
    """(session date, confidence note) of the latest session of ``market`` whose close is at or before ``at_utc``."""
    day = at_utc.date() + dt.timedelta(days=1)
    for _ in range(40):
        if is_session(market, day) and session_close_utc(market, day) <= at_utc:
            ok, note = covered(market, day, at_utc.date() + dt.timedelta(days=1))
            return (day if ok else None), note
        day -= dt.timedelta(days=1)
    return None, f'{market}_NO_SESSION_IN_40_DAYS'


def previous_session(market, day):
    probe = day - dt.timedelta(days=1)
    for _ in range(40):
        if is_session(market, probe):
            ok, note = covered(market, probe, day)
            return (probe if ok else None), note
        probe -= dt.timedelta(days=1)
    return None, f'{market}_NO_SESSION_IN_40_DAYS'


def sessions_after(market, after, through):
    """(count of sessions d with after < d <= through, confidence note)."""
    if through <= after:
        return 0, f'{market}:{CALENDAR_CONFIDENCE}'
    ok, note = covered(market, after + dt.timedelta(days=1), through)
    if not ok:
        return None, note
    count, day = 0, after + dt.timedelta(days=1)
    while day <= through:
        count += is_session(market, day)
        day += dt.timedelta(days=1)
    return count, note


# ----------------------------------------------------------------------------------------------- payload parsing
COMPONENT = re.compile(r'^- \*\*([A-Z0-9_]+)\*\*: ?(.*)$')
KV_LINE = re.compile(r'^- ([A-Za-z_][A-Za-z0-9_]*): (.*)$')
TOKEN = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=([^\s,]+)')
POINTER = re.compile(r'full list: ([A-Za-z0-9_./-]+)\)')
PAPER_LABEL = ('PAPER 참고', '런타임 미승인')


def tokens(text):
    return dict(TOKEN.findall(text))


def parse_payload(text):
    """Structure of the sealed machine-rendered payload (headings, session board, weekend context, component blocks)."""
    lines = text.splitlines()
    parsed = {'generated_at': None, 'board': {}, 'weekend': {}, 'components': {}, 'lines': lines}
    section = board_market = component = None
    for line in lines:
        match = re.match(r'^Generated at: (\S+)\s*$', line)
        if match:
            parsed['generated_at'] = _timestamp(match.group(1))
            continue
        if line.startswith('#'):
            level = len(line) - len(line.lstrip('#'))
            title = line[level:].strip()
            component = None
            if level == 2:
                section, board_market = title, None
            elif level == 3 and section and section.startswith('3-market session board'):
                board_market = title.split()[0] if title else None
            elif level == 1:
                section = board_market = None
            continue
        match = COMPONENT.match(line)
        if match:
            component = match.group(1)
            parsed['components'].setdefault(component, [])
            continue
        if component and line.startswith('  '):
            parsed['components'][component].append(line)
            continue
        if not line.strip():
            component = None
            continue
        component = None
        match = KV_LINE.match(line)
        if match and section:
            if section.startswith('3-market session board') and board_market:
                parsed['board'].setdefault(board_market, {})[match.group(1)] = match.group(2).strip()
            elif section.startswith('Weekend market session context'):
                parsed['weekend'][match.group(1)] = match.group(2).strip()
    return parsed


def dynamic_clock_rows(parsed):
    rows, group = [], None
    for line in parsed['components'].get('DYNAMIC_CLOCK', []):
        match = re.match(r'^    - ([A-Z]+): raw_triggers', line)
        if match:
            group = match.group(1)
            continue
        match = re.match(r'^      - (IMMEDIATE_REVIEW|WATCH_REVIEW|OBSERVATION_ONLY) (\S+) (.*)$', line)
        if match:
            rows.append({'group': group, 'tier': match.group(1), 'symbol': match.group(2), 'tokens': tokens(match.group(3))})
    return rows


# ---------------------------------------------------------------------------------------------------- inputs
def build_inputs(*, briefing_date, slot, payload, sources):
    """Assemble evaluator inputs from verified bytes.

    payload: sealed payload bytes. sources: {input_id: {'repo_path': str | None, 'body': bytes}} for B5_INPUTS ids.
    """
    text = payload.decode('utf-8') if isinstance(payload, (bytes, bytearray)) else str(payload)
    inputs = {'briefing_date': _date(briefing_date), 'slot': slot, 'payload_text': text, 'payload': parse_payload(text),
              'src': {}, 'repo_paths': {}, 'errors': {}}
    for input_id, row in (sources or {}).items():
        if input_id not in B5_INPUTS:
            raise ValueError('B5_INPUT_UNKNOWN:' + input_id)
        body = row['body']
        inputs['repo_paths'][input_id] = row.get('repo_path')
        if B5_INPUTS[input_id].get('text'):
            inputs['src'][input_id] = body.decode('utf-8', 'replace')
            continue
        try:
            value = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            inputs['errors'][input_id] = 'INPUT_UNPARSEABLE'
            continue
        if isinstance(value, dict):
            inputs['src'][input_id] = value
        else:
            inputs['errors'][input_id] = 'INPUT_NOT_A_JSON_OBJECT'
    return inputs


def _get(value, *path):
    for key in path:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return None
    return value


class _Eval:
    def __init__(self, check_id, inputs, rules):
        known = RULES[check_id]
        if rules is not None and not set(rules) <= set(known):
            raise ValueError('B5_RULE_UNKNOWN:' + ','.join(sorted(set(rules) - set(known))))
        self.check_id, self.inputs, self.rules = check_id, inputs, rules
        self.findings, self.refs, self.notes = [], [], []

    def on(self, rule):
        return self.rules is None or rule in self.rules

    def add(self, status, rule, detail):
        self.findings.append((status, f'{rule}:{detail}'))

    def ref(self, *refs):
        for ref in refs:
            if ref not in self.refs:
                self.refs.append(ref)

    def note(self, text):
        if text not in self.notes:
            self.notes.append(text)

    def source(self, rule, input_id):
        """The parsed pinned source, or None after recording why it cannot be used."""
        if input_id in self.inputs['errors']:
            self.add(HOLD, rule, f'{self.inputs["errors"][input_id]}:{input_id}')
            return None
        if input_id not in self.inputs['src']:
            self.add(NV, rule, 'INPUT_NOT_PINNED:' + input_id)
            return None
        self.ref(input_id)
        return self.inputs['src'][input_id]

    def result(self):
        status = PASS
        for finding_status, _ in self.findings:
            if STRICTNESS[finding_status] > STRICTNESS[status]:
                status = finding_status
        reason = '; '.join(detail for _, detail in self.findings) or 'OK'
        if self.notes:
            reason += ' | ' + '; '.join(self.notes)
        return {'check_id': self.check_id, 'status': status, 'reason': reason, 'evidence_refs': list(self.refs)}


def _generated_at(ev, rule):
    at = ev.inputs['payload']['generated_at']
    if at is None:
        ev.add(NV, rule, 'GENERATED_AT_MISSING')
    else:
        ev.ref('payload#generated_at')
    return at


def _market_of(subject):
    return 'KRX' if re.search(r'(\.KS|\.KQ)$', subject) or re.fullmatch(r'\d{6}', subject) else 'US'


# ------------------------------------------------------------------------------------------------ evaluators
def session_reconciliation(inputs, rules=None):
    ev = _Eval(SESSION_RECONCILIATION, inputs, rules)
    board = inputs['payload']['board']
    krx = fmd = None
    if ev.on('KRX_BOARD_VS_LATEST_KRX') or ev.on('DYNAMIC_CLOCK_KOREA_VS_LATEST_KRX') or ev.on('UNSCOPED_CONFIRMED_EVIDENCE_DATE'):
        krx = ev.source('KRX_BOARD_VS_LATEST_KRX', 'b5_latest_krx')
    confirmed = _date(_get(krx, 'decision_readiness', 'confirmed_through')) if krx is not None else None
    if ev.on('KRX_BOARD_VS_LATEST_KRX') and krx is not None:
        rendered = _date(_get(board, 'KRX', 'latest_confirmed_close_date'))
        ev.ref('payload#session_board.KRX.latest_confirmed_close_date', 'b5_latest_krx#decision_readiness.confirmed_through')
        if confirmed is None or rendered is None:
            ev.add(HOLD, 'KRX_BOARD_VS_LATEST_KRX', f'DATE_MISSING:rendered={rendered},source={confirmed}')
        elif rendered != confirmed:
            ev.add(HOLD, 'KRX_BOARD_VS_LATEST_KRX', f'rendered={rendered},source={confirmed}')
    if ev.on('DYNAMIC_CLOCK_KOREA_VS_LATEST_KRX') and krx is not None:
        rows = [r for r in dynamic_clock_rows(inputs['payload']) if r['group'] == 'KOREA'
                and _date(r['tokens'].get('price_observation_date'))]
        if rows:
            ev.ref('payload#DYNAMIC_CLOCK.KOREA.price_observation_date')
        bad = sorted({(r['symbol'], r['tokens']['price_observation_date']) for r in rows
                      if _date(r['tokens']['price_observation_date']) != confirmed})
        if bad:
            ev.add(HOLD, 'DYNAMIC_CLOCK_KOREA_VS_LATEST_KRX',
                   f'rendered={",".join(s + "@" + d for s, d in bad)},source={confirmed}')
    us_source = None
    if ev.on('US_BOARD_VS_US_REFERENCE') or ev.on('UNSCOPED_CONFIRMED_EVIDENCE_DATE'):
        fmd = ev.source('US_BOARD_VS_US_REFERENCE', 'b5_free_market_data')
        us_source = _date(_get(fmd, 'us_market_reference', 'as_of_session_date')) if fmd is not None else None
    if ev.on('US_BOARD_VS_US_REFERENCE') and fmd is not None:
        rendered = _date(_get(board, 'US', 'latest_verified_us_session_date'))
        ev.ref('payload#session_board.US.latest_verified_us_session_date', 'b5_free_market_data#us_market_reference.as_of_session_date')
        if rendered is None or us_source is None or rendered != us_source:
            ev.add(HOLD, 'US_BOARD_VS_US_REFERENCE', f'rendered={rendered},source={us_source}')
    if ev.on('UNSCOPED_CONFIRMED_EVIDENCE_DATE'):
        raw = inputs['payload']['weekend'].get('latest_confirmed_evidence_date')
        if raw is not None:
            ev.ref('payload#weekend_context.latest_confirmed_evidence_date')
            rendered = _date(raw)
            if krx is not None and fmd is not None and not (rendered == confirmed == us_source):
                ev.add(HOLD, 'UNSCOPED_CONFIRMED_EVIDENCE_DATE',
                       f'rendered={raw} is not market-scoped: KRX confirmed={confirmed}, US session={us_source}')
    return ev.result()


def trading_calendar(inputs, rules=None):
    ev = _Eval(TRADING_CALENDAR, inputs, rules)
    board, text = inputs['payload']['board'], inputs['payload_text']
    at = _generated_at(ev, 'CALENDAR')
    if at is None:
        return ev.result()
    confirmed_rendered = _date(_get(board, 'KRX', 'latest_confirmed_close_date'))
    observed_raw = _get(board, 'KRX', 'latest_observed_unconfirmed_date')
    observed_rendered = _date(observed_raw)
    krx_expected = None
    if ev.on('KRX_LAST_COMPLETED_SESSION') or ev.on('KRX_OBSERVED_UNCONFIRMED_NOT_UNKNOWN'):
        krx_expected, note = last_completed_session('KRX', at)
        ev.note('calendar ' + note)
        if krx_expected is None:
            ev.add(NV, 'KRX_LAST_COMPLETED_SESSION', 'CALENDAR_NOT_COVERED:' + note)
    if ev.on('KRX_LAST_COMPLETED_SESSION') and krx_expected is not None:
        ev.ref('payload#session_board.KRX.latest_confirmed_close_date', 'payload#session_board.KRX.latest_observed_unconfirmed_date')
        explicit = f'{krx_expected.isoformat()} 세션 미확정' in text
        if krx_expected not in (confirmed_rendered, observed_rendered) and not explicit:
            ev.add(HOLD, 'KRX_LAST_COMPLETED_SESSION',
                   f'completed_session={krx_expected} not rendered (confirmed={confirmed_rendered},'
                   f'observed_unconfirmed={observed_raw}) and no explicit "{krx_expected} 세션 미확정" statement')
    if ev.on('KRX_OBSERVED_UNCONFIRMED_NOT_UNKNOWN'):
        index = ev.source('KRX_OBSERVED_UNCONFIRMED_NOT_UNKNOWN', 'b5_krx_post_close_index')
        krx = ev.source('KRX_OBSERVED_UNCONFIRMED_NOT_UNKNOWN', 'b5_latest_krx')
        match = re.fullmatch(B5_INPUTS['b5_krx_post_close_index']['pattern'], inputs['repo_paths'].get('b5_krx_post_close_index') or '')
        if index is not None and krx is not None:
            day = _date(match.group(1)) if match else None
            confirmed = _date(_get(krx, 'decision_readiness', 'confirmed_through'))
            if day is None or _date(index.get('latest_observed_day')) not in (None, day):
                ev.add(HOLD, 'KRX_OBSERVED_UNCONFIRMED_NOT_UNKNOWN', 'POST_CLOSE_INDEX_DATE_UNBOUND')
            elif (index.get('observation_status') == 'observed_unconfirmed' and confirmed is not None and day > confirmed
                  and (krx_expected is None or day <= krx_expected)):
                ev.ref(f'b5_krx_post_close_index#krx_post_close/{day}/index.json.observation_status')
                if observed_rendered != day:
                    ev.add(HOLD, 'KRX_OBSERVED_UNCONFIRMED_NOT_UNKNOWN',
                           f'krx_post_close/{day}/index.json is observed_unconfirmed but rendered={observed_raw}')
    if ev.on('US_LAST_COMPLETED_SESSION'):
        us_expected, note = last_completed_session('US', at)
        ev.note('calendar ' + note)
        rendered = _date(_get(board, 'US', 'latest_verified_us_session_date'))
        ev.ref('payload#session_board.US.latest_verified_us_session_date')
        if us_expected is None:
            ev.add(NV, 'US_LAST_COMPLETED_SESSION', 'CALENDAR_NOT_COVERED:' + note)
        elif rendered != us_expected and f'{us_expected.isoformat()} US 세션 미확정' not in text:
            ev.add(HOLD, 'US_LAST_COMPLETED_SESSION', f'completed_session={us_expected},rendered={rendered}')
    return ev.result()


STALENESS_SESSIONS = {'price': 1, 'filing': 5}


def component_rows(ev):
    inputs = ev.inputs
    parsed = inputs['payload']
    rows = []
    if ev.on('FORWARD_ALPHA'):
        lines = [line for line in parsed['components'].get('FORWARD_ALPHA_REVIEW', []) if 'opportunity_state=' in line]
        pilot_date = None
        if lines:
            code = ev.source('FORWARD_ALPHA', 'b5_pilot_evidence_intake')
            match = re.search(r'^PILOT_DECISION_DATE\s*=\s*["\'](\d{4}-\d{2}-\d{2})["\']', code or '', re.M)
            pilot_date = _date(match.group(1)) if match else None
        for line in lines:
            subject = re.match(r'^\s*- (\S+): opportunity_state=', line)
            tok = tokens(line)
            rendered = next((tok[k] for k in ('decision_date', 'pilot_decision_date', 'as_of') if k in tok), None)
            rows.append({'rule': 'FORWARD_ALPHA', 'component': 'FORWARD_ALPHA_REVIEW', 'subject': subject.group(1) if subject else '?',
                         'kind': 'price', 'tags': {'decision_date': (rendered, pilot_date)},
                         'ref': 'b5_pilot_evidence_intake#PILOT_DECISION_DATE'})
    if ev.on('DART_ROWS'):
        lines = [(m, line) for line in parsed['components'].get('ROTATION_DISCOVERY', [])
                 for m in [re.match(r'^\s*- DART (\d{6}) ([^:]+): (.+?) evidence=', line)] if m]
        records = []
        if lines:
            dart = ev.source('DART_ROWS', 'b5_dart_content')
            records = (dart or {}).get('records') or []
        for match, line in lines:
            source = next((_date(r.get('filing_date')) for r in records
                           if r.get('ticker') == match.group(1) and (r.get('title') or '').strip() == match.group(3).strip()), None)
            rows.append({'rule': 'DART_ROWS', 'component': 'ROTATION_DISCOVERY', 'subject': f'DART {match.group(1)} {match.group(3)}',
                         'kind': 'filing', 'market': 'KRX', 'tags': {'filing_date': (tokens(line).get('filing_date'), source)},
                         'ref': 'b5_dart_content#records[].filing_date'})
    if ev.on('OFFICIAL_RELEASE_SUMMARY'):
        lines = [(m, line) for line in parsed['components'].get('OFFICIAL_RELEASE_SUMMARY', [])
                 for m in [re.match(r'^\s*- ([A-Z][A-Z0-9.]*): (.+?) published_at=(\S+)', line)] if m]
        summary = ev.source('OFFICIAL_RELEASE_SUMMARY', 'b5_official_release_summary') if lines else None
        for match, line in lines:
            tok = tokens(line)
            observation = next((o for o in (summary or {}).get('observations') or [] if o.get('subject') == match.group(1)), {})
            as_of = (summary or {}).get('evidence_as_of') if (summary or {}).get('subject') == match.group(1) else None
            rows.append({'rule': 'OFFICIAL_RELEASE_SUMMARY', 'component': 'OFFICIAL_RELEASE_SUMMARY', 'subject': match.group(1),
                         'kind': 'filing', 'tags': {'published_at': (tok.get('published_at'), _date(observation.get('published_at'))),
                                                    'evidence_as_of': (tok.get('evidence_as_of'), _date(as_of))},
                         'ref': 'b5_official_release_summary#evidence_as_of'})
    if ev.on('DYNAMIC_CLOCK_ROWS'):
        for row in dynamic_clock_rows(parsed):
            if row['group'] == 'KOREA':
                rows.append({'rule': 'DYNAMIC_CLOCK_ROWS', 'component': 'DYNAMIC_CLOCK', 'subject': row['symbol'], 'kind': 'price',
                             'market': 'KRX', 'tags': {'price_observation_date': (row['tokens'].get('price_observation_date'), None)},
                             'ref': 'payload#DYNAMIC_CLOCK.KOREA'})
    if ev.on('SENSOR_ROWS'):
        for line in parsed['components'].get('US_BREADTH_MEMBERSHIP', []):
            if 'members=' in line:
                rows.append({'rule': 'SENSOR_ROWS', 'component': 'US_BREADTH_MEMBERSHIP', 'subject': 'members', 'kind': 'price',
                             'market': 'US', 'tags': {'snapshot_date': (tokens(line).get('snapshot_date'), None)},
                             'ref': 'payload#US_BREADTH_MEMBERSHIP'})
        for line in parsed['components'].get('FREE_MARKET_DATA', []):
            if 'VIXCLS=' in line:
                rows.append({'rule': 'SENSOR_ROWS', 'component': 'FREE_MARKET_DATA', 'subject': 'VIXCLS', 'kind': 'price',
                             'market': 'US', 'tags': {'as_of': (tokens(line).get('as_of'), None)},
                             'ref': 'payload#FREE_MARKET_DATA.VIXCLS'})
    return rows


def component_as_of_dates(inputs, rules=None):
    ev = _Eval(COMPONENT_AS_OF_DATES, inputs, rules)
    rows = component_rows(ev)
    at = _generated_at(ev, 'COMPONENT_ROWS') if rows else None
    for row in rows:
        ev.ref(row['ref'])
        market = row.get('market') or _market_of(row['subject'])
        for tag, (rendered, source) in row['tags'].items():
            if rendered:
                continue  # the row carries its own date (an explicit UNKNOWN is an explicit statement)
            label = f'{row["component"]}:{row["subject"]}:{tag}'
            if source is None:
                ev.add(HOLD, row['rule'], f'UNDATED_NO_SOURCE_DATE:{label}')
                continue
            if at is None:
                continue
            through, note = last_completed_session(market, at)
            age, age_note = sessions_after(market, source, through) if through else (None, note)
            if age is None:
                ev.add(NV, row['rule'], f'CALENDAR_NOT_COVERED:{label}:{age_note}')
            elif age > STALENESS_SESSIONS[row['kind']]:
                ev.add(HOLD, row['rule'], f'UNDATED_STALE:{label}:source={source}:sessions_old={age}>'
                                          f'{STALENESS_SESSIONS[row["kind"]]}')
            else:
                ev.add(PWC, row['rule'], f'UNDATED_FRESH:{label}:source={source}:sessions_old={age}')
    return ev.result()


def pointer_freshness(inputs, rules=None):
    ev = _Eval(POINTER_FRESHNESS, inputs, rules)
    by_path = {path: input_id for input_id, path in inputs['repo_paths'].items() if path}
    for line in inputs['payload']['lines']:
        for match in POINTER.finditer(line):
            path = match.group(1)
            ev.ref('payload#full_list:' + path)
            input_id = by_path.get(path)
            if input_id is None:
                ev.add(HOLD, 'FULL_LIST_POINTERS', f'POINTER_TARGET_NOT_PINNED:{path}')
                continue
            target = ev.source('FULL_LIST_POINTERS', input_id)
            if target is None:
                continue
            decision = _date(_get(target, 'decision_date'))
            if decision == inputs['briefing_date']:
                continue
            if decision is not None and f'상세 목록 미갱신(기준일 {decision.isoformat()})' in line:
                continue
            ev.add(HOLD, 'FULL_LIST_POINTERS', f'POINTER_STALE:{path}:decision_date={decision},briefing_date={inputs["briefing_date"]}')
    return ev.result()


def decision_relevant_omission(inputs, rules=None):
    ev = _Eval(DECISION_RELEVANT_OMISSION, inputs, rules)
    lines = inputs['payload']['lines']
    if ev.on('PAPER_CANDIDATE_REGIME'):
        paper = ev.source('PAPER_CANDIDATE_REGIME', 'b5_paper_regime_reference')
        if paper is not None:
            labelled = [line for line in lines if all(part in line for part in PAPER_LABEL)]
            omitted = []
            for market in paper.get('markets') or []:
                candidate = _get(market, 'paper_reference', 'candidate_regime')
                if candidate in (None, 'UNKNOWN'):
                    continue
                name = market.get('market')
                shown = any(re.search(rf'(^|[^A-Z]){re.escape(str(name))}([^A-Z]|$)', line) and candidate in line for line in labelled)
                if not shown:
                    ref = market.get('paper_reference') or {}
                    omitted.append(f'{name}={candidate}(score={ref.get("score")},confidence={ref.get("confidence")},'
                                   f'as_of={market.get("as_of_date")})')
            if omitted and _get(paper, 'authority', 'paper_reference_display_authorized') is False:
                ev.note('paper reference display not authorized; omission accepted: ' + ','.join(omitted))
            elif omitted:
                ev.add(PWC, 'PAPER_CANDIDATE_REGIME', 'OMITTED_NOT_LABELLED_"PAPER 참고 · 런타임 미승인":' + ','.join(omitted))
    if ev.on('US_ETF_CLOSES'):
        fmd = ev.source('US_ETF_CLOSES', 'b5_free_market_data')
        omitted = []
        for etf in _get(fmd, 'us_market_reference', 'trend_etfs') or []:
            symbol, close, day = etf.get('symbol'), etf.get('close'), _date(etf.get('as_of_session_date'))
            if not symbol or close in (None, '') or day is None:
                continue
            shown = any(re.search(rf'(^|\W){re.escape(symbol)}(\W|$)', line) and f'close={close}' in line
                        and day.isoformat() in line for line in lines)
            if not shown:
                omitted.append(f'{symbol}={close}@{day}')
        if omitted:
            ev.add(PWC, 'US_ETF_CLOSES', 'DATED_CLOSES_AVAILABLE_BUT_NOT_RENDERED:' + ','.join(omitted))
    return ev.result()


def _cards(ev, rule):
    cards = ev.source(rule, 'b5_portal_close_cards')
    return None if cards is None else [c for c in cards.get('cards') or [] if isinstance(c, dict)]


def _korea_pct_claims(inputs, cards):
    claims = []
    for card in cards or []:
        if card.get('market') == 'KRX':
            for key in ('kospi_one_session_return_pct', 'kosdaq_one_session_return_pct'):
                if card.get(key) not in (None, ''):
                    claims.append((f'portal:{key}@{card.get("as_of_date")}', _date(card.get('as_of_date'))))
    for line in inputs['payload']['lines']:
        for match in re.finditer(r'(코스피|코스닥|KOSPI|KOSDAQ)\s*[:=]?\s*([+-]?\d+(?:\.\d+)?)%', line):
            claims.append((f'payload:{match.group(1)}={match.group(2)}%', None))
    return claims


def recomputability(inputs, rules=None):
    ev = _Eval(RECOMPUTABILITY, inputs, rules)
    parsed = inputs['payload']
    cards = None
    if 'b5_portal_close_cards' in inputs['src']:
        cards = _cards(ev, 'KOREA_INDEX_PCT')
    evaluated = []
    if ev.on('KOREA_INDEX_PCT'):
        claims = _korea_pct_claims(inputs, cards)
        if claims:
            evaluated.append('KOREA_INDEX_PCT')
            packet = ev.source('KOREA_INDEX_PCT', 'b5_korea_market_signals_packet')
            if packet is not None:
                raw = _get(packet, 'source', 'raw_persistence')
                for label, day in claims:
                    if not raw:
                        ev.add(NV, 'KOREA_INDEX_PCT', f'RAW_NOT_PERSISTED(raw_persistence={raw}):{label}')
                    elif day is not None and _date(packet.get('as_of_date')) != day:
                        ev.add(NV, 'KOREA_INDEX_PCT', f'PERSISTENCE_SOURCE_DATE_MISMATCH:{label}:packet={packet.get("as_of_date")}')
    if ev.on('VIXCLS') and any('VIXCLS=' in line for line in parsed['components'].get('FREE_MARKET_DATA', [])):
        evaluated.append('VIXCLS')
        fmd = ev.source('VIXCLS', 'b5_free_market_data')
        retention = str(_get(fmd, 'fred', 'raw_retention') or '')
        if fmd is not None and not retention.startswith('APPEND_ONLY'):
            ev.add(NV, 'VIXCLS', f'RAW_NOT_PERSISTED(raw_retention={retention or None})')
    if ev.on('US_BREADTH_MEMBERS') and any('members=' in line for line in parsed['components'].get('US_BREADTH_MEMBERSHIP', [])):
        evaluated.append('US_BREADTH_MEMBERS')
        manifest = ev.source('US_BREADTH_MEMBERS', 'b5_us_breadth_manifest')
        endpoints = (manifest or {}).get('endpoints') or []
        if manifest is not None and (not endpoints or not all(e.get('raw_file') and e.get('response_sha256') for e in endpoints)):
            ev.add(NV, 'US_BREADTH_MEMBERS', 'RAW_NOT_PERSISTED:endpoint raw_file/response_sha256 missing')
    if ev.on('US_ETF_SESSION_RETURN'):
        us_cards = [c for c in cards or [] if c.get('market') == 'US' and c.get('session_return_pct') not in (None, '')]
        if us_cards:
            evaluated.append('US_ETF_SESSION_RETURN')
            fmd = ev.source('US_ETF_SESSION_RETURN', 'b5_free_market_data')
            raw = _get(fmd, 'alpaca', 'daily_raw_evidence') or {}
            if fmd is not None and not (raw.get('raw_path') and raw.get('raw_file_sha256')):
                ev.add(NV, 'US_ETF_SESSION_RETURN', 'RAW_NOT_PERSISTED:alpaca daily_raw_evidence missing')
    ev.note('families_evaluated=' + (','.join(evaluated) or 'NONE'))
    return ev.result()


def portal_parity(inputs, rules=None):
    ev = _Eval(PORTAL_PARITY, inputs, rules)
    if 'b5_portal_close_cards' not in inputs['src'] and 'b5_portal_close_cards' not in inputs['errors']:
        ev.add(NV, 'KRX_FRESHNESS_LABEL', 'PORTAL_RENDER_NOT_PINNED:b5_portal_close_cards')
        return ev.result()
    cards = _cards(ev, 'KRX_FRESHNESS_LABEL')
    if cards is None:
        return ev.result()
    at = _generated_at(ev, 'KRX_FRESHNESS_LABEL')
    if at is None:
        return ev.result()
    kst_day = (at + dt.timedelta(hours=9)).date()
    for card in cards:
        day = _date(card.get('as_of_date'))
        if card.get('market') == 'KRX' and ev.on('KRX_FRESHNESS_LABEL'):
            label = str(card.get('freshness_label') or '')
            ev.ref('b5_portal_close_cards#KRX.freshness_label')
            if day is None:
                ev.add(HOLD, 'KRX_FRESHNESS_LABEL', 'CARD_UNDATED')
            elif '새 세션 없음' in label or '휴장' in label:
                completed, note = last_completed_session('KRX', at)
                ev.note('calendar ' + note)
                if completed is None:
                    ev.add(NV, 'KRX_FRESHNESS_LABEL', 'CALENDAR_NOT_COVERED:' + note)
                elif is_session('KRX', kst_day):
                    ev.add(HOLD, 'KRX_FRESHNESS_LABEL', f'CLOSED_LABEL_ON_TRADING_DAY:{kst_day}:label="{label}"')
                elif completed > day:
                    ev.add(HOLD, 'KRX_FRESHNESS_LABEL', f'NO_NEW_SESSION_LABEL_BUT_SESSION_COMPLETED:card={day},'
                                                        f'completed_session={completed},label="{label}"')
            elif '이전 거래일' in label:
                previous, note = previous_session('KRX', kst_day)
                ev.note('calendar ' + note)
                if previous is None:
                    ev.add(NV, 'KRX_FRESHNESS_LABEL', 'CALENDAR_NOT_COVERED:' + note)
                elif previous != day:
                    ev.add(HOLD, 'KRX_FRESHNESS_LABEL', f'PREVIOUS_TRADING_DAY_LABEL_STALE:card={day},'
                                                        f'previous_session={previous},label="{label}"')
            else:
                ev.add(NV, 'KRX_FRESHNESS_LABEL', f'LABEL_UNRECOGNISED:"{label}"')
        elif card.get('market') == 'US' and ev.on('US_CARD_SESSION_DATE'):
            ev.ref('b5_portal_close_cards#US.as_of_date')
            completed, note = last_completed_session('US', at)
            if completed is None:
                ev.add(NV, 'US_CARD_SESSION_DATE', 'CALENDAR_NOT_COVERED:' + note)
            elif day != completed:
                ev.add(HOLD, 'US_CARD_SESSION_DATE', f'{card.get("symbol")}:card={day},completed_session={completed}')
    return ev.result()


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def session_return_pct(previous_close, close):
    """Deterministic one-session return in percent from two closes (Decimal, unrounded)."""
    return (Decimal(str(close)) / Decimal(str(previous_close)) - 1) * 100


def matches_claim(claim, value):
    """True when ``value`` rounded half-up to the claim's decimal places equals the claim."""
    claimed = _decimal(claim)
    if claimed is None:
        return False
    return value.quantize(Decimal(1).scaleb(claimed.as_tuple().exponent), rounding=ROUND_HALF_UP) == claimed


def breadth_members(manifest):
    return sum(int(e.get('record_count') or 0) for e in (manifest or {}).get('endpoints') or [])


def spot_recompute(inputs, rules=None):
    ev = _Eval(SPOT_RECOMPUTE, inputs, rules)
    parsed = inputs['payload']
    cards = _cards(ev, 'US_ETF_SESSION_RETURN') if 'b5_portal_close_cards' in inputs['src'] else None
    if ev.on('US_ETF_SESSION_RETURN'):
        claims = [c for c in cards or [] if c.get('market') == 'US' and c.get('session_return_pct') not in (None, '')]
        fmd = ev.source('US_ETF_SESSION_RETURN', 'b5_free_market_data') if claims else None
        for card in claims:
            symbol, day = card.get('symbol'), _date(card.get('as_of_date'))
            bars = {}
            for bar in _get(fmd, 'alpaca', 'daily_bars') or []:
                if isinstance(bar, dict) and bar.get('symbol') == symbol and _date(bar.get('opened_at')):
                    bars[_date(bar['opened_at'])] = bar.get('close')
            prior, _ = previous_session('US', day) if day else (None, '')
            ev.ref(f'b5_free_market_data#alpaca.daily_bars[{symbol}]', 'b5_portal_close_cards#US.session_return_pct')
            if day not in bars or prior not in bars:
                ev.add(NV, 'US_ETF_SESSION_RETURN', f'{symbol}:BARS_MISSING:as_of={day},prior_session={prior}')
                continue
            value = session_return_pct(bars[prior], bars[day])
            if not matches_claim(card['session_return_pct'], value):
                ev.add(HOLD, 'US_ETF_SESSION_RETURN', f'{symbol}@{day}:claimed={card["session_return_pct"]},'
                                                      f'recomputed={value.quantize(Decimal("0.000001"))}')
            trend = next((e for e in _get(fmd, 'us_market_reference', 'trend_etfs') or []
                          if e.get('symbol') == symbol and _date(e.get('as_of_session_date')) == day), None)
            if trend is not None and _decimal(trend.get('close')) != _decimal(bars[day]):
                ev.add(HOLD, 'US_ETF_SESSION_RETURN', f'{symbol}@{day}:TREND_ETF_CLOSE_DIFFERS_FROM_DAILY_BAR')
    if ev.on('US_BREADTH_MEMBERS'):
        rows = [tokens(line) for line in parsed['components'].get('US_BREADTH_MEMBERSHIP', []) if 'members=' in line]
        manifest = ev.source('US_BREADTH_MEMBERS', 'b5_us_breadth_manifest') if rows else None
        for tok in rows:
            ev.ref('payload#US_BREADTH_MEMBERSHIP.members')
            if manifest is None:
                continue
            if _date(manifest.get('snapshot_date')) != _date(tok.get('snapshot_date')):
                ev.add(NV, 'US_BREADTH_MEMBERS', f'MANIFEST_NOT_PINNED_FOR_DATE:rendered={tok.get("snapshot_date")},'
                                                 f'pinned={manifest.get("snapshot_date")}')
            elif str(breadth_members(manifest)) != tok.get('members'):
                ev.add(HOLD, 'US_BREADTH_MEMBERS', f'claimed={tok.get("members")},recomputed={breadth_members(manifest)}')
    if ev.on('VIXCLS'):
        rows = [tokens(line) for line in parsed['components'].get('FREE_MARKET_DATA', []) if 'VIXCLS=' in line]
        fmd = ev.source('VIXCLS', 'b5_free_market_data') if rows else None
        for tok in rows:
            ev.ref('payload#FREE_MARKET_DATA.VIXCLS', 'b5_free_market_data#fred.value')
            if fmd is None:
                continue
            if _decimal(tok.get('VIXCLS')) != _decimal(_get(fmd, 'fred', 'value')) or \
                    _date(tok.get('as_of')) != _date(_get(fmd, 'fred', 'observation_date')):
                ev.add(HOLD, 'VIXCLS', f'claimed={tok.get("VIXCLS")}@{tok.get("as_of")},'
                                       f'source={_get(fmd, "fred", "value")}@{_get(fmd, "fred", "observation_date")}')
    if ev.on('CRYPTO_RAW_RECOMPUTE'):
        numeric = [name for name in ('BTC_TREND', 'BTC_RISK', 'STABLECOIN_NET_ISSUANCE')
                   if any(re.search(r'=[+-]?\d+\.\d+', line) for line in parsed['components'].get(name, []))]
        if numeric:
            ev.add(NV, 'CRYPTO_RAW_RECOMPUTE', 'RAW_NOT_PINNED_RECOMPUTE_SKIPPED(raw gzip captures are not manifest '
                                               'inputs):' + ','.join(numeric))
    if ev.on('KOREA_INDEX_PCT'):
        claims = _korea_pct_claims(inputs, cards)
        if claims:
            ev.add(NV, 'KOREA_INDEX_PCT', 'RAW_NOT_PERSISTED_NO_RECOMPUTE:' + ','.join(label for label, _ in claims))
    return ev.result()


EVALUATORS = {
    SESSION_RECONCILIATION: session_reconciliation,
    TRADING_CALENDAR: trading_calendar,
    COMPONENT_AS_OF_DATES: component_as_of_dates,
    POINTER_FRESHNESS: pointer_freshness,
    DECISION_RELEVANT_OMISSION: decision_relevant_omission,
    RECOMPUTABILITY: recomputability,
    PORTAL_PARITY: portal_parity,
    SPOT_RECOMPUTE: spot_recompute,
}


def evaluate(check_id, inputs, rules=None):
    if rules is not None and not set(rules) <= set(RULES[check_id]):
        raise ValueError('B5_RULE_UNKNOWN:' + ','.join(sorted(set(rules) - set(RULES[check_id]))))
    try:
        return EVALUATORS[check_id](inputs, rules)
    except (AttributeError, TypeError, KeyError, IndexError, ArithmeticError) as exc:
        # A pinned source with an unexpected shape fails closed as HOLD (never PASS, never a silent skip).
        return {'check_id': check_id, 'status': HOLD, 'reason': f'EVALUATOR_INPUT_SHAPE:{type(exc).__name__}',
                'evidence_refs': []}


def evaluate_all(inputs):
    return [evaluate(check_id, inputs) for check_id in CHECK_IDS]


def model_overstates(model_status, deterministic_status):
    """True when the model reports a status more lenient than the deterministic evaluator found."""
    return STRICTNESS[model_status] < STRICTNESS[deterministic_status]
