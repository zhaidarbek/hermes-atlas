import random
import re
import signal
import string
import time
from typing import Callable

import pytest

import scrub_before_75
from atlas.privacy import (_ENVIRONMENT_NAME, _ENVIRONMENT_START, _URL_CREDENTIALS, _VALUE_AHEAD, MASK,
                           _environment_word, scrub)

KEY = "sk-proj-" + "Ab3dE5gH7jK9mN1pQ3sT5vX7zB9dF1hJ"
GITHUB = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
TOKEN = "Zq8Xw2Lp9Rt4Vb7Nm3Kc6Jh1Gf5Ds0Ae8Yu"


@pytest.mark.parametrize("text, masked", [
    (f"use {KEY} for now", "use [secret: api key] for now"),
    (f"push with {GITHUB}", "push with [secret: github token]"),
    ("slack xoxb-1234567890-abcdefghij", "slack [secret: slack token]"),
    ("AKIA" + "ABCDEFGHIJKLMNOP is the id", "[secret: aws key] is the id"),
    ("db postgres://app:hunter2@db.local/main", "db postgres://[secret: password in URL]@db.local/main"),
    (f"token: {TOKEN}", "token: [secret: token]"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nIBAAK\n-----END RSA PRIVATE KEY-----\nthen",
     "[secret: private key]\nthen"),
])
def test_a_detected_secret_is_masked(text, masked):
    assert scrub(text) == masked


AWS_SECRET = "q7Rz2Lk9Wm4Xc8Vn1Bt6" + "Hy3Jd5Fs0Pg2Qa9Ue7Io"
BOT_TOKEN = "7312094856:" + "AAFq9Zt3Lw8Xv2Nc5Bm7Kd1Hj4Gp6Rs0Te"
PEM_LINE = "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtz"


@pytest.mark.parametrize("text, masked", [
    (f"AWS_SECRET_ACCESS_KEY={AWS_SECRET}", "AWS_SECRET_ACCESS_KEY=[secret: token]"),
    (f"TELEGRAM_BOT_TOKEN={BOT_TOKEN}", "TELEGRAM_BOT_TOKEN=[secret: telegram bot token]"),
    ("HF_TOKEN=hf_" + "aB3dE5gH7jK9mN1pQ3sT5vX7zB9dF1hJ2k", "HF_TOKEN=[secret: hugging face token]"),
    ("export OPENAI_KEY=abc123def456ghi", "export OPENAI_KEY=[secret: token]"),
    ("DB_PASSWORD='Kx7mP2vQ9rTz'", "DB_PASSWORD='[secret: password]'"),
    ("curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.e30.abc123'",
     "curl -H 'Authorization: Bearer [secret: token]'"),
    ("password: hunter2!", "password: [secret: password]"),
    ("and the staging db password is Kx7mP2vQ9rTz.", "and the staging db password is [secret: password]."),
    ("stripe sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc", "stripe [secret: api key]"),
    ("gitlab glpat-" + "xY7zQ2wE9rT4uI6oP1aS", "gitlab [secret: gitlab token]"),
    (f"-----BEGIN OPENSSH PRIVATE KEY-----\n{PEM_LINE}\nQyNTUxOQ\nthen I gave up",
     "[secret: private key]\nthen I gave up"),
])
def test_a_pasted_credential_is_masked(text, masked):
    assert scrub(text) == masked


@pytest.mark.parametrize("text", [
    "The elevation profile has spikes on the downhill. Can we smooth it?",
    "token bucket of 32 requests per second",
    "commit 3f9a1c0e8b7d6a5f4e3d2c1b0a9f8e7d6c5b4a39 fixed the password reset page",
    "see https://example.org/docs?page=2",
    "the password is wrong",
    "password: see the vault",
    "MAX_TOKENS=4096 and TOKEN_LIMIT=100",
    "API_KEY=changeme",
])
def test_ordinary_text_is_left_alone(text):
    assert scrub(text) == text


def test_scrubbing_twice_changes_nothing_more():
    once = scrub(f"{KEY} and postgres://a:b@h/x and token={TOKEN} and AWS_SECRET_ACCESS_KEY={AWS_SECRET} "
                 f"and the password is Kx7mP2vQ9rTz")
    assert scrub(once) == once


NAMES = ["password is ", "password: ", "Password was ", "pwd = ", "token: ", "token is ", "secret ", "auth was ",
         "api key: ", "API_KEY=", "HF_TOKEN=", "DB_PASSWORD = ", "Authorization: Bearer ", "https://user:", "sk-proj-"]
CHARACTERS = string.ascii_letters + string.digits + "[]:=-_ .!/@'\""


def text(rng: random.Random) -> str:
    value = "".join(rng.choice(CHARACTERS) for _ in range(rng.randint(1, 40)))
    return rng.choice(NAMES) + value + rng.choice(["", " and token is ", " ok", "\n", " [secret: token] "]) + value[::-1]


def test_scrubbing_twice_changes_nothing_more_on_any_text():
    rng = random.Random(40)
    for _ in range(20_000):
        once = scrub(text(rng))
        assert scrub(once) == once


@pytest.mark.parametrize("text, masked", [
    (f"curl 'https://example.invalid/cb?password=Hunt3r!x9&api_key={KEY}'",
     "curl 'https://example.invalid/cb?password=[secret: password][secret: api key]'"),
    (f"DB_PASSWORD=Hunt3r!x9,OPENAI_API_KEY={KEY}", "DB_PASSWORD=[secret: password][secret: api key]"),
    (f"token={TOKEN}&api_key={KEY}", "token=[secret: token][secret: api key]"),
    ("password is Invented7![secret: token]", "password is [secret: password][secret: token]"),
], ids=["url-query", "env-list", "token-query", "before-a-mask"])
def test_a_secret_written_right_before_a_masked_one_is_masked(text, masked):
    assert scrub(text) == masked


GLUED_NAMES = [("password=", "Hunt3r!x9"), ("password is ", "Invented7!"), ("DB_PASSWORD=", "Kx7mP2vQ9rTz"),
               ("pwd: ", "Hunt3r!x9"), ("token=", TOKEN), ("HF_TOKEN=", "Invented7x9")]


def test_a_secret_is_masked_whatever_detected_secret_follows_it():
    rng = random.Random(41)
    for _ in range(5_000):
        name, value = rng.choice(GLUED_NAMES)
        text = (rng.choice(["", "see ", "curl ?"]) + name + value + rng.choice(["", "&api_key=", ",OPENAI_API_KEY=", "&", "."])
                + rng.choice([KEY, GITHUB, "[secret: token]", "[secret: api key]"]) + rng.choice(["", " ok", "'", "]"]))
        once = scrub(text)
        assert value not in once and scrub(once) == once, text


ATOMS = ["password is ", "DB_PASSWORD=", "token: ", "api key: ", "secret: ", "Authorization: Bearer ", "https://u:",
         "@x ", KEY, TOKEN, "Invented7!", "[secret: token]", "[secret: password]", "[secret:", "[", "]", ":", ".", " ",
         "&api_key=", "="]


def test_scrubbing_twice_changes_nothing_more_on_any_mix_of_names_secrets_and_masks():
    rng = random.Random(42)
    for _ in range(20_000):
        once = scrub("".join(rng.choices(ATOMS, k=rng.randint(1, 10))))
        assert scrub(once) == once


@pytest.mark.parametrize("text, masked", [
    ("see api key: pwd=.Kx7mP2vQ9rTz", "see api key: pwd=[secret: password]"),
    ("the token is see:pwd=Hunt3r!x9", "the token is see:pwd=[secret: password]"),
    ("token: abc.password is Kx7mP2vQ9rTz", "token: abc.password is [secret: password]"),
], ids=["api-key", "token-is", "across-a-space"])
def test_a_name_inside_a_value_left_bare_is_read(text, masked):
    assert scrub(text) == masked


@pytest.mark.parametrize("text, masked", [
    ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.e30.pwd:Kx7mP2vQ9rTz",
     "Authorization: Bearer [secret: token]:[secret: password]"),
    (f"{KEY}-pwd:Kx7mP2vQ9rTz", "[secret: api key]:[secret: password]"),
    (f"{KEY}-pwd:{'a' * 255}7", "[secret: api key]:[secret: password]"),
    ("sk-proj-ABCD1234_EFGH5678_TOKEN=Invented123", "[secret: api key]=[secret: token]"),
    (f"pwd=Hunt3r!x9.token: {TOKEN}", "pwd=[secret: password]: [secret: token]"),
    ("Authorization: Bearer MY_SECRET_TOKEN_X = Invented123x", "Authorization: Bearer [secret: token] = [secret: token]"),
], ids=["bearer", "key", "digit-far-out", "env-name-in-a-key", "named-value", "env-name-is-the-token"])
def test_the_value_of_a_name_a_mask_took_is_masked(text, masked):
    assert scrub(text) == masked


@pytest.mark.parametrize("text", [
    "const options={token:{password:form.value,enabled:true}};",
    "For the auth: password=not-required mode, the service accepts anonymous requests.",
    "const options={auth:{pwd:form.v,ttl:0}};",
    "In auth:password=none,v=1 mode, requests are anonymous.",
], ids=["property-reference", "prose", "numeric-sibling-field", "non-secret-mode"])
def test_code_and_prose_inside_a_bare_value_stay_as_written(text):
    assert scrub(text) == text


def test_a_value_inside_a_bare_one_is_read_to_the_end_of_its_field_and_masked_to_the_end_of_the_run():
    assert scrub("api key: pwd=Hunt3r9,ttl:0") == "api key: pwd=[secret: password]"


def test_a_name_inside_a_glued_value_is_read_as_the_detectors_read_it():
    text = "pwd=-Password was Hunt3r9-TOKEN= &password=abc*def"
    assert scrub(text) == "pwd=[secret: password] was [secret: password] &password=[secret: password]"


def test_a_bare_value_a_mask_cuts_short_is_read_again():
    text = "token: Zq8Xw2Lp9Rt4Vb7Nm.pwd=" + "a" * 40 + "1"
    assert scrub(text) == "token: [secret: token][secret: password]"


@pytest.mark.parametrize("text, masked", [
    ("pwd is pwd is api_key is auth= auth is pwd=reader.value",
     "pwd is pwd is api_key is auth= auth is pwd=reader.value"),
    ("password=is/HF_TOKEN=\nab.PASS = Invented7(x", "password=[secret: password]\nab.PASS = [secret: password]"),
    ("Bearer abcdefghijkl\u066123456789:" + KEY[8:], "Bearer [secret: token][secret: telegram bot token]"),
    (BOT_TOKEN + "\u0661" + BOT_TOKEN[1:] + "\u0661" + BOT_TOKEN[1:],
     "[secret: telegram bot token][secret: telegram bot token]"),
], ids=["a-later-name", "a-mask-takes-an-environment-name", "a-key-after-a-bearer-token", "keys-after-a-key"])
def test_scrubbing_twice_changes_nothing_more_where_a_mask_frees_a_name_or_a_key(text, masked):
    assert scrub(text) == masked
    assert scrub(masked) == masked


def test_a_value_inside_a_bare_one_with_exactly_3_5_bits_per_character_is_masked_as_on_its_own():
    value = "7hc7jabagfccgdibbj7haia2f2e7bdec"
    assert scrub(f"token={value}") == "token=[secret: token]"
    assert scrub(f"token={'a' * 40}.token={value}") == f"token={'a' * 40}.token=[secret: token]"


SHOWN_OR_MASKED = rf"(?:{MASK.pattern}|(?:(?!{MASK.pattern}).)+?)"


def shows_only_what(after: str, before: str) -> bool:
    """`after` is `before` with some of the text `before` shows put behind masks."""
    return re.fullmatch(SHOWN_OR_MASKED.join(map(re.escape, MASK.split(after))), before, re.S) is not None


INVENTED_NAMES = [
    "password is ", "Password was ", "pwd=", "pwd:", ".pwd:", "-pwd=", "&password=", "passphrase: ", "passwd=", "pwd='",
    "token: ", "token=", "Token is ", "?token=", ".token is ", "token : ", '"token": "', 'token="', "api key: ",
    "API key is ", "api_key=", "-api-key:", "access key: ", "secret: ", "secret_key=", ".secret_key=", "auth: ", ".auth=",
    "API_KEY=", "API KEY=", "KEY = ", ".KEY=", "_KEY=", "-TOKEN=", "_TOKEN=", "DB_PASSWORD=", "HF_TOKEN=",
    "AWS_SECRET_ACCESS_KEY=", "GITHUB_TOKEN = ", "MY_PASS=", ".PASS = ", "CREDENTIALS=", "Authorization: Bearer ",
    "authorization: basic ", "Authorization: token ", "bearer ", "https://u:"]
INVENTED_GLUE = ["", "", "", " ", "  ", ".", "-", "_", ":", "=", "&", "/", "?", ",", ";", "'", '"', "\n", "[", "]", "(",
                 ")", "`", "@", "é", "!"]
INVENTED_WORDS = [
    "see", "the", "is", "key", "pass", "auth", "TOKEN", "token", "pwd", "password", "x" * 22, "ordinary_text", "12345678",
    "[secret: token]", "[secret: password]", "[secret: api key]", "[secret:", "https://u:pw9x@host/p", "ftp://a:b@",
    "-----BEGIN RSA PRIVATE KEY-----\n" + "A" * 64 + "\nAB==", "eyJhbGciOiJIUzI1NiJ9", "日本語", "²", "½", "AKIA", "ghp_",
    "sk-"]
ALNUM = string.ascii_letters + string.digits


def invented_secret(rng: random.Random) -> str:
    def body(n: int, more: str = "") -> str:
        return "".join(rng.choices(ALNUM + more, k=n))
    kind = rng.randrange(10)
    if kind == 0:
        return rng.choice(["sk-", "sk-proj-", "sk-ant-api03-"]) + body(rng.randint(20, 40), "_-")
    if kind == 1:
        return "ghp_" + body(36)
    if kind == 2:
        return "xoxb-" + body(24, "-")
    if kind == 3:
        return "AKIA" + "".join(rng.choices(string.ascii_uppercase + string.digits, k=16))
    if kind == 4:
        return "".join(rng.choices(string.digits, k=9)) + ":" + body(35, "_-")
    if kind == 5:
        return "eyJ" + body(rng.randint(10, 40), "._-")
    if kind == 6:
        return body(rng.randint(20, 40))
    if kind == 7:
        return body(rng.randint(3, 6)) + rng.choice("!@#$%^&*") + body(rng.randint(2, 6))
    if kind == 8:
        return "".join(rng.choices(string.ascii_lowercase + string.digits, k=rng.randint(8, 20)))
    return rng.choice("ab") * rng.randint(5, 60) + "1"


def invented_text(rng: random.Random) -> str:
    parts = []
    for _ in range(rng.randint(1, 10)):
        roll = rng.random()
        if roll < 0.5:
            parts.append(rng.choice(INVENTED_NAMES))
            if roll < 0.4:
                parts.append(invented_secret(rng))
        else:
            parts.append(rng.choice(INVENTED_WORDS) if roll < 0.8 else invented_secret(rng))
        parts.append(rng.choice(INVENTED_GLUE))
    return "".join(parts)


def test_every_secret_the_scrub_before_75_masked_stays_masked():
    rng = random.Random(75)
    read_more = 0
    for _ in range(300_000):
        text = invented_text(rng)
        before, after = scrub_before_75.scrub(text), scrub(text)
        if after != before:
            read_more += 1
            assert shows_only_what(after, before), text
        assert scrub(after) == after, text
    assert read_more > 10_000


def nested_text(rng: random.Random) -> str:
    """Names inside values, inside masked keys and around fields, built up from the inside out."""
    text = rng.choice(["form.value", "ordinary", "x" * rng.randint(1, 60), "A4!", "[secret: token]", "v2", "\u0661"])
    for _ in range(rng.randint(1, 8)):
        name = rng.choice(INVENTED_NAMES).lstrip(".-&?_")
        value = rng.choice(["plain", "reader.value", "7", invented_secret(rng)])
        glue = rng.choice([".", ":", ",", ";", "}", ")", "&", "-", "_", " ", "\n", "'", '"'])
        shape = rng.randrange(5)
        if shape == 0:
            text = name + value + glue + text
        elif shape == 1:
            text = name + rng.choice(["{", "(", ""]) + text + glue + value
        elif shape == 2:
            text = text + glue + name + value
        elif shape == 3:
            text = rng.choice(["sk-proj-", "glpat-", "Bearer "]) + invented_secret(rng) + glue + name + text
        else:
            text = name + rng.choice(["", '"', "'"]) + text + rng.choice(["", ".", ",}", ")]", " : ", " is "])
    return text


def test_every_secret_main_masked_stays_masked_and_scrubbing_twice_changes_nothing_on_nested_names():
    rng = random.Random(79)
    read_more = 0
    for _ in range(60_000):
        text = nested_text(rng)
        before, after = scrub_before_75.scrub(text), scrub(text)
        if after != before:
            read_more += 1
            assert shows_only_what(after, before), text
        assert scrub(after) == after, text
    assert read_more > 3_000


FIELD_VALUES = ["form.value", "reader.value", "none", "true", "null", "this.props.form.value", "not-required", "x",
                "{}"]
FIELD_SIBLINGS = ["ttl:0", "v=1", "retry:3", "port:8080", "page=2", "id:42", "count=12345678"]
FIELD_NAMES = ["pwd", "password", "passwd", "token", "auth", "secret", "api_key", "PASSWORD", "KEY", "TOKEN"]


def code_text(rng: random.Random) -> str:
    """Code and prose with names inside a bare value whose own fields hold no digit."""
    def field(depth: int) -> str:
        if depth > 2 or rng.random() < 0.4:
            return rng.choice(FIELD_VALUES)
        return ("{" + rng.choice(FIELD_NAMES) + rng.choice([":", "="]) + field(depth + 1) + rng.choice([",", ";", "&"])
                + rng.choice(FIELD_SIBLINGS) + "}")
    body = (rng.choice(FIELD_NAMES) + rng.choice([":", "="]) + field(0) + rng.choice([",", ";", "&", "}", ")"])
            + rng.choice(FIELD_SIBLINGS))
    return (rng.choice(["const options={", "In ", "cfg = ", ""]) + rng.choice(["auth", "token", "api key", "password"])
            + rng.choice([":", "=", ": "]) + rng.choice(["{", "(", ""]) + body
            + rng.choice(["};", ")", " mode, requests are anonymous.", ""]))


def test_code_and_prose_inside_a_bare_value_stay_as_main_left_them():
    rng = random.Random(131)
    for _ in range(20_000):
        text = code_text(rng)
        assert scrub(text) == scrub_before_75.scrub(text), text


def test_an_environment_name_is_read_where_main_reads_one():
    main = re.compile(_ENVIRONMENT_NAME + _VALUE_AHEAD)
    pieces = ["KEY", "API", "_", "__", "TOKEN", "PASS", "PASSPHRASE", "PWD", "CREDENTIAL", "S", "X", "1", "A1", "=",
              " = ", "\n=", "a", "\u00e9", ".", "-", "'", '"', "[secret: token]", "[", " ", "TOKENS", "APIKEY"]
    rng = random.Random(81)
    for _ in range(50_000):
        text = "".join(rng.choices(pieces, k=rng.randint(1, 12)))
        read = [(m.start(), m.end()) for m in _ENVIRONMENT_START.finditer(text) if _environment_word(m["word"])]
        assert read == [m.span() for m in main.finditer(text)], text


URL_PIECES = ["https://", "ftp://", "a.b+c-d://", "://", "x.", "1.", "-", "+", "_", "_x://", "\u00e9.", "\u0661", "u", ":",
              "user", "u:pw9!@", "u:p:w@", "pw9!@", "pw9!", "@", "host/p", "/", " ", "[", "]", "\n"]


def test_a_url_with_credentials_is_masked_where_the_scrub_before_75_masked_one():
    def mask(m: re.Match) -> str:
        return f"{m['scheme']}[secret: password in URL]@"
    rng = random.Random(81)
    masked = 0
    for _ in range(200_000):
        text = "".join(rng.choices(URL_PIECES, k=rng.randint(1, 14)))
        after = _URL_CREDENTIALS.sub(mask, text)
        assert after == scrub_before_75._URL_CREDENTIALS.sub(mask, text), text
        masked += after != text
    assert masked > 10_000


def fastest(scrubber, text: str) -> float:
    """The fastest of three runs. A run over 5 seconds fails, so a cost that grows with the square of
    the text fails here instead of stalling the suite."""
    def over(*_):
        raise TimeoutError("scrub took over 5 seconds")
    times, before = [], signal.signal(signal.SIGALRM, over)
    try:
        for _ in range(3):
            signal.setitimer(signal.ITIMER_REAL, 5)
            start = time.perf_counter()
            scrubber(text)
            times.append(time.perf_counter() - start)
            signal.setitimer(signal.ITIMER_REAL, 0)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, before)
    return min(times)


def near_3_5_bits(unit: str, name: str) -> Callable[[int], str]:
    """Copies of `unit`, which has exactly 3.5 bits per character, the last with a "1" turned to "0",
    then `name`: each value after a copy's name sits a hair under 3.5 bits."""
    return lambda n: unit * (n // len(unit) - 1) + unit.replace("1", "0", 1) + name


GLUED_LENGTH = 300_000
GLUED = {
    "environment-names": lambda n: "a.KEY=b" * (n // 7),
    "word-names": lambda n: "x.token:y" * (n // 9),
    "names-in-names": lambda n: "token:" * (n // 6) + "ordinary",
    "names-in-names-then-a-password": lambda n: "token:" * (n // 6) + "pwd=Hunt3r!x9",
    "minified-code": lambda n: "function(e){return{auth:e.auth,token:e.token,headers:{Authorization:e.h}}}," * (n // 77),
    "environment-name-glued-to-a-key": lambda n: "sk-proj-" + "KEY_" * (n // 4) + "_KEY=Invented123",
    "near-3.5-bits": near_3_5_bits("token=012345tok=", "token="),
    "near-3.5-bits-another-unit": near_3_5_bits("auth=0a1u2t3456=", "auth="),
    "a-failed-environment-name-in-a-value": lambda n: "KEY=" + "KEY_" * (n // 4) + "ordinary",
    "short-names-then-a-password": lambda n: "auth:" * (n // 5) + "pwd=Sample7!",
    "names-in-names-with-digits": lambda n: "token:1." * (n // 8) + TOKEN,
    "repeated-trailing": lambda n: "auth:pwd:" * (n // 18) + "." * (n // 2),
}
UNITS = ["token:", "pwd=", "KEY=", "KEY_", "API_KEY=", "auth:", "password is ", "is ", "x", "a", "1", "7", "!", ",",
         "}", "{", "&", ";", ":", "=", "_", "Hunt3r!x9", "[secret: token]", "Bearer ", " ", "\u00e9", "\u0661",
         "012345tok=", "\n", '"', KEY]
UNIT_RNG = random.Random(83)
for _ in range(6):
    unit = "".join(UNIT_RNG.choices(UNITS, k=UNIT_RNG.randint(2, 8))) + UNIT_RNG.choice(":=_,;&!{} ")
    GLUED[f"repeated-{unit!r}"] = lambda n, unit=unit: unit * (n // len(unit)) + "pwd=Sample7!"


@pytest.mark.parametrize("name", GLUED)
def test_glued_text_costs_a_small_constant_of_what_it_cost_before_75_and_grows_with_its_length(name):
    large = fastest(scrub, GLUED[name](GLUED_LENGTH))
    assert large < 10 * fastest(scrub_before_75.scrub, GLUED[name](GLUED_LENGTH)) + 0.02
    assert large < 8 * fastest(scrub, GLUED[name](GLUED_LENGTH // 4)) + 0.02


DOTTED = {
    "dotted-run": lambda n: "x." * (n // 2),
    "dotted-run-then-a-scheme": lambda n: "x." * (n // 2) + "://host",
    "dotted-run-then-a-user-and-no-at": lambda n: "x." * (n // 2) + "://u:pw9x",
    "dotted-run-then-credentials": lambda n: "x." * (n // 2) + "://u:pw9x@host",
    "dashed-and-plussed-run": lambda n: "a-b+" * (n // 4) + "://",
    "dotted-path": lambda n: "src.app.module." * (n // 15) + "Main",
    "dotted-runs-each-before-a-scheme": lambda n: "x.x.x.x://" * (n // 10),
}


@pytest.mark.parametrize("name", DOTTED)
def test_a_dotted_run_costs_under_a_second_and_grows_with_its_length(name):
    large = fastest(scrub, DOTTED[name](GLUED_LENGTH))
    assert large < 1
    assert large < 8 * fastest(scrub, DOTTED[name](GLUED_LENGTH // 4)) + 0.02
