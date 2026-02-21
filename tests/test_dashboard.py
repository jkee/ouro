import pytest


def test_dashboard_exists():
    # simple existence test for dashboard.js and index.html
    import os
    assert os.path.exists("index.html")
    assert os.path.exists("dashboard.js")
