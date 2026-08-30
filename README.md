# deckscan

Upload a pitch deck, a financial model, or both. Get two investor documents back:

1. **Financial screen** — grounding score, metric tiles, an actuals-vs-projections
   revenue chart, red flags that quote the numbers and page references that
   triggered them, and a ranked list of what needs grounding.
2. **Investor one-pager** — problem, solution, why now, business model, market
   size, traction, team, stage, ask, and risks retired, laid out to the standard
   TEN Capital template, with a red-flag analysis band at the foot of the page:
   the narrative above is what the company says, the band is what the numbers
   show. Top flags with their figures, the grounding score, and the three
   questions that would settle the most load-bearing claims.

Both are single-page PDFs. Runs as a CLI or as a web app on Railway.

## How it works

```
deck .pdf/.pptx/.docx ─────┐
                           ├─►  Claude Opus 5  ─►  merge  ─►  rule engine  ─►  two PDFs
model .xlsx/.xlsm/.csv ────┘     (reads)          (Python)     (Python)
```

Either input is enough on its own. A spreadsheet with no deck is a complete run:
every figure carries its `Sheet!C14` cell reference into the flags exactly as a
deck's figures carry their page, and the narrative one-pager falls back to
whatever prose the workbook itself states — a cover sheet's company name and
positioning line — rendering the rest as gaps rather than inventing it.

**Claude reads; Python decides.** Claude extracts figures, periods, and narrative
text — each with the page, slide, or cell it came from — and returns them as
structured JSON. Every red flag is then computed deterministically in Python
from those figures, so the same extraction always produces the same verdicts,
and every threshold behind them is a line in a YAML file you can edit.

Nothing is invented to fill a slot. A figure the source does not state comes back
empty, renders as *not disclosed*, and becomes a recorded gap.

PDFs are sent to Claude as native document blocks, so charts, tables, and
image-only slides are read from the page itself — no OCR install required. PPTX
and DOCX decks are flattened to text with slide/page markers first, so pictures
inside them are not read; supply those as PDF when the charts carry the numbers.
Workbooks are flattened the same way, one line per populated row, so a chart
drawn on a sheet is not read — its underlying cells are.

Legacy `.xls`, `.ppt`, `.doc`, Keynote and Numbers files are refused with a note
saying which format to export instead.

## Install

```bash
python -m pip install -e ".[dev]"
cp .env.example .env      # then paste your key into it
```

Create an API key at [console.anthropic.com](https://console.anthropic.com/settings/keys)
and set it:

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

Python 3.12+.

## CLI

```bash
deckscan analyze SOURCE [--model MODEL.xlsx] [--out SCREEN.pdf]
                 [--narrative ONEPAGER.pdf] [--json ANALYSIS.json]
                 [--company "Name"] [--config PATH] [--strict] [-v]

deckscan template --out onepager-template.pdf   # blank structural template
```

`SOURCE` is a deck (`.pdf`, `.pptx`, `.docx`) **or** a financial model (`.xlsx`,
`.xlsm`, `.csv`):

```bash
deckscan analyze deck.pdf                      # deck only
deckscan analyze model.xlsx                    # model only
deckscan analyze deck.pdf --model model.xlsx   # both
```

Both PDFs are written on every run. `--model` is authoritative: where the model
and the deck disagree by more than 5%, the model's figure is used *and* a
`DATA_INCONSISTENCY` flag names both values with their locations. Passing a
spreadsheet as `SOURCE` and another to `--model` is an error — there is no deck
for the second one to be authoritative over.

| Exit code | Meaning |
| --- | --- |
| 0 | Analysis produced (including a deck that could not be read, which yields a report full of gaps) |
| 1 | Unrecoverable input error (missing file, unsupported extension, two spreadsheets and no deck) |
| 2 | `--strict` and at least one CRITICAL flag fired |

## Web app

```bash
uvicorn deckscan.web:app --reload --port 8000
```

Drag a deck **or a spreadsheet** onto the main dropzone — it takes either, and a
spreadsheet dropped there is read as the financial model. Use the second dropzone
only to pair a model with a deck. Then watch the three-step progress and download
both PDFs and the full JSON. Uploads run as background jobs;
files and results are deleted after `JOB_TTL_SECONDS` (default one hour).

The interface uses the TEN Capital Network design system — dark navy card, the
tri-colour brand mark, Sora / Inter / JetBrains Mono. Everything lives in
[`src/deckscan/web_templates/`](src/deckscan/web_templates/); `base.html` holds
the design tokens, the other three templates extend it.

## Deploy to Railway

1. Push this repository to GitHub.
2. In Railway: **New Project → Deploy from GitHub repo**, and pick it.
   [`railway.toml`](railway.toml) supplies the build and start command; Nixpacks
   installs from [`requirements.txt`](requirements.txt) and reads the Python
   version from [`.python-version`](.python-version).
3. **Variables → New Variable**: `ANTHROPIC_API_KEY = sk-ant-...`
4. Deploy. Railway health-checks `/healthz`, which reports whether the key is
   configured without exposing it.

```bash
# or from the CLI
npm i -g @railway/cli
railway login && railway init
railway variables --set ANTHROPIC_API_KEY=sk-ant-...
railway up
```

**The deployment is open by default** — anyone with the URL can upload decks and
spend your API credit. Set `APP_PASSWORD` to put it behind HTTP Basic auth (any
username, that password); leave it empty to keep it open.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | — | Required. Uploads are refused without it. |
| `APP_PASSWORD` | empty | When set, gates the whole app behind HTTP Basic auth. |
| `JOB_TTL_SECONDS` | 3600 | How long uploads and generated PDFs survive on the server. |
| `WEB_MAX_WORKERS` | 2 | Concurrent analyses. |

## Configuration

Every threshold, keyword list, severity default and piece of user-visible text
lives in [`src/deckscan/config/rules.yaml`](src/deckscan/config/rules.yaml).
`--config PATH` is an **override**: it is deep-merged over the packaged defaults,
so it only needs the keys it changes.

```yaml
# tighter-growth.yaml
thresholds:
  growth:
    hockey_stick_multiple: 2.5
```

The narrative one-pager's structure — which fields are extracted, what each
section is labelled, and every colour and measurement — lives in
[`src/deckscan/config/onepager_template.json`](src/deckscan/config/onepager_template.json).
The fields Claude is asked for come from that same file, so the document and the
extraction can never drift apart.

## What the rules check

| Family | Fires on |
| --- | --- |
| Growth realism | Hockey sticks, a growth elbow exactly at the actual/projection boundary, flat history then a spike, a terminal year implying outsized market share, projections with no actuals |
| Burn and runway | Implausible cost per head, headcount outrunning opex, costs that don't follow the revenue plan, runway that doesn't reconcile with burn, runway under 12 months |
| Unit economics | LTV:CAC above the stage ceiling or under 1.0, LTV asserted with no derivation, LTV computed on revenue rather than gross profit, long CAC payback, zero churn, flat CAC while volume scales, revenue scaling without go-to-market spend |
| Completeness | Missing cash flow, P&L detail, balance sheet, assumptions, downside case, cap table, use of funds, pricing, TAM methodology, short projection horizon |
| Consistency | The same figure stated two ways, arithmetic that doesn't tie, scale-ambiguous figures, stale actuals |

Findings that assert an absence carry no locator by design; every other flag must
carry at least one page reference and at least one number, enforced by a model
validator.

## Development

```bash
python -m pytest --cov=deckscan     # tests never call the API
python -m ruff check src tests
python -m mypy
```

The test suite fakes the Anthropic client, so it runs offline and costs nothing.
