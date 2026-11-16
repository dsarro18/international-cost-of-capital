"""
International Cost of Capital App
Compares a project's cost of equity, cost of debt and WACC in USD and in the
currency of a selected country.

Data sources (fetched live, cached for 24h, refreshable from the sidebar):
  * Industry unlevered betas ........ Aswath Damodaran (NYU Stern), betas.xls
  * BBB (Baa2) USD default spread .... Aswath Damodaran, ratings.html
  * Statutory corporate tax rates .... OECD Corporate Tax Statistics (SDMX API)
  * 10-year government bond yields ... Trading Economics
"""

import io
import re
from datetime import datetime

import pandas as pd
import requests
import streamlit as st

# ----------------------------------------------------------------------------
# Sources
# ----------------------------------------------------------------------------
BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
RATINGS_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html"
OECD_PAGE_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html"
)
# The OECD report page blocks scripts, so the same statutory-rate table is pulled
# from the OECD's official data API (dataset DSD_TAX_CIT@DF_CIT).
OECD_API_URL = (
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,2.0/all"
    "?startPeriod=2020&format=csvfilewithlabels"
)
TE_BONDS_URL = "https://tradingeconomics.com/bonds"
TE_COUNTRY_URL = "https://tradingeconomics.com/{slug}/government-bond-yield"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIMEOUT = 30

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"

# Display name -> (OECD reference-area name, Trading Economics URL slug)
COUNTRIES = {
    "United States": ("United States", "united-states"),
    "Australia": ("Australia", "australia"),
    "Austria": ("Austria", "austria"),
    "Belgium": ("Belgium", "belgium"),
    "Brazil": ("Brazil", "brazil"),
    "Bulgaria": ("Bulgaria", "bulgaria"),
    "Canada": ("Canada", "canada"),
    "Chile": ("Chile", "chile"),
    "China": ("China (People’s Republic of)", "china"),
    "Colombia": ("Colombia", "colombia"),
    "Croatia": ("Croatia", "croatia"),
    "Czech Republic": ("Czechia", "czech-republic"),
    "Denmark": ("Denmark", "denmark"),
    "Egypt": ("Egypt", "egypt"),
    "Finland": ("Finland", "finland"),
    "France": ("France", "france"),
    "Germany": ("Germany", "germany"),
    "Greece": ("Greece", "greece"),
    "Hong Kong": ("Hong Kong (China)", "hong-kong"),
    "Hungary": ("Hungary", "hungary"),
    "Iceland": ("Iceland", "iceland"),
    "India": ("India", "india"),
    "Indonesia": ("Indonesia", "indonesia"),
    "Ireland": ("Ireland", "ireland"),
    "Israel": ("Israel", "israel"),
    "Italy": ("Italy", "italy"),
    "Japan": ("Japan", "japan"),
    "Kenya": ("Kenya", "kenya"),
    "Malaysia": ("Malaysia", "malaysia"),
    "Mexico": ("Mexico", "mexico"),
    "Morocco": ("Morocco", "morocco"),
    "Netherlands": ("Netherlands", "netherlands"),
    "New Zealand": ("New Zealand", "new-zealand"),
    "Nigeria": ("Nigeria", "nigeria"),
    "Norway": ("Norway", "norway"),
    "Pakistan": ("Pakistan", "pakistan"),
    "Peru": ("Peru", "peru"),
    "Philippines": ("Philippines", "philippines"),
    "Poland": ("Poland", "poland"),
    "Portugal": ("Portugal", "portugal"),
    "Romania": ("Romania", "romania"),
    "Serbia": ("Serbia", "serbia"),
    "Singapore": ("Singapore", "singapore"),
    "Slovakia": ("Slovak Republic", "slovakia"),
    "Slovenia": ("Slovenia", "slovenia"),
    "South Africa": ("South Africa", "south-africa"),
    "South Korea": ("Korea", "south-korea"),
    "Spain": ("Spain", "spain"),
    "Sri Lanka": ("Sri Lanka", "sri-lanka"),
    "Sweden": ("Sweden", "sweden"),
    "Switzerland": ("Switzerland", "switzerland"),
    "Thailand": ("Thailand", "thailand"),
    "Turkey": ("Türkiye", "turkey"),
    "United Kingdom": ("United Kingdom", "united-kingdom"),
    "Vietnam": ("Viet Nam", "vietnam"),
}

# Aliases used when matching rows of the Trading Economics /bonds table
TE_ALIASES = {
    "Czech Republic": ["Czech Republic", "Czechia"],
    "South Korea": ["South Korea", "Korea"],
    "Turkey": ["Turkey", "Türkiye"],
    "Slovakia": ["Slovakia", "Slovak Republic"],
}


# ----------------------------------------------------------------------------
# Data fetching (each returns a dict with value(s), source date and status)
# ----------------------------------------------------------------------------
def _get(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_betas():
    try:
        raw = pd.read_excel(io.BytesIO(_get(BETAS_URL).content),
                            sheet_name="Industry Averages", header=None)
        hdr = raw.index[raw.iloc[:, 0].astype(str).str.strip() == "Industry Name"][0]
        df = raw.iloc[hdr + 1:].copy()
        df.columns = [str(c).strip() for c in raw.iloc[hdr]]
        df = df[["Industry Name", "Number of firms", "Unlevered beta"]].dropna()
        df = df[~df["Industry Name"].astype(str).str.contains("Total Market", case=False)]
        df["Unlevered beta"] = df["Unlevered beta"].astype(float)
        df = df.rename(columns={"Industry Name": "Industry"}).reset_index(drop=True)

        date = None
        for i in range(min(hdr, 10)):
            if str(raw.iloc[i, 0]).lower().startswith("date updated"):
                date = pd.to_datetime(raw.iloc[i, 1]).strftime("%Y-%m-%d")
        return {"ok": True, "data": df, "date": date or "n/a"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_bbb_spread():
    """Baa2/BBB spread from the large non-financial firms table."""
    try:
        html = _get(RATINGS_URL).text
        table = pd.read_html(io.StringIO(html))[0]
        spread = None
        for _, row in table.iterrows():
            cells = [str(c).strip() for c in row.tolist()]
            # first 4 columns = large non-financial service firms
            if "Baa2/BBB" in cells[:4]:
                spread = float(cells[cells.index("Baa2/BBB") + 1].replace("%", "")) / 100
                break
        if spread is None:
            raise ValueError("Baa2/BBB row not found")
        m = re.search(r"(January|February|March|April|May|June|July|August|September|"
                      r"October|November|December)\s+20\d\d", html)
        return {"ok": True, "value": spread, "date": m.group(0) if m else "n/a"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_tax_rates():
    """Combined (central + sub-central) statutory CIT rate, latest year per country."""
    try:
        d = pd.read_csv(io.StringIO(_get(OECD_API_URL).text))
        d = d[(d["MEASURE"] == "CIT_C") & (d["TARGETING"] == "ST")]
        d = d.dropna(subset=["OBS_VALUE"]).sort_values("TIME_PERIOD")
        latest = d.groupby("Reference area").tail(1)
        rates = {r["Reference area"]: (r["OBS_VALUE"] / 100, int(r["TIME_PERIOD"]))
                 for _, r in latest.iterrows()}
        return {"ok": True, "data": rates}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_te_bonds_table():
    """Trading Economics /bonds overview table (may be blocked by a captcha)."""
    try:
        for t in pd.read_html(io.StringIO(_get(TE_BONDS_URL).text)):
            cols = [str(c).lower() for c in t.columns]
            if "country" in cols[0] or "major10y" in cols[0] or "yield" in cols:
                t = t.rename(columns={t.columns[0]: "Country"})
                if "Yield" in t.columns:
                    return t
        return None
    except Exception:  # noqa: BLE001
        return None


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_10y_yield(country):
    """10-year government bond yield for one country (decimal)."""
    _, slug = COUNTRIES[country]
    # 1) overview table
    t = fetch_te_bonds_table()
    if t is not None:
        names = TE_ALIASES.get(country, [country])
        row = t[t["Country"].astype(str).str.strip().isin(names)]
        if not row.empty:
            date = str(row.iloc[0]["Date"]) if "Date" in t.columns else "n/a"
            return {"ok": True, "value": float(row.iloc[0]["Yield"]) / 100,
                    "date": date, "url": TE_BONDS_URL}
    # 2) country page
    url = TE_COUNTRY_URL.format(slug=slug)
    try:
        for t in pd.read_html(io.StringIO(_get(url).text)):
            if "Yield" not in t.columns:
                continue
            first = t.iloc[:, 0].astype(str)
            row = t[first.str.contains("10Y") & ~first.str.contains("TIPS")]
            if not row.empty:
                date = str(row.iloc[0]["Date"]) if "Date" in t.columns else "n/a"
                return {"ok": True, "value": float(row.iloc[0]["Yield"]) / 100,
                        "date": date, "url": url}
        raise ValueError("10Y row not found")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "url": url}


# ----------------------------------------------------------------------------
# Finance
# ----------------------------------------------------------------------------
def to_foreign(r_usd, rf_us, rf_fc, method):
    """Convert a USD rate into a foreign-currency rate using the yield differential."""
    if method == "exact":
        return (1 + r_usd) * (1 + rf_fc) / (1 + rf_us) - 1
    return r_usd + (rf_fc - rf_us)


def cost_of_capital(beta_u, tax, debt_share, erp, rf_us, rf_fc, spread_usd,
                    fc_spread_override=None, method="exact"):
    d = debt_share
    de = d / (1 - d) if d < 1 else float("inf")
    beta_l = beta_u * (1 + (1 - tax) * de)

    re_usd = rf_us + beta_l * erp
    rd_usd = rf_us + spread_usd
    wacc_usd = (1 - d) * re_usd + d * rd_usd * (1 - tax)

    re_fc = to_foreign(re_usd, rf_us, rf_fc, method)
    if fc_spread_override is None:
        rd_fc = to_foreign(rd_usd, rf_us, rf_fc, method)
    else:
        rd_fc = rf_fc + fc_spread_override
    wacc_fc = (1 - d) * re_fc + d * rd_fc * (1 - tax)

    return {
        "beta_l": beta_l, "de": de,
        "usd": {"rf": rf_us, "re": re_usd, "spread": spread_usd, "rd": rd_usd,
                "rd_at": rd_usd * (1 - tax), "wacc": wacc_usd},
        "fc": {"rf": rf_fc, "re": re_fc, "spread": rd_fc - rf_fc, "rd": rd_fc,
               "rd_at": rd_fc * (1 - tax), "wacc": wacc_fc},
    }


def pct(x, nd=2):
    return f"{x * 100:.{nd}f}%"


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
st.set_page_config(page_title="International Cost of Capital", layout="wide")
st.title("International Cost of Capital")
st.caption("Cost of equity, cost of debt and WACC for a project, in USD and in the "
           "currency of a selected country.")

if "fetched_at" not in st.session_state:
    st.session_state.fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M")

with st.sidebar:
    st.header("Data")
    if st.button("🔄 Refresh data fetch", width="stretch"):
        st.cache_data.clear()
        st.session_state.fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        st.rerun()
    st.caption(f"Last fetch: {st.session_state.fetched_at}")

with st.spinner("Fetching data from Damodaran, OECD and Trading Economics..."):
    betas = fetch_betas()
    bbb = fetch_bbb_spread()
    taxes = fetch_tax_rates()

if not betas["ok"]:
    st.error(f"Could not load Damodaran betas: {betas['error']}. Use manual inputs.")
    industries = [DEFAULT_INDUSTRY]
    beta_df = pd.DataFrame({"Industry": [DEFAULT_INDUSTRY], "Number of firms": [None],
                            "Unlevered beta": [1.0]})
else:
    beta_df = betas["data"]
    industries = beta_df["Industry"].tolist()

with st.sidebar:
    st.header("Project")
    industry = st.selectbox(
        "Industry", industries,
        index=industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0)
    country_names = list(COUNTRIES)
    country = st.selectbox("Target country", country_names,
                           index=country_names.index(DEFAULT_COUNTRY))
    erp = st.number_input("Equity risk premium (%)", 0.0, 20.0, 6.5, 0.1) / 100
    debt_share = st.slider("Target debt share D/(D+E) (%)", 0, 90, 30, 1) / 100
    tax_choice = st.radio("Project tax rate", ["U.S. rate", f"{country} rate"],
                          horizontal=True)
    method_label = st.radio(
        "Currency conversion",
        ["Exact: (1+r$)(1+rf*)/(1+rf$) − 1", "Approximate: r$ + (rf* − rf$)"])
    method = "exact" if method_label.startswith("Exact") else "approx"

# fetched values
fetched_beta = float(beta_df.loc[beta_df["Industry"] == industry, "Unlevered beta"].iloc[0])
oecd_us, oecd_fc = COUNTRIES["United States"][0], COUNTRIES[country][0]
tax_us_f = taxes["data"].get(oecd_us) if taxes["ok"] else None
tax_fc_f = taxes["data"].get(oecd_fc) if taxes["ok"] else None
y_us = fetch_10y_yield("United States")
y_fc = y_us if country == "United States" else fetch_10y_yield(country)

with st.sidebar:
    st.header("Manual overrides")
    st.caption("Tick a box to replace the fetched value.")

    overridden = set()

    def override(label, fetched, default, key, step=0.01, fmt="%.4f"):
        on = st.checkbox(f"Override {label}", key=f"ov_{key}")
        if on or fetched is None:
            overridden.add(key)
        base = fetched if fetched is not None else default
        if on or fetched is None:
            if fetched is None:
                st.caption(f"⚠️ {label}: fetch failed, manual value used.")
            return st.number_input(label, value=float(base), step=step, format=fmt,
                                   key=f"in_{key}")
        return fetched

    beta_u = override("Unlevered beta", fetched_beta, 1.0, "beta")
    tax_us = override("U.S. tax rate (%)", tax_us_f[0] * 100 if tax_us_f else None,
                      25.0, "tax_us", 0.1, "%.2f") / 100
    tax_fc = override(f"{country} tax rate (%)", tax_fc_f[0] * 100 if tax_fc_f else None,
                      25.0, "tax_fc", 0.1, "%.2f") / 100
    spread_usd = override("USD BBB spread (%)", bbb["value"] * 100 if bbb["ok"] else None,
                          1.11, "spread", 0.01, "%.2f") / 100
    rf_us = override("U.S. 10Y yield (%)", y_us["value"] * 100 if y_us["ok"] else None,
                     4.0, "rf_us", 0.01, "%.3f") / 100
    rf_fc = override(f"{country} 10Y yield (%)", y_fc["value"] * 100 if y_fc["ok"] else None,
                     4.0, "rf_fc", 0.01, "%.3f") / 100
    fc_override_on = st.checkbox(f"Override {country} BBB spread (local currency)")
    fc_spread = None
    if fc_override_on:
        fc_spread = st.number_input(f"{country} BBB spread (%)", value=spread_usd * 100,
                                    step=0.01, format="%.2f") / 100

tax = tax_us if tax_choice == "U.S. rate" else tax_fc
res = cost_of_capital(beta_u, tax, debt_share, erp, rf_us, rf_fc, spread_usd,
                      fc_spread, method)

# ---- Headline metrics ------------------------------------------------------
st.subheader(f"{industry} — United States (USD) vs {country} (local currency)")
c1, c2 = st.columns(2)
for col, key, label in [(c1, "usd", "USD"), (c2, "fc", f"{country} currency")]:
    with col:
        st.markdown(f"#### {label}")
        m1, m2, m3 = st.columns(3)
        m1.metric("Cost of equity", pct(res[key]["re"]))
        m2.metric("After-tax cost of debt", pct(res[key]["rd_at"]))
        m3.metric("WACC", pct(res[key]["wacc"]))

# ---- Side-by-side table ----------------------------------------------------
rows = [
    ("10Y government bond yield (risk-free)", res["usd"]["rf"], res["fc"]["rf"]),
    ("Unlevered beta", beta_u, beta_u),
    ("Levered beta", res["beta_l"], res["beta_l"]),
    ("Equity risk premium", erp, erp),
    ("Cost of equity", res["usd"]["re"], res["fc"]["re"]),
    ("BBB credit spread", res["usd"]["spread"], res["fc"]["spread"]),
    ("Pre-tax cost of debt", res["usd"]["rd"], res["fc"]["rd"]),
    ("Project tax rate", tax, tax),
    ("After-tax cost of debt", res["usd"]["rd_at"], res["fc"]["rd_at"]),
    ("Debt share D/(D+E)", debt_share, debt_share),
    ("WACC", res["usd"]["wacc"], res["fc"]["wacc"]),
]
results_df = pd.DataFrame(rows, columns=["Item", "USD", f"{country} currency"])
show = results_df.copy()
for c in show.columns[1:]:
    show[c] = [f"{v:.3f}" if "beta" in i.lower() else pct(v)
               for i, v in zip(show["Item"], show[c])]
st.dataframe(show, hide_index=True, width="stretch")
if fc_spread is not None:
    st.caption(f"{country} BBB spread is a manual override. USD results are unaffected.")
else:
    st.caption(f"{country} BBB spread is calculated automatically: the USD cost of debt "
               "converted with the yield differential, minus the local 10Y yield.")

# ---- All-industry comparison ----------------------------------------------
st.subheader("All-industry comparison")
st.caption("Same country, tax rate, debt share, ERP, yields and spreads. Uses each "
           "industry's fetched Damodaran unlevered beta.")
all_rows = []
for _, r in beta_df.iterrows():
    o = cost_of_capital(r["Unlevered beta"], tax, debt_share, erp, rf_us, rf_fc,
                        spread_usd, fc_spread, method)
    all_rows.append({
        "Industry": r["Industry"], "Unlevered beta": r["Unlevered beta"],
        "Levered beta": o["beta_l"],
        "Cost of equity USD": o["usd"]["re"], f"Cost of equity {country}": o["fc"]["re"],
        "WACC USD": o["usd"]["wacc"], f"WACC {country}": o["fc"]["wacc"],
    })
all_df = pd.DataFrame(all_rows).sort_values("WACC USD", ascending=False)
fmt = {c: "{:.2%}" for c in all_df.columns if c not in ("Industry", "Unlevered beta",
                                                         "Levered beta")}
fmt.update({"Unlevered beta": "{:.3f}", "Levered beta": "{:.3f}"})
st.dataframe(
    all_df.style.format(fmt).apply(
        lambda s: ["background-color: #fff3cd" if s["Industry"] == industry else ""
                   for _ in s], axis=1),
    hide_index=True, width="stretch", height=420)

# ---- Downloads -------------------------------------------------------------
inputs_df = pd.DataFrame([
    ("Industry", industry), ("Target country", country),
    ("Unlevered beta", beta_u), ("Equity risk premium", erp),
    ("Debt share", debt_share), ("Project tax rate", tax),
    ("Tax rate choice", tax_choice), ("U.S. tax rate", tax_us),
    (f"{country} tax rate", tax_fc), ("USD BBB spread", spread_usd),
    ("U.S. 10Y yield", rf_us), (f"{country} 10Y yield", rf_fc),
    (f"{country} BBB spread override", fc_spread if fc_spread is not None else "off"),
    ("Conversion method", method_label), ("Data fetched", st.session_state.fetched_at),
], columns=["Input", "Value"])

xbuf = io.BytesIO()
with pd.ExcelWriter(xbuf, engine="openpyxl") as xw:
    inputs_df.to_excel(xw, sheet_name="Inputs", index=False)
    results_df.to_excel(xw, sheet_name="Results", index=False)
    all_df.to_excel(xw, sheet_name="All industries", index=False)

d1, d2, d3 = st.columns(3)
d1.download_button("⬇️ Results (CSV)", results_df.to_csv(index=False),
                   "cost_of_capital_results.csv", "text/csv", width="stretch")
d2.download_button("⬇️ All industries (CSV)", all_df.to_csv(index=False),
                   "cost_of_capital_all_industries.csv", "text/csv",
                   width="stretch")
d3.download_button("⬇️ Everything (Excel)", xbuf.getvalue(), "cost_of_capital.xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   width="stretch")

# ---- Assumptions -----------------------------------------------------------
with st.expander("Method and main assumptions", expanded=False):
    st.markdown(r"""
**1. Relever the industry beta** (Hamada, with the project tax rate):
$\beta_L = \beta_U\,[1 + (1-t)\,D/E]$, where $D/E = d/(1-d)$ and $d$ is the target debt share.

**2. USD cost of equity (CAPM):** $r_E^{\$} = r_f^{\$} + \beta_L \times ERP$

**3. USD cost of debt:** $r_D^{\$} = r_f^{\$} + s_{BBB}$ (Damodaran Baa2/BBB spread, large non-financial firms)

**4. Convert to the foreign currency** with the 10-year government yield differential
(international Fisher effect / interest parity):
- Exact: $1 + r^{FC} = (1 + r^{\$})\,\dfrac{1 + r_f^{FC}}{1 + r_f^{\$}}$
- Approximate: $r^{FC} = r^{\$} + (r_f^{FC} - r_f^{\$})$

The foreign cost of debt is converted the same way, unless you override the
foreign BBB spread. Then $r_D^{FC} = r_f^{FC} + s^{FC}$.

**5. WACC:** $WACC = (1-d)\,r_E + d\,r_D\,(1-t)$, using the same project tax rate,
debt share and ERP in both currencies.

**Assumptions**
- The 10-year government bond yield is the risk-free rate. It contains no default
  adjustment, so for emerging markets it embeds sovereign risk.
- Damodaran's unlevered betas are U.S. industry averages, applied to the project
  wherever it is located.
- Tax rates are the OECD combined (central + sub-central) statutory CIT rates,
  latest year available.
- The ERP is the same in both currencies. Currency risk enters only through
  the yield differential.
""")

# ---- Built-in verification -------------------------------------------------
with st.expander("Verification checks", expanded=False):
    base = dict(beta_u=beta_u, tax=tax, debt_share=debt_share, erp=erp,
                spread_usd=spread_usd, method=method)
    a = cost_of_capital(rf_us=rf_us, rf_fc=rf_us, fc_spread_override=None, **base)
    ok1 = abs(a["usd"]["wacc"] - a["fc"]["wacc"]) < 1e-12 and \
        abs(a["usd"]["re"] - a["fc"]["re"]) < 1e-12
    z = cost_of_capital(**{**base, "debt_share": 0.0}, rf_us=rf_us, rf_fc=rf_fc)
    ok2 = abs(z["usd"]["wacc"] - z["usd"]["re"]) < 1e-12 and \
        abs(z["fc"]["wacc"] - z["fc"]["re"]) < 1e-12
    p = cost_of_capital(rf_us=rf_us, rf_fc=rf_fc, fc_spread_override=None, **base)
    q = cost_of_capital(rf_us=rf_us, rf_fc=rf_fc, fc_spread_override=0.05, **base)
    ok3 = p["usd"] == q["usd"]
    st.markdown(
        f"- {'✅' if ok1 else '❌'} Equal government yields give equal USD and foreign "
        f"results (spread override off): WACC {pct(a['usd']['wacc'], 4)} vs "
        f"{pct(a['fc']['wacc'], 4)}\n"
        f"- {'✅' if ok2 else '❌'} Zero debt makes WACC equal the cost of equity: "
        f"{pct(z['usd']['wacc'], 4)} = {pct(z['usd']['re'], 4)}\n"
        f"- {'✅' if ok3 else '❌'} Overriding the foreign spread leaves the USD results "
        f"unchanged: USD WACC {pct(p['usd']['wacc'], 4)} both times")

# ---- Sources ---------------------------------------------------------------
st.subheader("Sources and dates")


def status(ok, txt, key=None):
    if key in overridden and ok:
        return f"Manual override (fetched: {txt})"
    return txt if ok else "⚠️ fetch failed — manual input"


src = pd.DataFrame([
    ("Industry unlevered betas (U.S.)", "Aswath Damodaran, NYU Stern", BETAS_URL,
     status(betas["ok"], f"Updated {betas.get('date')}", "beta")),
    ("BBB (Baa2) USD default spread", "Aswath Damodaran, NYU Stern", RATINGS_URL,
     status(bbb["ok"], f"Updated {bbb.get('date')}", "spread")),
    ("U.S. corporate tax rate", "OECD Corporate Tax Statistics", OECD_PAGE_URL,
     status(tax_us_f is not None, f"{tax_us_f[1]} rate" if tax_us_f else "", "tax_us")),
    (f"{country} corporate tax rate", "OECD Corporate Tax Statistics", OECD_PAGE_URL,
     status(tax_fc_f is not None, f"{tax_fc_f[1]} rate" if tax_fc_f else "", "tax_fc")),
    ("U.S. 10Y government bond yield", "Trading Economics", y_us.get("url", TE_BONDS_URL),
     status(y_us["ok"], f"Quote date {y_us.get('date')}", "rf_us")),
    (f"{country} 10Y government bond yield", "Trading Economics",
     y_fc.get("url", TE_BONDS_URL), status(y_fc["ok"], f"Quote date {y_fc.get('date')}", "rf_fc")),
], columns=["Data", "Source", "Link", "Date"])
st.dataframe(src, hide_index=True, width="stretch",
             column_config={"Link": st.column_config.LinkColumn("Link")})
st.caption(f"OECD rates are pulled from the OECD data API ({OECD_API_URL.split('?')[0]}), "
           "which serves the same statutory-rate table as the report page. "
           f"Data fetched {st.session_state.fetched_at}; cached for up to 24 hours.")
