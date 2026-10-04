"""Check-in eval — run the AI check-in against the REAL model on a fixed message set.

NOT a CI suite (deliberately not named test_*): it calls the Anthropic API, so
it costs money (roughly $0.25 for a default 3-run pass) and the model's output varies run to run.
CI's stub suites prove the code handles any reply; this proves the *model*
still produces the right ones.

Run it:
  - on every change to build_system_prompt() or CHECKIN_SCHEMA,
  - before changing the check-in model ID (run on old and new, compare),
  - when Anthropic announces a retirement date for the model in use.

Usage:  python3 eval_checkin.py [runs_per_case]     (default 3)
Needs ANTHROPIC_API_KEY in .env. Uses a throwaway SQLite DB; writes nothing real.
Exit code 1 if any case falls below its pass threshold.
"""
import os
import sys
import tempfile

os.environ['DATABASE_URL'] = f'sqlite:///{tempfile.mkdtemp()}/eval.db'
os.environ.setdefault('SECRET_KEY', 'eval-secret')

from datetime import datetime, date

from app import app, parse_checkin, CHECKIN_UNREADABLE_MSG
from database import db, User, Protocol, Symptom, Experiment

# app's load_dotenv(override=True) would let a DATABASE_URL in .env win over the
# throwaway DB above — refuse to seed a real database.
assert 'eval.db' in str(app.config['SQLALCHEMY_DATABASE_URI']), 'eval must run on its throwaway DB'

# The eval user's setup. Covers every symptom/protocol the help.html example
# messages mention, so those examples can be checked as written.
SYMPTOMS = [('Headache pain', 'scale'), ('Nausea', 'binary'),
            ('Fatigue', 'scale'), ('Energy', 'scale')]
PREVENTATIVES = ['Magnesium', 'Riboflavin', 'CoQ10']
RESCUES = ['Ibuprofen', 'Sumatriptan']
IDS = {}   # name -> id, filled in main() after seeding


def ep(p):
    return p.get('episode_data') or {}


def score(p, symptom):
    return ep(p).get('symptom_scores', {}).get(str(IDS[symptom]))


def took(p, protocol):
    """True/False if the reply marked this preventative, None if it didn't."""
    for c in p.get('protocol_compliance') or []:
        if c['id'] == IDS[protocol]:
            return c['took']
    return None


def used(p, name):
    return any(name.lower() in i['name'].lower() for i in ep(p).get('interventions') or [])


def says(p, *words):
    text = (p.get('suggested_response') or '').lower()
    return all(w.lower() in text for w in words)


# (name, message, check(parsed) -> bool). A case passes a run when parse
# succeeded and check returned True; it passes overall at >= THRESHOLD of runs.
CASES = [
    # help.html "The Daily Check-in" examples, verbatim — the guide promises
    # these work, so the eval holds the model to them. Edit together.
    ('help: rough day with intervention',
     'Pretty rough today, headache around a 7, fatigue at 5, took my Sumatriptan around noon and felt better by 3pm',
     lambda p: p['had_episode'] and score(p, 'Headache pain') == 7 and score(p, 'Fatigue') == 5
     and used(p, 'Sumatriptan')),
    ('help: good day, all protocols',
     'Good day, did all my protocols, no symptoms worth noting',
     lambda p: not p['had_episode'] and all(took(p, x) is True for x in PREVENTATIVES)),
    ('help: missed protocol + mild headache',
     'Forgot my magnesium this morning, slight headache in the afternoon around a 3, managed to work through it',
     lambda p: took(p, 'Magnesium') is False
     and (not p['had_episode'] or score(p, 'Headache pain') == 3)),
    ('help: building, can\'t work, nothing taken',
     "Woke up at a 6, been building all morning, now at an 8. Can't work. Haven't taken anything yet",
     lambda p: p['had_episode'] and score(p, 'Headache pain') == 8
     and ep(p)['functional_impairment'] in ('cannot_work', 'completely_incapacitated')
     and not ep(p)['interventions']),
    ('help: protocols taken, no episode',
     'Took my riboflavin and CoQ10 today. No episodes. Energy was decent, maybe a 4',
     lambda p: not p['had_episode'] and took(p, 'Riboflavin') is True and took(p, 'CoQ10') is True),
    ('help: bad flare, two interventions',
     'Bad flare since 2am, nausea at 8, completely out of commission. Used Zofran and ice pack, slightly better after 2 hours',
     lambda p: p['had_episode'] and score(p, 'Nausea') is True
     and ep(p)['functional_impairment'] == 'completely_incapacitated'
     # a new intervention is recorded under its generic name (prompt rule): Zofran -> Ondansetron
     and (used(p, 'Zofran') or used(p, 'Ondansetron')) and used(p, 'ice')),
    ('help: low-grade, functioning fine',
     'Same as yesterday basically — low-grade headache around a 3, functioning fine',
     lambda p: not p['had_episode']
     or (score(p, 'Headache pain') == 3 and ep(p)['functional_impairment'] in (None, 'working_normally'))),
    ('help: nothing to report',
     'good day, nothing to report',
     lambda p: not p['had_episode'] and not p['protocol_compliance']),
    # Logging
    ('scale + binary + intervention + compliance',
     'Bad one, pain 8, nauseous yes, took ibuprofen around noon, helped maybe 4/10. Took my magnesium.',
     lambda p: p['had_episode'] and score(p, 'Headache pain') == 8 and score(p, 'Nausea') is True
     and any(i['name'].lower() == 'ibuprofen' and i['effectiveness'] == 4 for i in ep(p)['interventions'])
     and took(p, 'Magnesium') is True),
    ('missed protocol with reason',
     'Forgot my magnesium, ran out',
     lambda p: took(p, 'Magnesium') is False),
    # Triggers (suggest-and-confirm; the 10/3 double-JSON reproducer)
    ('unlisted trigger kept in user words, not forced onto a global',
     'Migraine came on right after the fireworks show, a 6 — the flashing lights got me',
     lambda p: p['had_episode'] and score(p, 'Headache pain') == 6
     and 'Strong smells' not in ep(p)['triggers']
     and any('light' in t.lower() for t in ep(p)['triggers'])),
    ('listed trigger matched by synonym',
     'Headache a 5 after two glasses of wine last night',
     lambda p: 'Alcohol' in ep(p)['triggers']),
    # Scope: can't-do requests redirect, never promise (10/3 scope fix)
    ('experiment request redirected',
     'Can you add a new experiment for riboflavin 400mg starting today?',
     lambda p: not p['had_episode'] and says(p, 'experiment')
     and not says(p, "i'll set") and not says(p, "i've started") and not says(p, "i've created")),
    ('stop a protocol redirected',
     "Please stop my magnesium, it's not doing anything",
     lambda p: not p['had_episode'] and says(p, 'protocols')),
    ('update to an earlier episode not re-logged',
     'The ibuprofen only helped about 10%, it\'s still going',
     lambda p: not p['had_episode'] and says(p, 'episode')),
    ('mixed: logs the episode AND redirects the experiment',
     'Migraine started around 2pm, a 6. Took ibuprofen. Also can you start an experiment testing riboflavin?',
     lambda p: p['had_episode'] and score(p, 'Headache pain') == 6 and says(p, 'experiment')),
    ('genuine second episode still logged',
     "Another migraine just started, separate from this morning's, a 7",
     lambda p: p['had_episode'] and score(p, 'Headache pain') == 7),
]
THRESHOLD = 1.0   # every run must pass; lower deliberately, per case, only with a reason


def main():
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    failed = []
    with app.test_request_context():
        u = User(email='eval@test.com', is_active=True, verified_at=datetime.utcnow(),
                 onboarding_complete=True, ai_logging_enabled=True)
        u.password_hash = 'x'
        db.session.add(u)
        db.session.commit()
        for name, kind in SYMPTOMS:
            row = Symptom(user_id=u.id, name=name, input_type=kind, is_active=True)
            db.session.add(row)
            db.session.flush()
            IDS[name] = row.id
        for name in PREVENTATIVES:
            row = Protocol(user_id=u.id, name=name, type='preventative', status='active',
                           start_date=date(2026, 9, 1))
            db.session.add(row)
            db.session.flush()
            IDS[name] = row.id
        for name in RESCUES:
            db.session.add(Protocol(user_id=u.id, name=name, type='rescue', available=True))
        db.session.add(Experiment(user_id=u.id, name='Magnesium trial', protocol_id=IDS['Magnesium'],
                                  start_date=date(2026, 9, 1), status='active'))
        db.session.commit()

        for name, message, ok in CASES:
            passes, notes = 0, []
            for _ in range(runs):
                parsed, raw = parse_checkin(u, message)
                if parsed is None:
                    notes.append('UNREADABLE' if raw == CHECKIN_UNREADABLE_MSG else f'ERROR: {raw[:80]}')
                    continue
                try:
                    good = bool(ok(parsed))
                except Exception as e:  # a missing key is a failed run, not a crash
                    good, _ = False, notes.append(f'check raised {type(e).__name__}')
                if good:
                    passes += 1
                else:
                    notes.append((parsed.get('suggested_response') or '')[:100])
            rate = passes / runs
            mark = 'PASS' if rate >= THRESHOLD else 'FAIL'
            print(f'{mark} {passes}/{runs}  {name}')
            for n in notes:
                print(f'        - {n}')
            if mark == 'FAIL':
                failed.append(name)

    print(f'\n{len(CASES) - len(failed)}/{len(CASES)} cases passed ({runs} runs each)')
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
