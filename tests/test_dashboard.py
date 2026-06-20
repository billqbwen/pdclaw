"""Tests for dashboard endpoints."""

import time
import http.client

import pytest

from pdca_dashboard import DashboardHandler, DashboardServer


def test_api_log_returns_content():
    """GET /api/log returns 200 with log lines from DashboardHandler.log_lines."""
    test_lines = ["line one", "line two", "line three"]
    DashboardHandler.log_lines = test_lines

    server = DashboardServer(host="127.0.0.1", port=9192)
    server.start()
    time.sleep(0.2)

    try:
        conn = http.client.HTTPConnection("127.0.0.1", 9192, timeout=5)
        try:
            conn.request("GET", "/api/log")
            resp = conn.getresponse()
            body = resp.read().decode()

            assert resp.status == 200
            assert resp.getheader("Content-Type") == "text/plain; charset=utf-8"
            assert body == "\n".join(test_lines)
            assert int(resp.getheader("Content-Length")) == len("\n".join(test_lines))
        finally:
            conn.close()
    finally:
        server.stop()


def test_api_log_empty_when_no_lines():
    """GET /api/log returns 200 with empty body when log_lines is empty."""
    DashboardHandler.log_lines = []

    server = DashboardServer(host="127.0.0.1", port=9193)
    server.start()
    time.sleep(0.2)

    try:
        conn = http.client.HTTPConnection("127.0.0.1", 9193, timeout=5)
        try:
            conn.request("GET", "/api/log")
            resp = conn.getresponse()
            body = resp.read().decode()

            assert resp.status == 200
            assert body == ""
            assert int(resp.getheader("Content-Length")) == 0
        finally:
            conn.close()
    finally:
        server.stop()
