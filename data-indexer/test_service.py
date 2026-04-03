#!/usr/bin/env python3
"""
Test script for the data indexer service.
"""

import requests
import time
import subprocess
import sys

def test_health():
    """Test the health endpoint."""
    try:
        response = requests.get('http://localhost:5001/health')
        if response.status_code == 200:
            print("[PASS] Health check passed")
            return True
        else:
            print(f"[FAIL] Health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"[FAIL] Health check error: {e}")
        return False

def test_xml_response(endpoint, description):
    """Test an XML endpoint."""
    try:
        response = requests.get(f'http://localhost:5001/{endpoint}?root=/tmp')
        if response.status_code == 200:
            content_type = response.headers.get('content-type', '')
            if 'xml' in content_type.lower():
                print(f"[PASS] {description} returned XML")
                print(f"  Sample XML: {response.text[:200]}...")
                return True
            else:
                print(f"[FAIL] {description} returned wrong content type: {content_type}")
                return False
        else:
            print(f"[FAIL] {description} failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"[FAIL] {description} error: {e}")
        return False

if __name__ == '__main__':
    print("Testing Data Indexer Service...")

    # Start the service in background
    print("Starting service...")
    process = subprocess.Popen([sys.executable, 'app.py'],
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)

    # Wait for service to start
    time.sleep(3)

    try:
        # Test health
        if not test_health():
            sys.exit(1)

        # Test endpoints
        endpoints = [
            ('rinex', 'RINEX indexer'),
            ('tecsuite', 'TEC-suite indexer'),
            ('abstec', 'AbsTEC indexer'),
            ('parquet', 'Parquet indexer')
        ]

        all_passed = True
        for endpoint, description in endpoints:
            print(f"Testing {endpoint}...")
            if not test_xml_response(endpoint, description):
                all_passed = False

        print("\nTest Summary:")
        if all_passed:
            print("[SUCCESS] All tests passed!")
            return 0
        else:
            print("[FAILURE] Some tests failed!")
            return 1

    finally:
        # Stop the service
        process.terminate()
        process.wait()