# Notebooks

The analytics library is importable outside the API. For example, from `backend/` with the venv active:

```python
from app.data.synthetic import generate_universe
from app.data.validation import validate_bars
from app.strategies.registry import build_strategy
from app.backtesting.engine import run_backtest, CostModel

bars = validate_bars(generate_universe().bars).clean
close = bars.pivot(index="date", columns="symbol", values="close")
open_ = bars.pivot(index="date", columns="symbol", values="open")
bench = close.pop("NXMKT"); open_ = open_.drop(columns="NXMKT")
res = run_backtest(build_strategy("momentum", {"top_n": 8}), close, open_, None, "2019-01-02", None, costs=CostModel(5, 5))
print(res.equity.iloc[-1], res.total_costs, len(res.trades))
```

Exploratory notebooks go here. Committed notebooks should be cleared of outputs, and the generated data they use should be regenerated rather than committed.
