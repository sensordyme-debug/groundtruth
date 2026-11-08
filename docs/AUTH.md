# API authentication and ownership

Code: `api/auth.py` (middleware, login routes, token store) · ownership checks in `api/investigations_api.py`,
`api/sessions.py`, `api/app.py` (exports) · tests: `tests/test_auth.py`.

## What is protected
Every `/api/...` route needs an authenticated principal, except:
- `GET /api/v1/health` (public, no data), and
- `/api/v1/auth/*` (login / logout / me).

Static assets and the HTML shell are public; the app shows a sign-in screen (`web/app-v2/src/Login.tsx`) when
`/api/v1/auth/me` answers 401, or when any API call does. Being on the tailnet is **not** authentication.

Credentials accepted:
- `Authorization: Bearer <token>` (scripts, curl).
- The `gt_session` cookie, issued by `POST /api/v1/auth/login {"token": "..."}`. It is HttpOnly, SameSite=Strict,
  Secure on https, valid for 12 h, and held in server memory, so an API restart signs everyone out.

Missing, malformed or unknown credentials → `401 {"detail": "authentication required"}` with `WWW-Authenticate:
Bearer`. Logins are rate-limited to 10 failures per 5 min per client IP (`429`). Tokens are compared in constant time
and never logged; only their SHA-256 is stored.

## Ownership
The owner of sessions, investigations and exports is derived **on the server** from the principal
(`owner_id(user)` = `u-` + a hash of the user name). Any `X-GT-Owner` header the client sends is overwritten by the
middleware, so it can't be forged. Reading, cancelling, getting evidence for, giving feedback on, or getting the report of
an investigation owned by someone else gives `404`, the same as a missing one (no IDOR, no existence oracle).
Investigations saved before auth existed have no owner and are not readable while auth is on. A follow-up
(`context.previous`) that points at another user's investigation is dropped. Exports are recorded in
`data/app/v1/export_owners.jsonl` and served only to their owner.

## Configuration (environment; nothing in the frontend bundle)
| Variable | Default | Meaning |
|---|---|---|
| `GROUNDTRUTH_AUTH` | `on` | `off` = explicit local development mode: no login, the browser's `X-GT-Owner` is trusted. Never use it on a reachable host |
| `GROUNDTRUTH_USERS_FILE` | `data/app/v1/users.json` | `{"users": {name: {"token_sha256": ...}}}`, mode 600 |
| `GROUNDTRUTH_API_TOKEN` | unset | optional extra token (≥ 24 chars) for user `owner`, e.g. from a secret store |

`scripts/api_branch.sh` currently sets `GROUNDTRUTH_AUTH=off` unless the variable is already set, so local checks keep
running during development. **Set `GROUNDTRUTH_AUTH=on` (or remove that line) before exposing the API.** Local check
scripts authenticate through `scripts/_gt_auth.py` (`GROUNDTRUTH_API_TOKEN`, else `data/app/v1/admin_token`).

## First run, adding users, recovery
- **First start with auth on and no users:** the API creates user `owner` and writes its token to
  `data/app/v1/admin_token` (mode 600). The log line gives the file path only, never the token. Read it with
  `cat` in a private terminal, sign in, then store it in a password manager and delete the file.
- **Add a user or rotate a token:**
  `python -c "from api import auth; print(auth.add_user('alice'))"` prints the new token once. Running it again for the
  same name replaces the token, which is how you rotate.
- **Lost every token:** stop the API, move `users.json` aside, and start again. A new `owner` token is bootstrapped.
  Investigations stay owned by the old user's id; a user with the same name gets the same owner id back.
- **Suspected leak:** rotate the token (above) and restart the API to drop every in-memory session.
