import datetime as dt
import io
import zipfile

import numpy as np

from investai.data import bhavcopy as bc
from investai.data.pricestore import connect


def _zip(name, csv_text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, csv_text)
    return buf.getvalue()


UDIFF = ("TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,PrvsClsgPric,TtlTradgVol,ISIN\n"
         "AAA,EQ,10,11,9,10.5,9.8,1000,INE001\n"
         "FUTX,FT,1,1,1,1,1,1,INE999\n")
LEGACY = ("SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,PREVCLOSE,TOTTRDQTY,ISIN\n"
          "BBB,EQ,20,21,19,20.5,19.8,2000,INE002\n"
          "GBND,GB,1,1,1,1,1,1,INE998\n")


def test_url_picks_format_by_date():
    assert "BhavCopy_NSE_CM_0_0_0_20240801" in bc._url(dt.date(2024, 8, 1))
    u = bc._url(dt.date(2023, 6, 12))
    assert "cm12JUN2023bhav" in u and "/2023/JUN/" in u


def test_parse_udiff_keeps_only_equities():
    df = bc._parse(_zip("BhavCopy_x.csv", UDIFF), dt.date(2024, 8, 1))
    assert set(df["symbol"]) == {"AAA"}                  # FUT row dropped
    r = df.iloc[0]
    assert r["close"] == 10.5 and r["prevclose"] == 9.8 and r["date"] == "2024-08-01"


def test_parse_legacy_keeps_only_equities():
    df = bc._parse(_zip("cm01AUG2023bhav.csv", LEGACY), dt.date(2023, 8, 1))
    assert set(df["symbol"]) == {"BBB"}                  # bond row dropped
    assert df.iloc[0]["close"] == 20.5


def test_adjust_factors_detects_split():
    close = np.array([100.0, 100.0, 51.0, 52.0])
    open_ = np.array([100.0, 100.0, 50.0, 52.0])         # gap-down open on bar 2 = 1:2 split
    f = bc.adjust_factors(close, open_)
    assert f[0] == 1.0 and f[1] == 1.0 and f[3] == 1.0
    assert abs(f[2] - 0.5) < 1e-9


def test_adjust_factors_ignores_genuine_large_move():
    # A real ~-28% gap (Adani-style) is NOT a clean split ratio -> left untouched.
    close = np.array([100.0, 72.0, 70.0])
    open_ = np.array([100.0, 72.0, 70.0])
    f = bc.adjust_factors(close, open_)
    assert (f == 1.0).all()


def test_build_adjusted_back_adjusts_split(tmp_cfg):
    conn = connect(tmp_cfg)
    conn.executescript(bc._RAW_SCHEMA)
    rows = [  # 1:2 split on 2024-01-03 (open gaps to half of prior close)
        ("AAA", "2024-01-01", 100, 100, 100, 100, 99, 1000, "INE001"),
        ("AAA", "2024-01-02", 100, 100, 100, 100, 100, 1000, "INE001"),
        ("AAA", "2024-01-03", 50, 51, 50, 51, 100, 2000, "INE001"),   # open=50 gap
        ("AAA", "2024-01-04", 52, 52, 52, 52, 51, 2000, "INE001"),
    ]
    conn.executemany("INSERT INTO bhav_raw VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()

    summary = bc.build_adjusted(tmp_cfg)
    assert summary["symbols_adjusted"] == 1
    assert summary["splits_bonuses_detected"] == 1

    conn = connect(tmp_cfg)
    got = [r[0] for r in conn.execute(
        "SELECT adj_close FROM prices WHERE symbol='AAA' ORDER BY date").fetchall()]
    conn.close()
    # pre-split prices halved -> continuous series, no fake -49% gap
    assert np.allclose(got, [50.0, 50.0, 51.0, 52.0])
