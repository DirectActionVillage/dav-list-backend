#!/usr/bin/env python3
# Managed by Ansible. Do not edit by hand;
# change the role file.
"""New Trajectories grant application handler.

Serves one page and accepts one form. Every submission is encrypted to a set
of age public keys before it touches a disk. This process doesn't hold a
private key, so it can write an application and can never read one back.

Configuration is entirely by environment variable, and this file doesn't hold
an address, hostname, container id or site name of its own. That is deliberate:
the user-facing half of this handler is published to a public repository, so
designing for export beats redacting after the fact.

Standard library only. The one external program is `age`, invoked as a
subprocess to encrypt; nothing else is shelled out to.

Run `grant_form_handler.py --self-test` for the offline checks. It contacts
no network and writes nothing outside a temporary directory.
"""

import argparse
import html
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- the form ---------------------------------------------------------------

# Every free-text field, in the order the page presents them. The tuple is the
# single source of truth: the placeholder set, the payload, and the self-test
# all derive from it, so adding a field here is one edit.
TEXT_FIELDS = (
    "name",
    "email",
    "phone",
    "signal",
    "project",
    "why",
    "budget",
    "resources",
)

# The only required answers. Name, email, phone and Signal username are all
# optional on purpose: an applicant decides how much of themselves to hand
# over, and the committee reads a project rather than an identity.
REQUIRED_FIELDS = (
    ("project", "Please tell us what your project is."),
    ("why", "Please tell us why you care about it."),
    ("budget", "Please give us a rough budget."),
)

# The ways an applicant can offer to be reached. NONE of them is required, and
# nothing here refuses an application without a contact detail.
# The public data notice promises it: an applicant may leave out every
# contact detail, and then we just can't tell them if they win. The form warns
# beside these fields; the handler accepts.
CONTACT_FIELDS = ("email", "phone", "signal")

MSG_BAD_EMAIL = (
    "That email address doesn't look right. Check it, or leave it blank and "
    "give us another way to reach you."
)
MSG_TOO_LONG = (
    "One of your answers is too long for this form. Please shorten it and send "
    "it again."
)
MSG_HONEYPOT = "One hidden field on this form has to stay empty. Clear it and send the form again."
MSG_TOKEN = (
    "This form timed out, or it was already sent. Your answers are still here, "
    "so just send it again."
)
MSG_TOO_FAST = (
    "That came in faster than a person can fill this out. Wait a moment and "
    "send it again."
)
MSG_BUSY = "This form is busy right now. Please try again in an hour."

# Lenient on purpose. An address only has to hold an @ and no whitespace. A
# stricter rule argues with an applicant about their own address, and this form
# is read by a person who can spot a typo.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")

# An age X25519 recipient is bech32: the literal age1 plus 58 characters.
AGE_RECIPIENT_RE = re.compile(r"^age1[0-9a-z]{58}$")

# The page files hold only these. A placeholder the handler does
# not know about is a hard error, so a page edit cannot ship a literal
# @@SOMETHING@@ to a reader.
PLACEHOLDER_RE = re.compile(r"@@[A-Z0-9_]+@@")
PAGE_PLACEHOLDERS = frozenset(
    ["@@TOKEN@@", "@@NOTICE@@", "@@SUBSCRIBE_CHECKED@@"]
    + ["@@V_" + name.upper() + "@@" for name in TEXT_FIELDS]
)

TOKEN_FIELD = "grant_form_token"
SUBSCRIBE_FIELD = "subscribe"

# --- configuration ----------------------------------------------------------

ENV_DEFAULTS = {
    "GRANT_FORM_LISTEN_ADDRESS": "127.0.0.1",
    "GRANT_FORM_LISTEN_PORT": "8081",
    "GRANT_FORM_PATH": "/grants/apply",
    "GRANT_FORM_AGE_BIN": "age",
    "GRANT_FORM_MIN_FILL_SECONDS": "20",
    "GRANT_FORM_TOKEN_TTL_SECONDS": "21600",
    "GRANT_FORM_MAX_TOKENS": "5000",
    # The body cap has to clear the field caps. validate() refuses an over-long
    # answer with a message beside the form, every answer still filled in. A
    # body over this cap comes back as a bare 413 that loses the application.
    # _self_test_caps() derives the bound and fails when this pair stops
    # satisfying it.
    "GRANT_FORM_MAX_BODY_BYTES": "1048576",
    "GRANT_FORM_MAX_FIELD_CHARS": "8000",
    "GRANT_FORM_RATE_LIMIT_COUNT": "20",
    "GRANT_FORM_RATE_LIMIT_WINDOW_SECONDS": "3600",
    "GRANT_FORM_HONEYPOT_FIELD": "leave_blank",
    "GRANT_FORM_NOTIFY_URL": "",
    "GRANT_FORM_NOTIFY_TIMEOUT": "5",
    # The page a maintainer opens from the notification. Empty leaves the link out.
    "GRANT_FORM_NOTIFY_RUNBOOK_URL": "",
    "GRANT_FORM_ENCRYPT_TIMEOUT": "30",
}

REQUIRED_ENV = (
    "GRANT_FORM_PAGE",
    "GRANT_FORM_THANKS_PAGE",
    "GRANT_FORM_SPOOL_DIR",
    "GRANT_FORM_RECIPIENTS_FILE",
)


class ConfigError(Exception):
    """A refusal to start. Every one of these is fatal by design."""


class StoreError(Exception):
    """The application could not be encrypted or written."""


def _env(name):
    value = os.environ.get(name)
    if value is None or value == "":
        return ENV_DEFAULTS.get(name, "")
    return value


def _env_int(name):
    raw = _env(name)
    try:
        return int(raw)
    except ValueError:
        raise ConfigError("%s must be a whole number, got %r" % (name, raw))


class Config:
    """Every knob, read once at startup from the environment."""

    def __init__(self):
        missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
        if missing:
            raise ConfigError(
                "these environment variables are required and unset: %s"
                % ", ".join(missing)
            )
        self.listen_address = _env("GRANT_FORM_LISTEN_ADDRESS")
        self.listen_port = _env_int("GRANT_FORM_LISTEN_PORT")
        self.path = _env("GRANT_FORM_PATH")
        self.page_file = os.environ["GRANT_FORM_PAGE"]
        self.thanks_file = os.environ["GRANT_FORM_THANKS_PAGE"]
        self.spool_dir = os.environ["GRANT_FORM_SPOOL_DIR"]
        self.recipients_file = os.environ["GRANT_FORM_RECIPIENTS_FILE"]
        self.age_bin = _env("GRANT_FORM_AGE_BIN")
        self.min_fill_seconds = _env_int("GRANT_FORM_MIN_FILL_SECONDS")
        self.token_ttl_seconds = _env_int("GRANT_FORM_TOKEN_TTL_SECONDS")
        self.max_tokens = _env_int("GRANT_FORM_MAX_TOKENS")
        self.max_body_bytes = _env_int("GRANT_FORM_MAX_BODY_BYTES")
        self.max_field_chars = _env_int("GRANT_FORM_MAX_FIELD_CHARS")
        self.rate_limit_count = _env_int("GRANT_FORM_RATE_LIMIT_COUNT")
        self.rate_limit_window = _env_int("GRANT_FORM_RATE_LIMIT_WINDOW_SECONDS")
        self.honeypot_field = _env("GRANT_FORM_HONEYPOT_FIELD")
        self.notify_url = _env("GRANT_FORM_NOTIFY_URL")
        self.notify_timeout = _env_int("GRANT_FORM_NOTIFY_TIMEOUT")
        self.notify_runbook_url = _env("GRANT_FORM_NOTIFY_RUNBOOK_URL")
        self.encrypt_timeout = _env_int("GRANT_FORM_ENCRYPT_TIMEOUT")
        if not self.path.startswith("/"):
            raise ConfigError("GRANT_FORM_PATH must start with a slash")


def log(message):
    """One line to stderr, which systemd puts in the journal.

    Nothing an applicant typed is ever passed to this. The journal is not
    encrypted, so a logged answer would defeat the whole design.
    """
    sys.stderr.write(
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + message + "\n"
    )
    sys.stderr.flush()


# --- recipients -------------------------------------------------------------


def load_recipients(path):
    """Return the declared age recipients, or raise.

    The refusal is the point. Without a recipient there is nothing to encrypt to.
    A handler that started anyway would either drop applications on the floor
    or rest them in plaintext.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ConfigError("cannot read the age recipients file %s: %s" % (path, exc))
    lines = [line.strip() for line in raw.splitlines()]
    keys = [line for line in lines if line and not line.startswith("#")]
    if not keys:
        raise ConfigError(
            "no age recipient is declared in %s, so this handler refuses to "
            "start. Declare the maintainers' age PUBLIC keys in the role "
            "and apply again." % path
        )
    bad = [key for key in keys if not AGE_RECIPIENT_RE.match(key)]
    if bad:
        raise ConfigError(
            "%d line(s) in %s are not age public keys. An age public key reads "
            "age1 followed by 58 characters." % (len(bad), path)
        )
    return keys


def age_argv(age_bin, recipients_file, out_path):
    """The exact argv used to encrypt.

    Write-only by construction: it doesn't pass --identity or --decrypt, so no code path
    in this process can turn a stored application back into plaintext. The
    self-test asserts that, because it is the property the whole design rests
    on.
    """
    return [
        age_bin,
        "--encrypt",
        "--armor",
        "--recipients-file",
        recipients_file,
        "--output",
        out_path,
    ]


# --- request plumbing -------------------------------------------------------


def clean_value(raw):
    """Normalize one submitted value.

    Line endings collapse to LF and control characters go. So what the
    committee decrypts is the text a person typed, and nothing a terminal will
    act on. Nothing here truncates: an over-long answer is an
    error the applicant can see and fix, never a silent trim.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    keep = []
    for char in text:
        if char in ("\n", "\t"):
            keep.append(char)
        elif ord(char) >= 32 and ord(char) != 127:
            keep.append(char)
    return "".join(keep).strip()


def parse_form(body, honeypot_field):
    """Turn a urlencoded body into the field dict the rest of the code uses."""
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    parsed = urllib.parse.parse_qs(
        body, keep_blank_values=True, encoding="utf-8", errors="replace"
    )

    def first(name):
        values = parsed.get(name) or [""]
        return clean_value(values[0])

    fields = {name: first(name) for name in TEXT_FIELDS}
    fields[TOKEN_FIELD] = first(TOKEN_FIELD)
    fields[honeypot_field] = first(honeypot_field)
    fields[SUBSCRIBE_FIELD] = bool(parsed.get(SUBSCRIBE_FIELD))
    return fields


def validate(fields, honeypot_field, max_field_chars):
    """Return the list of problems to show the applicant, in reading order.

    It strips before it judges, and does not trust clean_value() to have run.
    So a caller that skips parsing cannot let a space through as an answer.
    """
    problems = []
    if str(fields.get(honeypot_field) or "").strip():
        problems.append(MSG_HONEYPOT)
    for name, message in REQUIRED_FIELDS:
        if not fields.get(name, "").strip():
            problems.append(message)
    # No contact-detail rule sits here, and none may be added. An application
    # with every contact field blank is accepted, because the public data
    # notice says it is. The form states the consequence beside the fields.
    email = fields.get("email", "")
    if email and not EMAIL_RE.match(email):
        problems.append(MSG_BAD_EMAIL)
    if any(len(fields.get(name, "")) > max_field_chars for name in TEXT_FIELDS):
        problems.append(MSG_TOO_LONG)
    return problems


class TokenStore:
    """Single-use form tokens, held in memory.

    A token is minted on the GET that serves the form and consumed by the POST
    that returns it. Without JavaScript anywhere, a submission then has to
    have fetched the page. It cannot be replayed. And the gap between issue
    and use is a fill time we can put a floor under.

    A restart drops every outstanding token. The cost is one extra click. An
    unknown token re-renders the form with every answer still in it and a
    fresh token, so nothing an applicant typed is lost.
    """

    def __init__(self, ttl_seconds, maximum):
        self._ttl = ttl_seconds
        self._max = maximum
        self._issued = {}
        self._lock = threading.Lock()

    def _prune(self, now):
        cutoff = now - self._ttl
        for token in [t for t, at in self._issued.items() if at < cutoff]:
            del self._issued[token]

    def issue(self, now=None):
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(18)
        with self._lock:
            self._prune(now)
            while len(self._issued) >= self._max:
                oldest = min(self._issued, key=self._issued.get)
                del self._issued[oldest]
            self._issued[token] = now
        return token

    def consume(self, token, now=None):
        """Return the issue time and forget the token, or None."""
        now = time.time() if now is None else now
        with self._lock:
            self._prune(now)
            return self._issued.pop(token, None)


class RateLimiter:
    """A flood cap on submissions, counted across everyone.

    Per-source counting is not available here: every request arrives from the
    same reverse proxy, so its address doesn't identify who sent it. This is
    therefore a cap on how fast the spool can fill, not a per-actor control.
    The limit is set well above what a real application round produces. The
    refusal tells the applicant to come back, and never drops their work.
    """

    def __init__(self, count, window_seconds):
        self._count = count
        self._window = window_seconds
        self._hits = deque()
        self._lock = threading.Lock()

    def allow(self, now=None):
        now = time.time() if now is None else now
        with self._lock:
            while self._hits and self._hits[0] < now - self._window:
                self._hits.popleft()
            if len(self._hits) >= self._count:
                return False
            self._hits.append(now)
            return True


# --- rendering --------------------------------------------------------------


def notice_html(problems):
    """The block at the top of the form, or nothing when all is well."""
    if not problems:
        return ""
    items = "".join(
        "<li>" + html.escape(problem) + "</li>" for problem in problems
    )
    return (
        '<div class="notice" role="alert"><p>We couldn\'t take this yet.</p>'
        "<ul>" + items + "</ul></div>"
    )


def render(page, mapping):
    """Substitute every placeholder in one pass.

    One pass matters: an applicant may type @@V_EMAIL@@ into the name box, and
    a second pass would treat that echoed text as a placeholder.
    """

    def replace(match):
        key = match.group(0)
        if key not in mapping:
            raise KeyError(key)
        return mapping[key]

    return PLACEHOLDER_RE.sub(replace, page)


def render_form(page, token, fields, problems):
    mapping = {
        "@@TOKEN@@": html.escape(token, quote=True),
        "@@NOTICE@@": notice_html(problems),
        # The template puts a space before this marker, so both the unsubstituted
        # template and the served page parse as valid HTML. Substituting a
        # leading space instead would leave `value="yes"@@...@@` in the file.
        # A validator would flag that parse error on the template itself.
        "@@SUBSCRIBE_CHECKED@@": "checked" if fields.get(SUBSCRIBE_FIELD) else "",
    }
    for name in TEXT_FIELDS:
        mapping["@@V_" + name.upper() + "@@"] = html.escape(
            fields.get(name, ""), quote=True
        )
    return render(page, mapping)


def page_placeholders(text):
    return frozenset(PLACEHOLDER_RE.findall(text))


def read_page(path, expected):
    """Load a page file and refuse anything the handler cannot fill in."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise ConfigError("cannot read the page file %s: %s" % (path, exc))
    found = page_placeholders(text)
    unknown = found - expected
    if unknown:
        raise ConfigError(
            "%s carries placeholders this handler does not fill: %s"
            % (path, ", ".join(sorted(unknown)))
        )
    missing = expected - found
    if missing:
        raise ConfigError(
            "%s is missing placeholders this handler needs: %s"
            % (path, ", ".join(sorted(missing)))
        )
    return text


# --- storage ----------------------------------------------------------------


def build_payload(fields):
    """What a maintainer decrypts. Nothing derived, nothing inferred."""
    return {
        "form": "new-trajectories-grant-application",
        "schema": 1,
        "received_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "answers": {name: fields.get(name, "") for name in TEXT_FIELDS},
        "mailing_list_opt_in": bool(fields.get(SUBSCRIBE_FIELD)),
    }


def store_application(config, payload):
    """Encrypt the payload to the declared recipients and land it in the spool.

    The plaintext exists only as bytes on this process's stdin pipe. It is
    never written to a file, a temporary directory, or the journal.
    """
    os.makedirs(config.spool_dir, mode=0o700, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    name = "application-%s-%s.age" % (stamp, secrets.token_hex(4))
    final = os.path.join(config.spool_dir, name)
    partial = final + ".partial"
    argv = age_argv(config.age_bin, config.recipients_file, partial)
    plaintext = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)
    try:
        completed = subprocess.run(
            argv,
            input=plaintext.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=config.encrypt_timeout,
        )
    except OSError as exc:
        raise StoreError("could not run %s: %s" % (config.age_bin, exc))
    except subprocess.TimeoutExpired:
        _unlink(partial)
        raise StoreError("age did not finish within the encrypt timeout")
    if completed.returncode != 0:
        _unlink(partial)
        raise StoreError(
            "age exited %d: %s"
            % (completed.returncode, completed.stderr.decode("utf-8", "replace").strip())
        )
    os.chmod(partial, 0o600)
    _fsync_path(partial)
    os.replace(partial, final)
    _fsync_dir(config.spool_dir)
    return name


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _fsync_path(path):
    handle = os.open(path, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def _fsync_dir(path):
    handle = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


# The notification access token. systemd copies it into the unit as this credential
# (LoadCredential=), so the service user reads a private copy while the
# root-only source file stays closed to it. Without it the POST is anonymous.
NOTIFY_TOKEN_CREDENTIAL = "notify-token"


def notify_token():
    """The notification token from the unit's credential directory, or ""."""
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if not directory:
        return ""
    try:
        with open(
            os.path.join(directory, NOTIFY_TOKEN_CREDENTIAL), encoding="utf-8"
        ) as handle:
            return handle.readline().strip()
    except OSError:
        return ""


def notify(config, filename):
    """Tell the maintainers something arrived, and tell it nothing else.

    More people read the topic than the maintainers, so the body holds the
    spool file name and no applicant data at all. A failed notification is
    logged and never fails the submission: the application is already durable
    by the time this runs.
    """
    if not config.notify_url:
        return
    text = (
        "A New Trajectories grant application arrived. It is encrypted in the "
        "spool as " + filename + "."
    )
    runbook = getattr(config, "notify_runbook_url", "")
    if runbook:
        text += " What to do: " + runbook
    request = urllib.request.Request(
        config.notify_url, data=text.encode("utf-8"), method="POST"
    )
    request.add_header("Title", "New grant application")
    if runbook:
        request.add_header("Click", runbook)
    token = notify_token()
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, timeout=config.notify_timeout) as response:
            response.read()
    except Exception as exc:  # noqa: BLE001 - a notify failure must not raise
        log("notify failed: %s" % exc)


# --- the server -------------------------------------------------------------


class GrantFormHandler(BaseHTTPRequestHandler):
    server_version = "grant-form"
    sys_version = ""
    protocol_version = "HTTP/1.0"
    timeout = 30

    # Set by serve() before the server starts.
    config = None
    form_page = ""
    thanks_page = ""
    tokens = None
    limiter = None

    def log_message(self, fmt, *args):
        """Deliberately silent.

        The default logs the request line, which includes the query string, and
        an access log on this path would record who applied and when. The
        outcome of each request is logged by the handlers below instead.
        """

    def _accepted(self, path):
        return path in (self.config.path, self.config.path + "/")

    def _send(self, status, body, headers=()):
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        # img-src data: lets the inlined logo show. It doesn't allow a fetch.
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; img-src data:")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)
        self.wfile.flush()

    def _plain(self, status, message):
        self._send(status, "<!doctype html><title>%s</title><p>%s</p>" % (status, message))

    def do_GET(self):  # noqa: N802 - the base class names it
        split = urllib.parse.urlsplit(self.path)
        if not self._accepted(split.path):
            self._plain(HTTPStatus.NOT_FOUND, "Not found.")
            return
        if "submitted=1" in split.query:
            self._send(HTTPStatus.OK, self.thanks_page)
            return
        blank = {name: "" for name in TEXT_FIELDS}
        self._send(HTTPStatus.OK, render_form(self.form_page, self.tokens.issue(), blank, []))

    def do_POST(self):  # noqa: N802 - the base class names it
        split = urllib.parse.urlsplit(self.path)
        if not self._accepted(split.path):
            self._plain(HTTPStatus.NOT_FOUND, "Not found.")
            return
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if content_type != "application/x-www-form-urlencoded":
            self._plain(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "This form posts urlencoded fields.")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._plain(HTTPStatus.LENGTH_REQUIRED, "A length is required.")
            return
        if length < 0 or length > self.config.max_body_bytes:
            self._plain(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, MSG_TOO_LONG)
            return
        if not self.limiter.allow():
            log("submission refused: rate cap")
            self._plain(HTTPStatus.TOO_MANY_REQUESTS, MSG_BUSY)
            return
        body = self.rfile.read(length)
        fields = parse_form(body, self.config.honeypot_field)

        problems = []
        issued_at = self.tokens.consume(fields.get(TOKEN_FIELD, ""))
        if issued_at is None:
            problems.append(MSG_TOKEN)
        elif time.time() - issued_at < self.config.min_fill_seconds:
            problems.append(MSG_TOO_FAST)
        problems.extend(
            validate(fields, self.config.honeypot_field, self.config.max_field_chars)
        )

        if problems:
            log("submission returned to the applicant with %d problem(s)" % len(problems))
            page = render_form(self.form_page, self.tokens.issue(), fields, problems)
            self._send(HTTPStatus.OK, page)
            return

        try:
            name = store_application(self.config, build_payload(fields))
        except StoreError as exc:
            log("submission FAILED to store: %s" % exc)
            self._plain(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "We could not store your application. Nothing was saved. Please "
                "try again, and tell us if it keeps failing.",
            )
            return
        log("submission stored as %s" % name)
        self._send(
            HTTPStatus.SEE_OTHER,
            "<!doctype html><title>Thank you</title><p>Thank you.</p>",
            headers=(("Location", self.config.path + "?submitted=1"),),
        )
        notify(self.config, name)


def serve():
    """Start up, or refuse loudly. Nothing here is best-effort."""
    os.umask(0o077)
    config = Config()
    recipients = load_recipients(config.recipients_file)
    GrantFormHandler.config = config
    GrantFormHandler.form_page = read_page(config.page_file, PAGE_PLACEHOLDERS)
    GrantFormHandler.thanks_page = read_page(config.thanks_file, frozenset())
    GrantFormHandler.tokens = TokenStore(config.token_ttl_seconds, config.max_tokens)
    GrantFormHandler.limiter = RateLimiter(
        config.rate_limit_count, config.rate_limit_window
    )
    os.makedirs(config.spool_dir, mode=0o700, exist_ok=True)
    server = ThreadingHTTPServer(
        (config.listen_address, config.listen_port), GrantFormHandler
    )
    server.daemon_threads = True
    log(
        "listening on %s:%d for %s, encrypting to %d recipient(s)"
        % (config.listen_address, config.listen_port, config.path, len(recipients))
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


# --- self-test --------------------------------------------------------------


class _Checks:
    def __init__(self):
        self.failures = []
        self.skipped = []

    def ok(self, condition, label):
        if condition:
            print("PASS  " + label)
        else:
            print("FAIL  " + label)
            self.failures.append(label)

    def skip(self, label, why):
        print("SKIP  %s (%s)" % (label, why))
        self.skipped.append(label)


def _fields(**overrides):
    base = {name: "" for name in TEXT_FIELDS}
    base[TOKEN_FIELD] = ""
    base[SUBSCRIBE_FIELD] = False
    base.update(overrides)
    return base


def _complete_project_answers():
    return {"project": "Build a tool library.", "why": "People share tools.", "budget": "About 400 dollars."}


def _self_test_parsing(check):
    honeypot = "leave_blank"
    body = (
        "name=Ada&email=ada%40example.org&phone=&signal=&project=A+tool+library"
        "&why=It+helps&budget=400&resources=&" + honeypot + "=&"
        + TOKEN_FIELD + "=abc&subscribe=yes"
    )
    parsed = parse_form(body, honeypot)
    check.ok(parsed["name"] == "Ada", "parse: a plain value survives")
    check.ok(parsed["email"] == "ada@example.org", "parse: percent-encoding decodes")
    check.ok(parsed["project"] == "A tool library", "parse: plus signs become spaces")
    check.ok(parsed["phone"] == "", "parse: a blank field is kept, not dropped")
    check.ok(parsed[TOKEN_FIELD] == "abc", "parse: the token field is read")
    check.ok(parsed[SUBSCRIBE_FIELD] is True, "parse: the opt-in checkbox reads true")
    check.ok(
        parse_form("subscribe=&name=x", honeypot)[SUBSCRIBE_FIELD] is True,
        "parse: a present checkbox counts however it is valued",
    )
    check.ok(
        parse_form("name=x", honeypot)[SUBSCRIBE_FIELD] is False,
        "parse: an absent checkbox reads false",
    )
    check.ok(
        parse_form("name=%C3%A1%C3%A9", honeypot)["name"] == "áé",
        "parse: UTF-8 percent-encoding decodes",
    )
    check.ok(
        parse_form("project=a%00b%07c", honeypot)["project"] == "abc",
        "parse: control characters are stripped",
    )
    check.ok(
        parse_form("project=a%0D%0Ab", honeypot)["project"] == "a\nb",
        "parse: CRLF collapses to LF",
    )
    check.ok(
        parse_form("name=++Ada++", honeypot)["name"] == "Ada",
        "parse: surrounding whitespace is trimmed",
    )


def _self_test_required(check):
    honeypot = "leave_blank"
    good = _fields(email="ada@example.org", **_complete_project_answers())
    check.ok(validate(good, honeypot, 8000) == [], "required: a complete application passes")
    for name, message in REQUIRED_FIELDS:
        missing = dict(good)
        missing[name] = ""
        problems = validate(missing, honeypot, 8000)
        check.ok(message in problems, "required: a blank %s is refused" % name)
    for name in ("name", "phone", "signal", "resources"):
        optional = dict(good)
        optional[name] = ""
        check.ok(
            validate(optional, honeypot, 8000) == [],
            "required: %s stays optional" % name,
        )
    whitespace = dict(good)
    whitespace["project"] = "   "
    check.ok(
        REQUIRED_FIELDS[0][1] in validate(whitespace, honeypot, 8000),
        "required: whitespace does not answer a required question",
    )


def _self_test_contact(check):
    """The published promise: an application without a contact detail is accepted.

    This is the case most likely to be "fixed" back into a rule by someone who
    reads the form and thinks it is a bug. So it is asserted from three angles.
    """
    honeypot = "leave_blank"
    none_given = _fields(**_complete_project_answers())
    check.ok(
        validate(none_given, honeypot, 8000) == [],
        "contact: an application with no contact detail at all is ACCEPTED",
    )
    for name, value in (
        ("email", "ada@example.org"),
        ("phone", "207 555 0100"),
        ("signal", "ada.42"),
    ):
        one = _fields(**_complete_project_answers())
        one[name] = value
        check.ok(
            validate(one, honeypot, 8000) == [],
            "contact: %s alone is accepted" % name,
        )
    every = _fields(
        email="ada@example.org",
        phone="207 555 0100",
        signal="ada.42",
        **_complete_project_answers()
    )
    check.ok(
        validate(every, honeypot, 8000) == [],
        "contact: all three together are accepted",
    )
    payload = build_payload(none_given)
    check.ok(
        all(payload["answers"][name] == "" for name in CONTACT_FIELDS),
        "contact: a contact-free application still reaches the stored payload",
    )


def _self_test_other_rules(check):
    honeypot = "leave_blank"
    base = _fields(email="ada@example.org", **_complete_project_answers())
    trapped = dict(base)
    trapped[honeypot] = "http://spam.example"
    check.ok(
        MSG_HONEYPOT in validate(trapped, honeypot, 8000),
        "abuse: a filled honeypot is refused",
    )
    check.ok(
        MSG_HONEYPOT not in validate(base, honeypot, 8000),
        "abuse: an empty honeypot passes",
    )
    bad_email = dict(base)
    bad_email["email"] = "not an address"
    check.ok(
        MSG_BAD_EMAIL in validate(bad_email, honeypot, 8000),
        "email: whitespace is refused",
    )
    no_at = dict(base)
    no_at["email"] = "adaexample.org"
    check.ok(MSG_BAD_EMAIL in validate(no_at, honeypot, 8000), "email: a missing @ is refused")
    odd = dict(base)
    odd["email"] = "ada+grants@sub.example.museum"
    check.ok(
        MSG_BAD_EMAIL not in validate(odd, honeypot, 8000),
        "email: an unusual but valid address is accepted",
    )
    long_answer = dict(base)
    long_answer["why"] = "x" * 8001
    check.ok(
        MSG_TOO_LONG in validate(long_answer, honeypot, 8000),
        "length: an over-long answer is refused",
    )


def _self_test_caps(check):
    """The body cap must never be what refuses an application the fields accept.

    The two refusals are not equal. validate() returns an over-long answer
    beside the form with everything the applicant typed still in it. The body
    cap returns a bare 413 and loses the lot. So any body the field caps
    would accept has to fit under the body cap. That is a property of the two
    numbers rather than of a running server.
    """
    max_chars = int(ENV_DEFAULTS["GRANT_FORM_MAX_FIELD_CHARS"])
    max_body = int(ENV_DEFAULTS["GRANT_FORM_MAX_BODY_BYTES"])
    # Worst case on the wire: every counted character is a four-byte UTF-8
    # codepoint. Every one of those bytes percent-encodes to three
    # characters. The product is derived from TEXT_FIELDS, so adding a field
    # moves the bound on its own.
    worst = len(TEXT_FIELDS) * max_chars * 4 * 3
    check.ok(
        max_body >= worst,
        "caps: the body cap clears the worst-case encoding of every field at its cap",
    )
    # The same claim checked empirically, through the encoding a form post
    # actually uses, with a real four-byte codepoint in every field.
    filled = {name: "\U0001f600" * max_chars for name in TEXT_FIELDS}
    filled[TOKEN_FIELD] = "0" * 64
    filled[ENV_DEFAULTS["GRANT_FORM_HONEYPOT_FIELD"]] = ""
    body = urllib.parse.urlencode(filled).encode("utf-8")
    check.ok(
        len(body) <= max_body,
        "caps: a maximum-length application encodes to a body under the cap",
    )


def _self_test_recipients(check):
    valid = "age1" + ("q" * 58)
    with tempfile.TemporaryDirectory() as tmp:
        missing = os.path.join(tmp, "absent.txt")
        check.ok(
            _raises(ConfigError, load_recipients, missing),
            "recipients: a missing file refuses startup",
        )
        empty = os.path.join(tmp, "empty.txt")
        open(empty, "w", encoding="utf-8").close()
        check.ok(
            _raises(ConfigError, load_recipients, empty),
            "recipients: an empty file refuses startup",
        )
        comments = os.path.join(tmp, "comments.txt")
        with open(comments, "w", encoding="utf-8") as handle:
            handle.write("# nobody yet\n\n")
        check.ok(
            _raises(ConfigError, load_recipients, comments),
            "recipients: a comments-only file refuses startup",
        )
        junk = os.path.join(tmp, "junk.txt")
        with open(junk, "w", encoding="utf-8") as handle:
            handle.write("ssh-ed25519 AAAAC3Nz\n")
        check.ok(
            _raises(ConfigError, load_recipients, junk),
            "recipients: a line that is not an age public key refuses startup",
        )
        good = os.path.join(tmp, "good.txt")
        with open(good, "w", encoding="utf-8") as handle:
            handle.write("# the grant committee\n" + valid + "\n")
        check.ok(
            load_recipients(good) == [valid],
            "recipients: a declared age public key is accepted",
        )


def _self_test_argv(check):
    argv = age_argv("/usr/bin/age", "/etc/recipients.txt", "/spool/out.partial")
    check.ok("--encrypt" in argv, "argv: encrypts")
    check.ok("--armor" in argv, "argv: armors, so the file is text the committee can move")
    check.ok("--recipients-file" in argv, "argv: encrypts to the declared recipients file")
    forbidden = {"-i", "--identity", "-d", "--decrypt"}
    check.ok(
        not forbidden.intersection(argv),
        "argv: carries no identity or decrypt flag, so this process cannot read an application back",
    )


def _self_test_render(check):
    page = (
        "<form>@@NOTICE@@<input name=t value=\"@@TOKEN@@\">"
        "<input name=name value=\"@@V_NAME@@\">"
        "<textarea>@@V_PROJECT@@</textarea>"
        # The space before the marker mirrors the shipped template. Keep it:
        # without it the template itself is not valid HTML. A validator flags
        # that on the file, even though the served page would be fine.
        "<input type=checkbox @@SUBSCRIBE_CHECKED@@>"
        + "".join(
            "@@V_" + f.upper() + "@@"
            for f in TEXT_FIELDS
            if f not in ("name", "project")
        )
        + "</form>"
    )
    hostile = _fields(name='<script>alert("x")</script>', project="@@V_EMAIL@@", email="ada@example.org")
    out = render_form(page, "tok&1", hostile, [MSG_TOO_LONG])
    check.ok("<script>" not in out, "render: a script tag in a value is escaped")
    check.ok("&lt;script&gt;" in out, "render: the escaped form is what lands")
    check.ok('value="tok&amp;1"' in out, "render: the token is attribute-escaped")
    check.ok(
        out.count("ada@example.org") == 1,
        "render: an echoed placeholder is not substituted a second time",
    )
    check.ok(html.escape(MSG_TOO_LONG) in out, "render: the notice block carries the problem")
    check.ok(
        not PLACEHOLDER_RE.search(render_form(page, "t", _fields(), [])),
        "render: a blank form leaves no placeholder behind",
    )
    check.ok(
        "type=checkbox >" in render_form(page, "t", _fields(), []),
        "render: the opt-in checkbox is unchecked by default",
    )
    check.ok(
        "type=checkbox checked>" in render_form(page, "t", _fields(subscribe=True), []),
        "render: a ticked opt-in renders checked",
    )
    check.ok(
        _raises(KeyError, render, "@@NOT_A_PLACEHOLDER@@", {}),
        "render: an unknown placeholder is a hard error, never silent output",
    )


def _self_test_tokens(check):
    store = TokenStore(ttl_seconds=100, maximum=10)
    token = store.issue(now=1000.0)
    check.ok(store.consume(token, now=1010.0) == 1000.0, "token: a fresh token is accepted")
    check.ok(store.consume(token, now=1011.0) is None, "token: a token is single use")
    check.ok(store.consume("never-issued", now=1011.0) is None, "token: an unknown token is refused")
    stale = store.issue(now=1000.0)
    check.ok(store.consume(stale, now=1200.0) is None, "token: an expired token is refused")
    small = TokenStore(ttl_seconds=1000, maximum=3)
    kept = [small.issue(now=1000.0 + n) for n in range(5)]
    check.ok(small.consume(kept[0], now=1010.0) is None, "token: the store is bounded")
    check.ok(small.consume(kept[4], now=1010.0) is not None, "token: the newest tokens survive the bound")


def _self_test_rate(check):
    limiter = RateLimiter(count=3, window_seconds=60)
    check.ok(
        all(limiter.allow(now=1000.0 + n) for n in range(3)),
        "rate: submissions under the cap pass",
    )
    check.ok(not limiter.allow(now=1003.0), "rate: the submission over the cap is refused")
    check.ok(limiter.allow(now=1100.0), "rate: the window rolls forward")


def _self_test_pages(check, page_path, thanks_path):
    if page_path:
        with open(page_path, "r", encoding="utf-8") as handle:
            found = page_placeholders(handle.read())
        check.ok(
            found == PAGE_PLACEHOLDERS,
            "page: %s carries exactly the placeholders the handler fills" % page_path,
        )
    else:
        check.skip("page placeholder check", "no --page given")
    if thanks_path:
        with open(thanks_path, "r", encoding="utf-8") as handle:
            found = page_placeholders(handle.read())
        check.ok(
            found == frozenset(),
            "page: %s carries no placeholders" % thanks_path,
        )
    else:
        check.skip("thanks-page placeholder check", "no --thanks given")


def _self_test_roundtrip(check):
    """Prove the encryption path on a box that has age, with a throwaway key.

    The committee's own keys are never involved and no application is written.
    It proves that age is installed and that age_argv() is a working command
    line. It also proves that what the handler writes decrypts to what it was
    given. CI doesn't have age, so this case reports as skipped there.
    """
    age_bin = _env("GRANT_FORM_AGE_BIN")
    keygen = os.environ.get("GRANT_FORM_AGE_KEYGEN_BIN", "age-keygen")
    with tempfile.TemporaryDirectory() as tmp:
        key_file = os.path.join(tmp, "throwaway.key")
        try:
            made = subprocess.run(
                [keygen, "-o", key_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
        except OSError as exc:
            check.skip("encryption round trip", "age-keygen is not available: %s" % exc)
            return
        if made.returncode != 0:
            check.skip("encryption round trip", "age-keygen failed")
            return
        public = ""
        for line in made.stderr.decode("utf-8", "replace").splitlines():
            match = re.search(r"(age1[0-9a-z]{58})", line)
            if match:
                public = match.group(1)
        if not public:
            check.skip("encryption round trip", "age-keygen printed no public key")
            return
        recipients = os.path.join(tmp, "recipients.txt")
        with open(recipients, "w", encoding="utf-8") as handle:
            handle.write("# throwaway, self-test only\n" + public + "\n")
        check.ok(
            load_recipients(recipients) == [public],
            "round trip: the generated public key passes the recipient check",
        )
        spool = os.path.join(tmp, "spool")
        config = _FakeConfig(age_bin, recipients, spool)
        payload = build_payload(_fields(name="Ada", project="p", why="w", budget="b"))
        try:
            name = store_application(config, payload)
        except StoreError as exc:
            check.skip("encryption round trip", "age is not available: %s" % exc)
            return
        stored = os.path.join(spool, name)
        with open(stored, "r", encoding="utf-8") as handle:
            armored = handle.read()
        check.ok(
            armored.startswith("-----BEGIN AGE ENCRYPTED FILE-----"),
            "round trip: the stored file is an armored age file",
        )
        check.ok("Ada" not in armored, "round trip: no answer appears in the stored bytes")
        check.ok(
            oct(os.stat(stored).st_mode & 0o777) == oct(0o600),
            "round trip: the stored file is 0600",
        )
        back = subprocess.run(
            [age_bin, "--decrypt", "--identity", key_file, stored],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        check.ok(back.returncode == 0, "round trip: the committee key decrypts the file")
        if back.returncode == 0:
            check.ok(
                json.loads(back.stdout.decode("utf-8"))["answers"]["name"] == "Ada",
                "round trip: the decrypted payload holds the submitted answers",
            )


def _self_test_notify(check):
    """Prove notify() sends the credential token, and only when it exists.

    urlopen is swapped for a stub that records the request, so nothing leaves
    this process.
    """
    sent = []

    class _Reply:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b""

    def _record(request, timeout=None):
        sent.append(request)
        return _Reply()

    class _NotifyConfig:
        notify_url = "http://127.0.0.1:9/topic"
        notify_timeout = 1

    real_urlopen = urllib.request.urlopen
    saved_dir = os.environ.pop("CREDENTIALS_DIRECTORY", None)
    urllib.request.urlopen = _record
    try:
        notify(_NotifyConfig, "application-x.age")
        check.ok(
            sent and sent[-1].get_header("Authorization") is None,
            "notify: without a credential the POST carries no Authorization",
        )
        with tempfile.TemporaryDirectory() as tmp:
            with open(
                os.path.join(tmp, NOTIFY_TOKEN_CREDENTIAL), "w", encoding="utf-8"
            ) as handle:
                handle.write("tk_fixture\n")
            os.environ["CREDENTIALS_DIRECTORY"] = tmp
            notify(_NotifyConfig, "application-y.age")
        check.ok(
            len(sent) == 2
            and sent[-1].get_header("Authorization") == "Bearer tk_fixture",
            "notify: the credential token goes out as a Bearer header",
        )
        check.ok(
            sent[-1].get_header("Title") == "New grant application"
            and b"application-y.age" in sent[-1].data,
            "notify: the title and the spool file name still arrive",
        )
        check.ok(
            sent[-1].get_header("Click") is None,
            "notify: without a runbook URL the POST carries no Click link",
        )
        _NotifyConfig.notify_runbook_url = "https://wiki.example/runbook"
        notify(_NotifyConfig, "application-z.age")
        check.ok(
            sent[-1].get_header("Click") == "https://wiki.example/runbook"
            and b"https://wiki.example/runbook" in sent[-1].data,
            "notify: the runbook URL goes out as the Click link and in the body",
        )
    finally:
        urllib.request.urlopen = real_urlopen
        os.environ.pop("CREDENTIALS_DIRECTORY", None)
        if saved_dir is not None:
            os.environ["CREDENTIALS_DIRECTORY"] = saved_dir


class _FakeConfig:
    """The three attributes store_application() reads. Self-test only."""

    def __init__(self, age_bin, recipients_file, spool_dir):
        self.age_bin = age_bin
        self.recipients_file = recipients_file
        self.spool_dir = spool_dir
        self.encrypt_timeout = 30


def _raises(exception, function, *args):
    try:
        function(*args)
    except exception:
        return True
    except Exception:  # noqa: BLE001 - the wrong exception is still a failure
        return False
    return False


def self_test(page_path=None, thanks_path=None):
    check = _Checks()
    _self_test_parsing(check)
    _self_test_required(check)
    _self_test_contact(check)
    _self_test_other_rules(check)
    _self_test_caps(check)
    _self_test_recipients(check)
    _self_test_argv(check)
    _self_test_render(check)
    _self_test_tokens(check)
    _self_test_rate(check)
    _self_test_pages(check, page_path, thanks_path)
    _self_test_roundtrip(check)
    _self_test_notify(check)
    print("")
    if check.failures:
        print("SELF-TEST FAILED: %d check(s)" % len(check.failures))
        return 1
    print("SELF-TEST PASSED (%d skipped)" % len(check.skipped))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run the offline checks and exit; contacts no network",
    )
    parser.add_argument("--page", help="form page to check placeholders against")
    parser.add_argument("--thanks", help="thank-you page to check placeholders against")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test(args.page, args.thanks)
    try:
        return serve()
    except ConfigError as exc:
        log("REFUSING TO START: %s" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
