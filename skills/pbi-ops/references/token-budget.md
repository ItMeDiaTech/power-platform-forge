# Token budget: what each pbi verb returns and the cheapest call sequences

Every envelope is one JSON object kept under 4 KB unless `--raw` is given. If a verb would go over, ppx halves its longest list and says so in `warnings` ("output shortened to fit 4 KB"); when you see that, narrow the call instead of passing `--raw`.

## Default output per verb

| Verb | Default items | Fields per item | Typical size |
|---|---|---|---|
| `pbi ws ls` | 30 workspaces | `id, name, onCapacity` | 1 to 3 KB |
| `pbi ls` | 15 items, plus `count`, `by_type`, `more` | `id, name, type`; reports add `model` (name); models add `lastRefresh`, `lastStatus` | 1.5 to 3 KB |
| `pbi report show` | one report | `id, name, format, reportType, isOwnedByMe, model, pages, page_names` (40 at most) | under 1.5 KB |
| `pbi model show` | one model | owner, `isRefreshable`, data sources, parameters, last 3 refreshes, `build` | 1 to 3 KB |
| `pbi history` | 5 entries (`--failures`: up to 5 failures from the last 60) | `start, end, status, type, requestId`; failures add `errorCode, message` (300 chars), `cause` | 1 to 3 KB |
| `pbi query` | 50 rows as CSV | over about 3 KB of CSV the rows go to a `.csv` file and the envelope keeps the path and 10 lines | under 3.5 KB |
| `pbi diff` | 15 entries per list | `added, removed, renamed, changed` with `counts` over all | 1 to 3 KB |
| `pbi refresh` | one refresh | `requestId, status, used_today, limit`, `changes` | under 1 KB |

## Make each call cheaper

- `--fields name,lastRefresh` drops ids and types (about 60 percent of a list item).
- `--type models` instead of everything, then join reports to models by the report's `model` name.
- `--top N` only as high as the question needs; `count` already tells you how many exist.
- Cached inventories cost no API call. `--refresh` once per session per workspace, not per question.
- For DAX, return the aggregate (`ROW("rows", COUNTROWS(T))`), not rows you then count.

## Fewest calls per question

| Question | Calls |
|---|---|
| "What reports are in workspace X and when did their models last refresh?" | 2: `pbi ls --type reports --fields name,model`, `pbi ls --type models --fields name,lastRefresh,lastStatus` |
| "Why did model Y fail to refresh?" | 1: `pbi history --model Y --failures` (spends no refresh) |
| "How many rows are in table T?" | 1: `pbi query --model Y --dax 'EVALUATE ROW("rows", COUNTROWS(T))'` |
| "Can I query model Y, and where does its data come from?" | 1: `pbi model show Y` |
| "What changed in X this week?" | 1: `pbi diff --workspace X` (needs a snapshot at least 7 days old) |
| "Refresh Y and tell me if it worked" | 1: `pbi refresh --model Y --wait --timeout 90`; on timeout, 1 more: `pbi history --model Y --top 1` |

Add one `ppx auth doctor` per session in front of these.
