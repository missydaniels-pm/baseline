"""Password reset + signing out other devices — review F1, first slice (10/19/26).

Owner decisions 10/19/26: a reset link signs the user in on that device, and a
reset or a password change signs out every other session (users.session_version,
copied into the cookie at sign-in). Covered:

  1. /forgot-password is enumeration-safe: the same page for a real, unknown,
     unverified or deactivated address; an email goes only to an active,
     verified account.
  2. The reset link: valid → form; tampered → 400; expired → 'expired'; a used
     link (password already changed) → 400; a deactivated account's link → 400.
  3. Setting the password: weak, mismatched or over-72-byte passwords are
     rejected and change nothing; a good one changes the hash, bumps the session
     version, signs this client in, and signs another device out ("Please sign
     in again").
  4. Settings → change password: this device stays in, others are signed out.
  5. Cookies from before session versions (no 'sv') keep working.
  6. The 72-byte rule applies where a password is set (register), never at
     login: a legacy long password still signs in.

Isolated temp DB. Run: python test_password_reset.py
"""
import os
import tempfile

# TEST_DATABASE_URL (CI's Postgres job) wins; otherwise an isolated temp SQLite DB.
os.environ['DATABASE_URL'] = os.environ.get('TEST_DATABASE_URL') or f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.pop('RESEND_API_KEY', None)

from datetime import datetime

import app as app_module
from app import (app, bcrypt, limiter, _pw, generate_reset_token, load_reset_token,
                 PRIVACY_POLICY_VERSION)
from database import db, User

FAILS = []
SENT = []
OLD_PW = 'OldPassw0rd'
NEW_PW = 'NewPassw0rd'


def check(cond, msg):
    print(('PASS: ' if cond else 'FAIL: ') + msg)
    if not cond:
        FAILS.append(msg)


def fake_send(email, url):
    SENT.append((email, url))
    return True


def make_user(email, password=OLD_PW, active=True, verified=True):
    u = User(name='T', email=email, is_active=active,
             verified_at=datetime.utcnow() if verified else None,
             onboarding_complete=True, has_seen_tour=True,
             privacy_policy_version=PRIVACY_POLICY_VERSION, privacy_consent_at=datetime.utcnow())
    u.password_hash = bcrypt.generate_password_hash(_pw(password)).decode('utf-8')
    db.session.add(u)
    db.session.commit()
    return u.id


def login(c, email, password):
    return c.post('/login', data={'email': email, 'password': password})


def reload(uid):
    db.session.expire_all()
    return db.session.get(User, uid)


def main():
    app_module.send_password_reset_email = fake_send
    with app.app_context():
        limiter.reset()
        uid = make_user('me@test.com')
        make_user('unverified@test.com', active=False, verified=False)
        make_user('off@test.com', active=False)

        # ── 1. Forgot password: same page, email only for a real active account ──
        anon = app.test_client()
        check(anon.get('/forgot-password').status_code == 200, '[forgot] page renders signed out')
        pages = {}
        for addr in ('me@test.com', 'nobody@test.com', 'unverified@test.com', 'off@test.com'):
            r = anon.post('/forgot-password', data={'email': addr}, follow_redirects=True)
            pages[addr] = r.get_data(as_text=True)
            check(r.status_code == 200 and 'If an account exists for that address' in pages[addr],
                  f'[forgot] {addr} → generic confirmation')
        check(len(set(pages.values())) == 1, '[forgot] identical page for every address (same response body)')
        r = anon.post('/forgot-password', data={'email': 'nobody@test.com'})
        check(r.status_code == 302 and 'sent=1' in r.headers['Location'], '[forgot] redirect-after-POST (a reload can\'t resend)')
        SENT[:] = [s for s in SENT if s[0] == 'me@test.com'][:1]
        check([e for e, _ in SENT] == ['me@test.com'], f'[forgot] email sent only to the active account (got {SENT})')
        reset_path = SENT[0][1].split('://', 1)[1].split('/', 1)[1]
        reset_path = '/' + reset_path

        # ── 2. The link ──
        r = anon.get(reset_path)
        check(r.status_code == 200 and b'Choose a new password' in r.data, '[link] valid link → form')
        check(b'autocomplete="username"' in r.data and b'value="me@test.com"' in r.data,
              '[link] the form names the account for password managers')
        r = anon.get(reset_path[:-3] + 'xyz')
        check(r.status_code == 400 and b"doesn't work" in r.data, '[link] tampered link → 400')
        tok = reset_path.rsplit('/', 1)[1]
        _, err = load_reset_token(tok, max_age=-1)
        check(err == 'expired', '[link] past its max age → expired')
        from app import generate_verify_token
        r = anon.get(f'/reset-password/{generate_verify_token("me@test.com")}')
        check(r.status_code == 400, '[link] a verification token is not a reset token')
        mover = User.query.get(make_user('mover@test.com'))
        moved_link = generate_reset_token(mover)
        mover.email = 'moved@test.com'
        db.session.commit()
        check(anon.get(f'/reset-password/{moved_link}').status_code == 400,
              '[link] a link dies when the account\'s email changes')
        off = User.query.filter_by(email='off@test.com').first()
        r = anon.get(f'/reset-password/{generate_reset_token(off)}')
        check(r.status_code == 400, '[link] a deactivated account\'s link → 400')

        # ── 3. Setting the password ──
        other = app.test_client()                       # "another device", signed in first
        check(login(other, 'me@test.com', OLD_PW).status_code == 302, '[setup] other device signs in')
        check(other.get('/').status_code == 200, '[setup] other device reaches the dashboard')
        before = reload(uid)
        old_hash, old_sv = before.password_hash, before.session_version

        for label, pw, confirm in (('weak', 'short', 'short'),
                                   ('mismatch', NEW_PW, NEW_PW + 'x'),
                                   ('over 72 bytes', 'Aa1' + 'é' * 40, 'Aa1' + 'é' * 40)):
            r = anon.post(reset_path, data={'password': pw, 'confirm_password': confirm})
            check(r.status_code == 400 and reload(uid).password_hash == old_hash,
                  f'[set] {label} password rejected, nothing changed')

        r = anon.post(reset_path, data={'password': NEW_PW, 'confirm_password': NEW_PW})
        after = reload(uid)
        check(r.status_code == 302 and r.headers['Location'].endswith('/'), '[set] good password → dashboard')
        check(bcrypt.check_password_hash(after.password_hash, _pw(NEW_PW)), '[set] new password stored (bcrypt)')
        check(after.session_version == old_sv + 1, '[set] session version bumped')
        check(anon.get('/').status_code == 200, '[set] this client is signed in')
        r = other.get('/')
        check(r.status_code == 302 and r.headers['Location'].endswith('/login'), '[set] the other device is signed out')
        r = other.get('/login')
        check(b'signed out because this account' in r.data, '[set] and is told the password was changed')
        r = app.test_client().get(reset_path)
        check(r.status_code == 400, '[set] the used link no longer works')
        r = app.test_client().post(reset_path, data={'password': 'An0therPass', 'confirm_password': 'An0therPass'})
        check(r.status_code == 400 and bcrypt.check_password_hash(reload(uid).password_hash, _pw(NEW_PW)),
              '[set] a second POST of the same link is refused')
        check(login(app.test_client(), 'me@test.com', OLD_PW).status_code == 200, '[set] old password rejected')
        check(login(app.test_client(), 'me@test.com', NEW_PW).status_code == 302, '[set] new password signs in')

        # ── 4. Change password in Settings ──
        here, there = app.test_client(), app.test_client()
        login(here, 'me@test.com', NEW_PW)
        login(there, 'me@test.com', NEW_PW)
        r = here.post('/settings/password', data={'current_password': NEW_PW,
                                                         'new_password': 'Th1rdPassword',
                                                         'confirm_new_password': 'Th1rdPassword'})
        check(r.status_code == 302 and here.get('/').status_code == 200, '[change] this device stays signed in')
        check(there.get('/').status_code == 302, '[change] the other device is signed out')
        r = here.post('/settings/password', data={'current_password': 'Th1rdPassword',
                                                         'new_password': 'Aa1' + 'é' * 40,
                                                         'confirm_new_password': 'Aa1' + 'é' * 40})
        check(bcrypt.check_password_hash(reload(uid).password_hash, _pw('Th1rdPassword')),
              '[change] an over-72-byte new password is rejected')

        # ── 5. Cookies from before session versions ──
        legacy_id = make_user('legacy@test.com')
        c = app.test_client()
        with c.session_transaction() as sess:
            sess['user_id'] = legacy_id                   # no 'sv'
        check(c.get('/').status_code == 200, '[legacy] a cookie without a session version still works')

        # ── 6. 72 bytes: rejected when set, never at login ──
        limiter.reset()
        long_pw = 'Aa1' + 'x' * 97                         # 100 bytes
        r = app.test_client().post('/register', data={'email': 'long@test.com', 'password': long_pw,
                                                       'confirm_password': long_pw, 'privacy_ack': 'on'})
        check(r.status_code == 200 and b'too long' in r.data and not User.query.filter_by(email='long@test.com').first(),
              '[72] register rejects a password over 72 bytes')
        make_user('oldlong@test.com', password=long_pw)
        check(login(app.test_client(), 'oldlong@test.com', long_pw).status_code == 302,
              '[72] a legacy long password still signs in')

        # ── 7. Show-password toggle is on every page with a password field ──
        from app import JS_VERSION
        tag = f'js/password-toggle.js?v={JS_VERSION}'.encode()
        anon = app.test_client()
        for path in ('/login', '/register', f'/reset-password/{generate_reset_token(reload(uid))}'):
            check(tag in anon.get(path).data, f'[toggle] {path.split("/")[1]} loads the show-password script')
        signed = app.test_client()
        login(signed, 'me@test.com', 'Th1rdPassword')
        check(tag in signed.get('/settings').data, '[toggle] settings loads it (via base.html)')
        r = anon.get(f'/static/js/password-toggle.js?v={JS_VERSION}')
        check(r.status_code == 200 and 'javascript' in r.headers.get('Content-Type', ''),
              '[toggle] the script is served as JavaScript')
        r.close()

    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL PASSWORD RESET TESTS PASSED')


if __name__ == '__main__':
    main()
