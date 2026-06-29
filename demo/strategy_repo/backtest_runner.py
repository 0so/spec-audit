"""Synthetic demo file. Illustrates a real class of drift found during
dogfooding against a real research codebase: a resample-window
parameter declared in two places, silently diverged."""

N = 3000  # number of bootstrap resamples used to compute the reported Sharpe CI


def run_backtest():
    ...
