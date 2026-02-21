import os

def test_dashboard_exists():
    assert os.path.exists("index.html")
    assert os.path.exists("dashboard.js")
