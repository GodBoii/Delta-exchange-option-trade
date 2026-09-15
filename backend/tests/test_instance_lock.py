import pytest

from app.instance_lock import InstanceLock


def test_second_writer_is_rejected_until_owner_releases(tmp_path):
    first = InstanceLock(str(tmp_path / "writer.lock"))
    second = InstanceLock(str(tmp_path / "writer.lock"))
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="Another trading backend"):
            second.acquire()
    finally:
        first.close()
    second.acquire()
    second.close()
