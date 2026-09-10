"""
Regression tests for proact_host.inputs -- the {key, pt, nonce, ad} generator
that feeds every capture campaign.

Why these matter: the side-channel validity of a whole dataset rests on two
invariants that fail *silently* if they regress.

  * a variable declared FIXED must be byte-identical on every single run
    (otherwise a fixed-key CPA has no fixed key to recover), and
  * a variable declared RANDOM must actually vary run to run (a silently
    fixed plaintext produces a dataset with zero data-dependent leakage that
    no CPA can ever break -- and nothing anywhere raises an error).

The rest pins `parse_input_file`'s user-facing error strings, which carry the
offending line number and are the only feedback a user gets on a bad file.

Pure stdlib, offline: no board, no serial, no ChipWhisperer, no network.
"""
import pytest

from proact_host import inputs
from proact_host.inputs import VARS, InputPlan, Variable, parse_input_file


# --------------------------------------------------------------------------
# module constants
# --------------------------------------------------------------------------

def test_vars_is_the_four_aead_inputs_in_order():
    """Storage columns and file parsing both key off this tuple's contents."""
    assert VARS == ("key", "pt", "nonce", "ad")


# --------------------------------------------------------------------------
# Variable -- fixed
# --------------------------------------------------------------------------

def test_fixed_variable_returns_the_same_bytes_on_every_call():
    """THE invariant for fixed-key CPA: the key may never drift mid-campaign."""
    v = Variable(random=False, value=b"\x01" * 16)
    first = v.next()
    assert first == b"\x01" * 16
    for _ in range(64):
        assert v.next() == first


def test_fixed_variable_with_no_value_defaults_to_zero_bytes():
    assert Variable(random=False).next() == bytes(16)
    assert Variable(random=False).value == bytes(16)


def test_fixed_variable_zero_default_follows_nbytes():
    assert Variable(random=False, nbytes=8).next() == bytes(8)
    assert Variable(random=False, nbytes=32).next() == bytes(32)


def test_fixed_variable_value_is_used_verbatim_even_if_it_contradicts_nbytes():
    """No padding, no truncation: an explicit value wins over nbytes.

    Pinning this because it is the only place a caller can end up with a
    24-byte 'key' while nbytes still says 16.
    """
    v = Variable(random=False, value=b"\xaa" * 24, nbytes=16)
    assert v.next() == b"\xaa" * 24
    assert v.nbytes == 16


# --------------------------------------------------------------------------
# Variable -- random
# --------------------------------------------------------------------------

def test_variable_is_random_by_default():
    assert Variable().random is True


def test_random_variable_yields_16_bytes_that_differ_across_calls():
    """The other invariant: 'random' must not silently become constant.

    Collision probability for two draws of 16 uniform bytes is 2**-128, so
    this is deterministic in every practical sense.
    """
    v = Variable()
    draws = [v.next() for _ in range(16)]
    assert all(isinstance(d, bytes) and len(d) == 16 for d in draws)
    assert len(set(draws)) == 16


def test_random_variables_differ_across_instances():
    """Two independently constructed random variables must not agree."""
    assert Variable().next() != Variable().next()


@pytest.mark.parametrize("nbytes", [1, 8, 12, 16, 32])
def test_nbytes_is_honoured_for_random_generation(nbytes):
    assert len(Variable(nbytes=nbytes).next()) == nbytes


def test_random_variable_ignores_a_supplied_value():
    """random=True wins: `value` is stored but never returned by next()."""
    v = Variable(random=True, value=b"\x01" * 16)
    assert v.value == b"\x01" * 16
    draws = {v.next() for _ in range(8)}
    assert b"\x01" * 16 not in draws
    assert len(draws) == 8


# --------------------------------------------------------------------------
# InputPlan -- generated from per-variable specs
# --------------------------------------------------------------------------

def test_input_plan_yields_exactly_n_rows_with_exactly_the_four_fields():
    plan = InputPlan(n=3)
    rows = list(plan)
    assert len(rows) == 3
    for row in rows:
        assert set(row) == set(VARS)
        assert all(isinstance(row[v], bytes) for v in VARS)


def test_input_plan_default_variables_are_all_random_16_byte_values():
    rows = list(InputPlan(n=8))
    for name in VARS:
        column = [r[name] for r in rows]
        assert all(len(x) == 16 for x in column)
        assert len(set(column)) == 8, f"{name!r} column did not vary"


def test_fixed_key_random_plaintext_is_the_standard_cpa_setup():
    """The canonical campaign: one constant key column, a varying pt column."""
    key = bytes(range(16))
    variables = {
        "key": Variable(random=False, value=key),
        "pt": Variable(),
        "nonce": Variable(random=False, value=bytes(16)),
        "ad": Variable(random=False, value=b""),
    }
    rows = list(InputPlan(variables=variables, n=32))

    assert len(rows) == 32
    assert {r["key"] for r in rows} == {key}
    assert {r["nonce"] for r in rows} == {bytes(16)}
    assert {r["ad"] for r in rows} == {b""}
    assert len({r["pt"] for r in rows}) == 32


def test_input_plan_regenerates_random_values_on_each_iteration():
    """The plan is a generator factory, not a cached list."""
    plan = InputPlan(n=4)
    assert [r["pt"] for r in plan] != [r["pt"] for r in plan]


def test_input_plan_with_n_zero_yields_nothing():
    assert list(InputPlan(n=0)) == []


def test_empty_variables_dict_falls_back_to_all_random_defaults():
    """`variables or {...}`: an empty mapping is falsy, so defaults kick in."""
    plan = InputPlan(variables={}, n=2)
    assert set(plan.vars) == set(VARS)
    assert all(v.random for v in plan.vars.values())
    assert len(list(plan)) == 2


def test_partial_variables_dict_raises_keyerror_during_iteration():
    """Callers must supply all four variables; a partial dict blows up lazily.

    Pinned rather than 'fixed': the only production caller
    (Software/GUI/proact_gui.py::_build_plan) always builds the full VARS dict.
    """
    plan = InputPlan(variables={"key": Variable(random=False)}, n=1)
    with pytest.raises(KeyError):
        list(plan)


# --------------------------------------------------------------------------
# InputPlan -- replay from a parsed run list
# --------------------------------------------------------------------------

def test_runs_override_n_and_set_the_row_count():
    runs = [{"key": bytes(16)}, {"key": bytes(16)}, {"key": bytes(16)}]
    plan = InputPlan(n=999, runs=runs)
    assert plan.n == 3
    assert len(list(plan)) == 3


def test_runs_are_replayed_verbatim_and_in_order():
    runs = [
        {"key": b"\x00" * 16, "pt": b"\x0a" * 16,
         "nonce": b"\x01" * 16, "ad": b"\xff"},
        {"key": b"\x00" * 16, "pt": b"\x0b" * 16,
         "nonce": b"\x02" * 16, "ad": b"\xee"},
    ]
    rows = list(InputPlan(runs=runs))
    assert rows == [
        {"key": b"\x00" * 16, "pt": b"\x0a" * 16,
         "nonce": b"\x01" * 16, "ad": b"\xff"},
        {"key": b"\x00" * 16, "pt": b"\x0b" * 16,
         "nonce": b"\x02" * 16, "ad": b"\xee"},
    ]


def test_missing_fields_in_a_replayed_row_become_16_zero_bytes():
    """Documented as 'missing fields = 0'; the fill width is a hard 16."""
    rows = list(InputPlan(runs=[{"pt": b"\x07" * 16}]))
    assert rows == [{"key": bytes(16), "pt": b"\x07" * 16,
                     "nonce": bytes(16), "ad": bytes(16)}]


def test_replayed_rows_drop_unknown_extra_fields():
    rows = list(InputPlan(runs=[{"pt": b"\x07" * 16, "bogus": b"\x99"}]))
    assert set(rows[0]) == set(VARS)


def test_replay_is_reiterable_and_stable():
    runs = [{"pt": bytes([i]) * 16} for i in range(5)]
    plan = InputPlan(runs=runs)
    assert list(plan) == list(plan)


def test_runs_take_precedence_over_variable_specs():
    variables = {v: Variable(random=False, value=b"\xcc" * 16) for v in VARS}
    rows = list(InputPlan(variables=variables, n=7, runs=[{"pt": b"\x01" * 16}]))
    assert len(rows) == 1
    assert rows[0]["pt"] == b"\x01" * 16
    assert rows[0]["key"] == bytes(16)  # from the runs fill, not from `variables`


def test_empty_runs_list_yields_no_rows():
    """[] is not None, so it is a (degenerate) replay, not a fallback to n."""
    plan = InputPlan(n=5, runs=[])
    assert plan.n == 0
    assert list(plan) == []


# --------------------------------------------------------------------------
# parse_input_file -- happy path
# --------------------------------------------------------------------------

def _write(tmp_path, text, name="inputs.txt"):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def test_parse_input_file_full_syntax(tmp_path):
    """Comments, blank lines, comma OR whitespace separators, mixed-case
    field names, uppercase hex, and short/empty hex values."""
    path = _write(tmp_path, (
        "# campaign: fixed key, random-ish plaintext\n"
        "\n"
        "key=000102030405060708090a0b0c0d0e0f pt=ff00ff00ff00ff00ff00ff00ff00ff00\n"
        "   \n"
        "KEY=000102030405060708090A0B0C0D0E0F,pt=00112233445566778899aabbccddeeff\n"
        "# trailing comment\n"
        "key=ff Pt=AA, NONCE=00, ad=\n"
    ))
    runs = parse_input_file(path)

    assert len(runs) == 3
    assert runs[0] == {
        "key": bytes(range(16)),
        "pt": bytes.fromhex("ff00ff00ff00ff00ff00ff00ff00ff00"),
    }
    # field names are lower-cased, hex is case-insensitive
    assert runs[1]["key"] == bytes(range(16))
    assert runs[1]["pt"] == bytes.fromhex("00112233445566778899aabbccddeeff")
    # short values are NOT zero-padded to nbytes; empty value -> b""
    assert runs[2] == {"key": b"\xff", "pt": b"\xaa",
                       "nonce": b"\x00", "ad": b""}


def test_parse_input_file_only_lists_the_fields_actually_present(tmp_path):
    path = _write(tmp_path, "pt=%s\n" % ("11" * 16))
    runs = parse_input_file(path)
    assert runs == [{"pt": b"\x11" * 16}]
    # ... and InputPlan is what fills in the absent ones.
    assert list(InputPlan(runs=runs))[0]["key"] == bytes(16)


def test_parse_input_file_round_trips_into_a_replay_plan(tmp_path):
    key = "000102030405060708090a0b0c0d0e0f"
    path = _write(tmp_path, "".join(
        f"key={key} pt={i:02x}{'00' * 15}\n" for i in range(4)
    ))
    rows = list(InputPlan(runs=parse_input_file(path)))
    assert len(rows) == 4
    assert {r["key"] for r in rows} == {bytes(range(16))}
    assert [r["pt"][0] for r in rows] == [0, 1, 2, 3]


# --------------------------------------------------------------------------
# parse_input_file -- errors (user-facing strings)
# --------------------------------------------------------------------------

def test_token_without_equals_names_the_line_and_the_token(tmp_path):
    path = _write(tmp_path, "00112233\n")
    with pytest.raises(ValueError) as exc:
        parse_input_file(path)
    assert str(exc.value) == "line 1: expected key=hex, got '00112233'"


def test_unknown_field_names_the_line_and_the_field(tmp_path):
    path = _write(tmp_path, "keyx=00\n")
    with pytest.raises(ValueError) as exc:
        parse_input_file(path)
    assert str(exc.value) == "line 1: unknown field 'keyx'"


def test_error_line_numbers_count_comment_and_blank_lines(tmp_path):
    """The reported number must match what the user sees in their editor."""
    path = _write(tmp_path, (
        "# header\n"
        "\n"
        "key=%s\n"
        "oops=00\n" % ("00" * 16)
    ))
    with pytest.raises(ValueError) as exc:
        parse_input_file(path)
    assert str(exc.value) == "line 4: unknown field 'oops'"


def test_spaces_around_equals_are_reported_as_a_missing_equals(tmp_path):
    """'key = 00' splits into three tokens; the first has no '='.

    Pinned as-is: a slightly confusing message, but a stable one.
    """
    path = _write(tmp_path, "key = 00\n")
    with pytest.raises(ValueError) as exc:
        parse_input_file(path)
    assert str(exc.value) == "line 1: expected key=hex, got 'key'"


@pytest.mark.parametrize("value", ["zz", "f", "0x00"])
def test_bad_hex_value_reports_line_and_field(tmp_path, value):
    path = _write(tmp_path, "# header\nkey=%s\n" % value)
    with pytest.raises(ValueError, match="line 2: invalid hex for 'key'"):
        parse_input_file(path)


def test_file_with_only_comments_and_blanks_is_rejected(tmp_path):
    path = _write(tmp_path, "# nothing here\n\n   \n# still nothing\n")
    with pytest.raises(ValueError) as exc:
        parse_input_file(path)
    assert str(exc.value) == "no input rows found in %s" % path


def test_completely_empty_file_is_rejected(tmp_path):
    path = _write(tmp_path, "")
    with pytest.raises(ValueError, match="no input rows found"):
        parse_input_file(path)


def test_missing_file_raises_filenotfounderror(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_input_file(str(tmp_path / "does-not-exist.txt"))


def test_module_exposes_the_documented_public_names():
    for name in ("VARS", "Variable", "InputPlan", "parse_input_file"):
        assert hasattr(inputs, name)
