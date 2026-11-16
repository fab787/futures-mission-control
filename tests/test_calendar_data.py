"""Tests for the pure calendar/earnings helpers (Stage 2a).

Fixtures are trimmed copies of real TradingView responses captured on
2026-11-15. No network access and no provider modules are involved.
"""
from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from mission_control import calendar_data as cal

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_FILE = ROOT / "config" / "earnings_watchlist.yaml"
FILTERS_FILE = ROOT / "config" / "macro_event_filters.yaml"


# ---------------------------------------------------------------- fixtures

def econ_row(**overrides):
    row = {
        "actual": None,
        "category": "cnsm",
        "comment": "A long explanatory paragraph that must not be stored.",
        "country": "US",
        "currency": "USD",
        "date": "2026-11-17T13:30:00.000Z",
        "forecast": 0.2,
        "forecastRaw": 0.2,
        "id": "1001661350203",
        "importance": 1,
        "indicator": "Retail Sales MM",
        "period": "Oct. 2026",
        "previous": 0.8,
        "previousRaw": 0.8,
        "source": "Census Bureau",
        "ticker": "ECONOMICS:USRSMM",
        "title": "Retail Sales MoM",
        "unit": "%",
    }
    row.update(overrides)
    return row


RETAIL = econ_row()
HOUSING = econ_row(id="1001661345001", date="2026-11-18T13:30:00.000Z",
                   indicator="Housing Starts Number", title="Housing Starts",
                   forecast=1.268, previous=1.221, unit=None, scale="M")
PCE = econ_row(id="1001653914707", date="2026-11-25T13:30:00.000Z",
               indicator="Core PCE Price Index MM *", title="Core PCE Price Index MoM",
               forecast=None, previous=0.3)
GDP = econ_row(id="1001661639103", date="2026-11-25T13:30:00.000Z",
               indicator="GDP 2nd Estimate", title="GDP Growth Rate QoQ 2nd Est",
               forecast=None, previous=1.5, period="Q3 2026")
JOLTS = econ_row(id="1001668385001", date="2026-12-01T15:00:00.000Z",
                 indicator="JOLTS Job Openings", title="JOLTs Job Openings",
                 comment="The survey collects data from nonfarm establishments.",
                 forecast=None, previous=7.523, unit=None, scale="M")
ISM_SERVICES = econ_row(id="1001650998403", date="2026-12-03T15:00:00.000Z",
                        indicator="ISM N-Mfg PMI", title="ISM Services PMI",
                        forecast=None, previous=54.5, unit=None)
ISM_SERVICES.pop("ticker")  # the real ISM Services row carries no ticker
NFP = econ_row(id="1001626876806", date="2026-12-04T13:30:00.000Z",
               indicator="Non-Farm Payrolls", title="Non Farm Payrolls",
               forecast=None, previous=165, unit=None, scale="K")
MICHIGAN = econ_row(id="1001652127105", date="2026-12-04T15:00:00.000Z",
                    indicator="U Mich Sentiment Prelim",
                    title="Michigan Consumer Sentiment Prel",
                    forecast=None, previous=51, unit=None)
EXISTING_HOME = econ_row(id="1001658836001", date="2026-12-09T15:00:00.000Z",
                         indicator="Existing Home Sales", title="Existing Home Sales",
                         forecast=None, previous=3.75, unit=None, scale="M")
FOMC = econ_row(id="1001609056301", date="2026-12-09T19:00:00.000Z",
                indicator="FOMC Decision Fed Funds Tgt Rate", title="Interest Rate",
                forecast=None, previous=None, period="Dec. 9, 2026")
CPI = econ_row(id="1001660738503", date="2026-12-10T13:30:00.000Z",
               indicator="CPI MM, SA", title="Inflation Rate MoM",
               forecast=None, previous=0.4)

ALL_ECON_ROWS = [RETAIL, HOUSING, PCE, GDP, JOLTS, ISM_SERVICES, NFP,
                 MICHIGAN, EXISTING_HOME, FOMC, CPI]
ECON_PAYLOAD = {"result": ALL_ECON_ROWS, "status": "ok"}


def earn_row(**overrides):
    row = {
        "close": 162.85,
        "description": "NVIDIA Corporation",
        "eps_forecast_next_fq": 2.474056,
        "fiscal_period_end": "2026-10-31",
        "name": "NVDA",
        "next_date_confirmed": True,
        "release_date": "2026-08-26",
        "release_next_at": "2026-11-17T21:00:00Z",
        "release_next_date": "2026-11-17",
        "release_next_time_flag": 1,
        "revenue_forecast_next_fq": 108932722899,
        "symbol": "NASDAQ:NVDA",
    }
    row.update(overrides)
    return row


AVGO_ROW = earn_row(description="Broadcom Inc.", name="AVGO", symbol="NASDAQ:AVGO",
                    release_date="2026-09-02", release_next_at="2026-12-09T21:15:00Z",
                    release_next_date="2026-12-09", eps_forecast_next_fq=3.830142,
                    revenue_forecast_next_fq=34896656465)
MU_ROW = earn_row(description="Micron Technology, Inc.", name="MU", symbol="NASDAQ:MU",
                  release_date="2026-09-30", release_next_at="2026-12-23T12:00:00Z",
                  release_next_date="2026-12-23", next_date_confirmed=False,
                  release_next_time_flag=0, fiscal_period_end="2026-11-30",
                  eps_forecast_next_fq=38.230603, revenue_forecast_next_fq=61721179524)
OUT_OF_WINDOW = [
    {"release_date": "2026-11-02", "release_next_date": "2027-02-04", "symbol": "NASDAQ:AAPL"},
    {"release_date": "2026-10-28", "release_next_date": "2027-01-26", "symbol": "NASDAQ:MSFT"},
    {"release_date": "2026-10-29", "release_next_date": "2027-02-04", "symbol": "NASDAQ:AMZN"},
    {"release_date": "2026-10-28", "release_next_date": "2027-02-02", "symbol": "NASDAQ:GOOGL"},
    {"release_date": "2026-10-21", "release_next_date": "2027-02-03", "symbol": "NASDAQ:META"},
    {"release_date": "2026-10-21", "release_next_date": "2027-02-03", "symbol": "NASDAQ:TSLA"},
    {"release_date": "2026-10-13", "release_next_date": "2027-01-14", "symbol": "NYSE:JPM"},
]
EARN_PAYLOAD = {
    "data": {
        "count": 3,
        "earnings": [earn_row(), AVGO_ROW, MU_ROW],
        "from": "2026-11-15",
        "to": "2026-12-31",
        "hint": "The earnings calendar holds only two dates per symbol.",
        "out_of_window": OUT_OF_WINDOW,
    },
    "success": True,
}

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
TEN_SYMBOLS = {
    "NASDAQ:NVDA", "NASDAQ:AAPL", "NASDAQ:MSFT", "NASDAQ:AMZN", "NASDAQ:GOOGL",
    "NASDAQ:META", "NASDAQ:AVGO", "NASDAQ:TSLA", "NASDAQ:MU", "NYSE:JPM",
}


def shipped_filters():
    return cal.load_macro_event_filters(FILTERS_FILE)


def shipped_watchlist():
    return cal.load_earnings_watchlist(WATCHLIST_FILE)


# ------------------------------------------------- economic normalization

def test_economic_event_fields_are_mapped_and_comment_dropped():
    events = cal.normalize_economic_events({"result": [RETAIL], "status": "ok"})
    assert events == [{
        "id": "1001661350203",
        "title": "Retail Sales MoM",
        "indicator": "Retail Sales MM",
        "country": "US",
        "importance": 1,
        "time_utc": "2026-11-17T13:30:00+00:00",
        "period": "Oct. 2026",
        "unit": "%",
        "scale": None,
        "forecast": 0.2,
        "previous": 0.8,
        "actual": None,
        "source": "TradingView MCP",
    }]
    assert "comment" not in events[0]


def test_economic_wrapper_variants_give_the_same_result():
    expected = cal.normalize_economic_events(ECON_PAYLOAD)
    assert len(expected) == len(ALL_ECON_ROWS)
    assert cal.normalize_economic_events(ALL_ECON_ROWS) == expected
    assert cal.normalize_economic_events(json.dumps(ECON_PAYLOAD)) == expected
    assert cal.normalize_economic_events({"data": {"result": ALL_ECON_ROWS}}) == expected


def test_economic_rows_without_usable_date_or_title_are_skipped():
    rows = [
        RETAIL,
        econ_row(id="a", date=None),
        econ_row(id="b", date="2026-11-17T13:30:00"),  # no timezone
        econ_row(id="c", date="not a date"),
        econ_row(id="d", title=None, indicator=None),
        "not a row",
        42,
    ]
    events = cal.normalize_economic_events(rows)
    assert [event["id"] for event in events] == ["1001661350203"]


def test_economic_duplicates_are_removed_by_id():
    events = cal.normalize_economic_events([RETAIL, copy.deepcopy(RETAIL)])
    assert len(events) == 1


def test_economic_events_are_sorted_by_time_then_title():
    shuffled = [CPI, RETAIL, NFP, MICHIGAN, PCE, GDP]
    events = cal.normalize_economic_events(shuffled)
    times = [event["time_utc"] for event in events]
    assert times == sorted(times)
    same_time = [e["title"] for e in events if e["time_utc"] == "2026-11-25T13:30:00+00:00"]
    assert same_time == sorted(same_time)


def test_economic_numbers_reject_bool_infinity_and_strings():
    row = econ_row(forecast=True, previous=float("inf"), actual="0.5")
    event = cal.normalize_economic_events([row])[0]
    assert event["forecast"] is None
    assert event["previous"] is None
    assert event["actual"] is None


def test_economic_row_without_ticker_is_accepted():
    events = cal.normalize_economic_events([ISM_SERVICES])
    assert events[0]["title"] == "ISM Services PMI"


@pytest.mark.parametrize("payload", [None, 42, "not json", {}, {"result": "x"}, {"result": 5}])
def test_economic_garbage_payloads_give_empty_list(payload):
    assert cal.normalize_economic_events(payload) == []


def test_economic_normalization_does_not_mutate_input():
    payload = copy.deepcopy(ECON_PAYLOAD)
    snapshot = copy.deepcopy(payload)
    cal.normalize_economic_events(payload)
    assert payload == snapshot


# ------------------------------------------------- earnings normalization

def by_symbol(result):
    return {item["symbol"]: item for item in result["items"]}


def test_in_window_earnings_item_is_mapped():
    item = by_symbol(cal.normalize_earnings(EARN_PAYLOAD))["NASDAQ:NVDA"]
    assert item == {
        "symbol": "NASDAQ:NVDA",
        "ticker": "NVDA",
        "name": "NVIDIA Corporation",
        "next_report_date": "2026-11-17",
        "next_report_at_utc": "2026-11-17T21:00:00+00:00",
        "date_confirmed": True,
        "time_known": True,
        "last_report_date": "2026-08-26",
        "fiscal_period_end": "2026-10-31",
        "eps_forecast": 2.474056,
        "revenue_forecast": 108932722899,
        "in_window": True,
        "source": "TradingView MCP",
    }


def test_unconfirmed_placeholder_time_is_dropped():
    item = by_symbol(cal.normalize_earnings(EARN_PAYLOAD))["NASDAQ:MU"]
    assert item["time_known"] is False
    assert item["next_report_at_utc"] is None
    assert item["next_report_date"] == "2026-12-23"
    assert item["date_confirmed"] is False


def test_missing_time_flag_keeps_the_time_but_marks_it_unknown():
    row = earn_row()
    row.pop("release_next_time_flag")
    item = cal.normalize_earnings({"earnings": [row]})["items"][0]
    assert item["time_known"] is None
    assert item["next_report_at_utc"] == "2026-11-17T21:00:00+00:00"


def test_out_of_window_items_carry_dates_only():
    item = by_symbol(cal.normalize_earnings(EARN_PAYLOAD))["NASDAQ:AAPL"]
    assert item["in_window"] is False
    assert item["next_report_date"] == "2027-02-04"
    assert item["last_report_date"] == "2026-11-02"
    assert item["ticker"] == "AAPL"
    assert item["next_report_at_utc"] is None
    assert item["name"] is None
    assert item["eps_forecast"] is None
    assert item["time_known"] is None
    assert item["date_confirmed"] is None


def test_all_ten_watchlist_symbols_are_present_and_none_missing():
    result = cal.normalize_earnings(EARN_PAYLOAD, shipped_watchlist())
    assert {item["symbol"] for item in result["items"]} == TEN_SYMBOLS
    assert result["missing_symbols"] == []


def test_earnings_are_sorted_by_date():
    items = cal.normalize_earnings(EARN_PAYLOAD)["items"]
    assert [item["symbol"] for item in items[:3]] == [
        "NASDAQ:NVDA", "NASDAQ:AVGO", "NASDAQ:MU",
    ]
    dates = [item["next_report_date"] for item in items]
    assert dates == sorted(dates)


def test_symbols_outside_the_watchlist_are_dropped_and_absent_ones_reported():
    payload = copy.deepcopy(EARN_PAYLOAD)
    payload["data"]["earnings"].append(earn_row(symbol="NASDAQ:ZZZZ", name="ZZZZ"))
    payload["data"]["out_of_window"] = [
        row for row in payload["data"]["out_of_window"] if row["symbol"] != "NYSE:JPM"
    ]
    result = cal.normalize_earnings(payload, shipped_watchlist())
    symbols = {item["symbol"] for item in result["items"]}
    assert "NASDAQ:ZZZZ" not in symbols
    assert result["missing_symbols"] == ["NYSE:JPM"]


def test_without_a_watchlist_everything_is_kept():
    result = cal.normalize_earnings(EARN_PAYLOAD)
    assert len(result["items"]) == 10
    assert result["missing_symbols"] == []


def test_a_symbol_in_both_lists_keeps_the_in_window_entry():
    payload = {"earnings": [earn_row()],
               "out_of_window": [{"release_next_date": "2027-02-01", "symbol": "NASDAQ:NVDA"}]}
    items = cal.normalize_earnings(payload)["items"]
    assert len(items) == 1
    assert items[0]["in_window"] is True


def test_earnings_wrapper_variants_give_the_same_result():
    expected = cal.normalize_earnings(EARN_PAYLOAD)
    assert cal.normalize_earnings(EARN_PAYLOAD["data"]) == expected
    assert cal.normalize_earnings(json.dumps(EARN_PAYLOAD)) == expected


def test_earnings_rows_without_a_date_are_skipped():
    payload = {"earnings": [earn_row(release_next_at=None, release_next_date=None)]}
    assert cal.normalize_earnings(payload)["items"] == []


def test_earnings_garbage_reports_every_watchlist_symbol_missing():
    result = cal.normalize_earnings("garbage", shipped_watchlist())
    assert result["items"] == []
    assert set(result["missing_symbols"]) == TEN_SYMBOLS


def test_earnings_normalization_does_not_mutate_input():
    payload = copy.deepcopy(EARN_PAYLOAD)
    snapshot = copy.deepcopy(payload)
    cal.normalize_earnings(payload, shipped_watchlist())
    assert payload == snapshot


# ----------------------------------------------------------- config loading

def test_shipped_watchlist_has_the_ten_approved_symbols():
    assert {item["symbol"] for item in shipped_watchlist()} == TEN_SYMBOLS


def test_shipped_filters_have_the_eight_approved_names():
    assert set(shipped_filters()) == {
        "NFP", "CPI", "PPI", "FOMC", "PCE", "GDP", "unemployment", "ISM",
    }


def write(tmp_path, text, name="config.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_watchlist_accepts_plain_strings_and_uppercases(tmp_path):
    path = write(tmp_path, 'earnings_watchlist:\n  - "nasdaq:nvda"\n  - symbol: "NYSE:JPM"\n')
    assert cal.load_earnings_watchlist(path) == [
        {"symbol": "NASDAQ:NVDA", "name": None},
        {"symbol": "NYSE:JPM", "name": None},
    ]


@pytest.mark.parametrize("body", [
    'earnings_watchlist:\n  - "NVDA"\n',                           # no exchange
    'earnings_watchlist:\n  - "NASDAQ:NVDA"\n  - "NASDAQ:NVDA"\n',  # duplicate
    "earnings_watchlist: []\n",
    "something_else: 1\n",
    "- just a list\n",
    "",
    "earnings_watchlist:\n  - 123\n",
])
def test_watchlist_rejects_invalid_files(tmp_path, body):
    with pytest.raises(ValueError):
        cal.load_earnings_watchlist(write(tmp_path, body))


def test_watchlist_is_capped(tmp_path):
    lines = "\n".join(f'  - "NASDAQ:A{n}"' for n in range(cal.MAX_WATCHLIST_SYMBOLS + 1))
    with pytest.raises(ValueError):
        cal.load_earnings_watchlist(write(tmp_path, "earnings_watchlist:\n" + lines + "\n"))


def test_missing_watchlist_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        cal.load_earnings_watchlist(tmp_path / "nope.yaml")


@pytest.mark.parametrize("body", [
    "macro_event_filters: {}\n",
    "macro_event_filters:\n  CPI: cpi\n",          # aliases must be a list
    "macro_event_filters:\n  CPI: []\n",
    'macro_event_filters:\n  CPI: [""]\n',
    "other: 1\n",
])
def test_filters_reject_invalid_files(tmp_path, body):
    with pytest.raises(ValueError):
        cal.load_macro_event_filters(write(tmp_path, body))


def test_filters_reject_too_many_aliases(tmp_path):
    aliases = ", ".join(f'"a{n}"' for n in range(cal.MAX_ALIASES_PER_FILTER + 1))
    with pytest.raises(ValueError):
        cal.load_macro_event_filters(write(tmp_path, f"macro_event_filters:\n  X: [{aliases}]\n"))


# ----------------------------------------------------------- macro filtering

def test_shipped_filters_classify_real_events_as_the_owner_expects():
    events = cal.normalize_economic_events(ECON_PAYLOAD)
    filters = shipped_filters()
    matched = {event["title"]: cal.match_macro_filters(event, filters) for event in events}
    assert matched["Core PCE Price Index MoM"] == ["PCE"]
    assert matched["GDP Growth Rate QoQ 2nd Est"] == ["GDP"]
    assert matched["ISM Services PMI"] == ["ISM"]
    assert matched["Non Farm Payrolls"] == ["NFP"]
    assert matched["Interest Rate"] == ["FOMC"]
    assert matched["Inflation Rate MoM"] == ["CPI"]
    # Not on the approved list, so hidden by default:
    for hidden in ("Retail Sales MoM", "Housing Starts", "JOLTs Job Openings",
                   "Michigan Consumer Sentiment Prel", "Existing Home Sales"):
        assert matched[hidden] == []


def test_fomc_decision_matches_through_the_indicator_not_the_title():
    filters = {"FOMC": ["fomc"]}
    title_only = {"title": "Interest Rate", "indicator": None}
    with_indicator = {"title": "Interest Rate", "indicator": "FOMC Decision Fed Funds Tgt Rate"}
    assert cal.match_macro_filters(title_only, filters) == []
    assert cal.match_macro_filters(with_indicator, filters) == ["FOMC"]


def test_aliases_match_whole_words_only():
    filters = {"ISM": ["ism"]}
    assert cal.match_macro_filters({"title": "Prism Index"}, filters) == []
    assert cal.match_macro_filters({"title": "ISM Manufacturing PMI"}, filters) == ["ISM"]
    assert cal.match_macro_filters({"title": "ism services"}, filters) == ["ISM"]


def test_event_comment_text_is_never_used_for_matching():
    events = cal.normalize_economic_events([JOLTS])  # its comment mentions "nonfarm"
    assert cal.filter_macro_events(events, shipped_filters()) == []


def test_filter_macro_events_tags_matches_and_leaves_input_alone():
    events = cal.normalize_economic_events(ECON_PAYLOAD)
    snapshot = copy.deepcopy(events)
    kept = cal.filter_macro_events(events, shipped_filters())
    assert events == snapshot
    assert all("matched_filters" in event for event in kept)
    assert {event["title"] for event in kept} == {
        "Core PCE Price Index MoM", "GDP Growth Rate QoQ 2nd Est", "ISM Services PMI",
        "Non Farm Payrolls", "Interest Rate", "Inflation Rate MoM",
    }


# ------------------------------------------------------------------ windows

def test_events_in_window_includes_inside_and_excludes_outside():
    events = cal.normalize_economic_events(ECON_PAYLOAD)
    three = cal.events_in_window(events, now=NOW, days=3)
    assert [event["title"] for event in three] == ["Retail Sales MoM"]
    seven = cal.events_in_window(events, now=NOW, days=7)
    assert [event["title"] for event in seven] == ["Retail Sales MoM", "Housing Starts"]


def test_events_that_already_happened_are_excluded_unless_requested():
    events = cal.normalize_economic_events([RETAIL])
    after = datetime(2026, 11, 17, 14, 0, tzinfo=UTC)
    assert cal.events_in_window(events, now=after, days=3) == []
    assert len(cal.events_in_window(events, now=after, days=3, include_past_hours=1)) == 1


def test_window_accepts_a_non_utc_now():
    events = cal.normalize_economic_events([RETAIL])
    eastern = timezone(timedelta(hours=-5))
    same_moment = datetime(2026, 11, 15, 7, 0, tzinfo=eastern)  # == 12:00 UTC
    assert len(cal.events_in_window(events, now=same_moment, days=3)) == 1


def test_window_rejects_naive_now_and_bad_days():
    events = cal.normalize_economic_events([RETAIL])
    with pytest.raises(ValueError):
        cal.events_in_window(events, now=datetime(2026, 11, 15, 12, 0), days=3)
    for bad in (0, -1, 32, True, "3"):
        with pytest.raises(ValueError):
            cal.events_in_window(events, now=NOW, days=bad)


def test_earnings_with_a_known_time_use_the_exact_window():
    items = cal.normalize_earnings(EARN_PAYLOAD)["items"]
    three = cal.earnings_in_window(items, now=NOW, days=3)
    assert [item["symbol"] for item in three] == ["NASDAQ:NVDA"]


def test_earnings_known_time_boundary():
    items = cal.normalize_earnings(EARN_PAYLOAD)["items"]
    # NVDA reports 2026-11-17 21:00Z. A window ending before that excludes it.
    assert cal.earnings_in_window(items, now=NOW, days=2) == []
    assert len(cal.earnings_in_window(items, now=NOW, days=3)) == 1


def test_earnings_with_a_date_only_include_the_last_day_inclusive():
    item = {"symbol": "X:X", "next_report_at_utc": None, "next_report_date": "2026-11-18"}
    assert len(cal.earnings_in_window([item], now=NOW, days=3)) == 1   # window ends 11-18
    assert cal.earnings_in_window([item], now=NOW, days=2) == []        # window ends 11-17


def test_earnings_already_reported_are_excluded():
    items = cal.normalize_earnings(EARN_PAYLOAD)["items"]
    after = datetime(2026, 11, 17, 22, 0, tzinfo=UTC)
    assert cal.earnings_in_window(items, now=after, days=3) == []


def test_weekly_pull_dates_cover_fourteen_days_from_today():
    assert cal.weekly_pull_dates(NOW) == {"date_from": "2026-11-15", "date_to": "2026-11-29"}


def test_weekly_pull_dates_use_the_utc_date():
    eastern = timezone(timedelta(hours=-5))
    late_evening = datetime(2026, 11, 15, 23, 30, tzinfo=eastern)  # 04:30 UTC next day
    assert cal.weekly_pull_dates(late_evening)["date_from"] == "2026-11-16"
    with pytest.raises(ValueError):
        cal.weekly_pull_dates(datetime(2026, 11, 15))


# --------------------------------------------------------------------- view

def build_view(now):
    return cal.build_calendar_view(
        economic_events=cal.normalize_economic_events(ECON_PAYLOAD),
        earnings=cal.normalize_earnings(EARN_PAYLOAD)["items"],
        macro_filters=shipped_filters(),
        now=now,
    )


def test_calendar_view_windows_and_hidden_counts():
    view = build_view(datetime(2026, 12, 3, 12, 0, tzinfo=UTC))
    three = view["windows"]["next_3_days"]
    week = view["windows"]["this_week"]
    assert [e["title"] for e in three["macro_events"]] == ["ISM Services PMI", "Non Farm Payrolls"]
    assert three["other_macro_event_count"] == 1          # Michigan Sentiment
    assert [e["title"] for e in week["macro_events"]] == [
        "ISM Services PMI", "Non Farm Payrolls", "Interest Rate",
    ]
    assert week["other_macro_event_count"] == 2           # Michigan, Existing Home Sales
    assert three["earnings"] == []
    assert [e["symbol"] for e in week["earnings"]] == ["NASDAQ:AVGO"]
    assert three["days"] == 3 and week["days"] == 7


def test_calendar_view_labels_source_and_has_no_trade_authority():
    view = build_view(NOW)
    assert view["source"] == "TradingView MCP"
    assert view["timezone"] == "UTC"
    assert view["trade_authority"] == "none"
    assert view["order_execution"] == "disabled"
    assert view["human_approval_required"] is True
    assert view["filter_names"] == sorted(shipped_filters())


def test_calendar_view_does_not_mutate_its_inputs():
    events = cal.normalize_economic_events(ECON_PAYLOAD)
    earnings = cal.normalize_earnings(EARN_PAYLOAD)["items"]
    filters = shipped_filters()
    snapshots = (copy.deepcopy(events), copy.deepcopy(earnings), copy.deepcopy(filters))
    cal.build_calendar_view(economic_events=events, earnings=earnings,
                            macro_filters=filters, now=NOW)
    assert (events, earnings, filters) == snapshots


def test_calendar_view_with_no_data_is_empty_not_an_error():
    view = cal.build_calendar_view(economic_events=[], earnings=[],
                                   macro_filters=shipped_filters(), now=NOW)
    for window in view["windows"].values():
        assert window["macro_events"] == []
        assert window["earnings"] == []
        assert window["other_macro_event_count"] == 0
