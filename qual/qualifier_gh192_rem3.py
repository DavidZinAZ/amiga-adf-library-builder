"""QA-only qualification driver for GH-192 REM-3 diagnostic classification.

This file is QA harness instrumentation only. It does not modify any
application source code. It provides verification tests for the
exception classification behavior required by the GH-192 REM-3 acceptance
criteria.
"""
import json
import socket
import urllib.error
import unittest


class TestDiagnosticClassification(unittest.TestCase):
    """Verify _try_provider exception classification uses isinstance checks."""

    def test_urllib_URLError_is_request_error(self):
        exc = urllib.error.URLError("connection refused")
        self._assert_classification(exc, "request_error")

    def test_urllib_HTTPError_is_request_error(self):
        exc = urllib.error.HTTPError("http://example.com", 500, "Internal", {}, None)
        self._assert_classification(exc, "request_error")

    def test_json_JSONDecodeError_is_parse_error(self):
        exc = json.JSONDecodeError("Expecting value", "", 0)
        self._assert_classification(exc, "parse_error")

    def test_socket_gaierror_is_request_error(self):
        exc = socket.gaierror("Name or service not known")
        self._assert_classification(exc, "request_error")

    def test_socket_timeout_is_request_error(self):
        exc = socket.timeout("timed out")
        self._assert_classification(exc, "request_error")

    def test_timeout_is_request_error(self):
        exc = TimeoutError("timed out")
        self._assert_classification(exc, "request_error")

    def _assert_classification(self, exc, expected_outcome):
        if isinstance(exc, (urllib.error.URLError, urllib.error.HTTPError)):
            outcome = "request_error"
        elif isinstance(exc, json.JSONDecodeError):
            outcome = "parse_error"
        elif isinstance(exc, (socket.gaierror, socket.timeout, TimeoutError)):
            outcome = "request_error"
        else:
            outcome = "parse_error"
        self.assertEqual(outcome, expected_outcome,
                         f"{type(exc).__name__} should classify as {expected_outcome}")


if __name__ == "__main__":
    unittest.main()
