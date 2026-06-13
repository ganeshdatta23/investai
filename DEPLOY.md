# Deploying InvestAI (free, paper-only)

This deploys the engine as a **free, serverless, autonomous PAPER-trading system**.
Every NSE trading evening it scans, decides, updates a simulated ledger, and publishes
a dashboard. **It never places a real order.** A hard live-gate stays locked until a
strategy is proven *and* a human approves each order — and per
[`CONCLUSION.md`](CONCLUSION.md), no proven edge exists today, so that gate stays shut.

There is no paid infrastructure. Two ways to run it:

---

## Option 1 — Cloud, always-on (GitHub Actions + Pages) · $0

1. **Create a repo and push:**
   ```bash
   git init && git add -A && git commit -m "investai"
   git branch -M main
   git remote add origin https://github.com/<you>/investai.git
   git push -u origin main
   ```
2. **Enable Actions:** repo → *Settings → Actions → General* → allow workflows, and under
   *Workflow permissions* select **Read and write**.
3. **Enable the dashboard:** *Settings → Pages* → Source = **Deploy from a branch**,
   Branch = **main**, folder = **/docs** → Save.
4. **Run it:** *Actions* tab → **paper-trade** → *Run workflow* (or wait for the daily
   16:00 IST cron). It commits `state/paper.db` + `docs/index.html` each run.
5. **View:** `https://<you>.github.io/<repo>/`

The schedule is in [`.github/workflows/paper.yml`](.github/workflows/paper.yml)
(`cron: "30 10 * * 1-5"` = 16:00 IST, Mon–Fri).

> **Data note:** cloud runners fetch via Yahoo Finance. If Yahoo throttles the runner IP,
> the dashboard still publishes (showing `data_unavailable`) — use Option 2 for a
> guaranteed feed, or later wire Upstox.

---

## Option 2 — Local, on your machine (Windows Task Scheduler) · $0

Run once to verify:
```powershell
C:\Users\ganes\.conda\envs\investai\python.exe -m investai dashboard --out docs
```
Then schedule it daily (run from the project folder) with
[`scripts/run_daily.ps1`](scripts/run_daily.ps1):
```powershell
schtasks /Create /TN "InvestAI Paper" /TR "powershell -File C:\Users\ganes\projectspace\investai\scripts\run_daily.ps1" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 16:00
```
Open `docs/index.html` in a browser to view the dashboard.

---

## What it does / does NOT do

| Does | Does NOT |
|---|---|
| Scan NSE, score, decide, size by risk policy | Place any real buy/sell order |
| Track a **simulated** ledger + P&L | Touch your money or your broker |
| Publish an honest dashboard | Promise returns |
| Persist state across runs | Bypass the live-gate |

To ever go live you would have to: (1) prove an edge in forward-testing, (2) wire a broker
(Upstox/Dhan), (3) flip `live_mode`, and (4) approve **each** order by hand. The code keeps
that gate closed by default on purpose.
