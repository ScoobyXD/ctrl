import subprocess
import sys
import uuid

from ctrl.browser_lease import BrowserLease


def test_other_process_cannot_own_browser_until_current_owner_releases():
    name = "Local\\CTRL.LeaseTest." + uuid.uuid4().hex
    owner = BrowserLease(name)
    assert owner.acquire()
    assert owner.acquire()
    script = (
        "from ctrl.browser_lease import BrowserLease; import sys; "
        "lease=BrowserLease(sys.argv[1]); print(lease.acquire()); lease.release()"
    )
    try:
        result = subprocess.run([sys.executable, "-c", script, name], capture_output=True, text=True, check=True)
        assert result.stdout.strip() == "False"
    finally:
        owner.release()
        owner.release()
    result = subprocess.run([sys.executable, "-c", script, name], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "True"


def test_process_exit_releases_browser_even_without_cleanup():
    name = "Local\\CTRL.LeaseTest." + uuid.uuid4().hex
    script = (
        "from ctrl.browser_lease import BrowserLease; import sys,os; "
        "lease=BrowserLease(sys.argv[1]); assert lease.acquire(); os._exit(0)"
    )
    subprocess.run([sys.executable, "-c", script, name], check=True)
    owner = BrowserLease(name)
    try:
        assert owner.acquire()
    finally:
        owner.release()
