import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(
    shutil.which("gcc") is None, reason="native gcc is unavailable"
)
def test_stepcompress_history_reads_remain_consistent_during_concurrent_expiry():
    root = Path(__file__).parents[1]
    source_dir = root / "klippy" / "chelper"
    test_source = Path(__file__).with_name(
        "stepcompress_history_concurrency_test.c"
    )
    executable = root / "test" / ".stepcompress_history_concurrency_test"
    compile_command = [
        "gcc",
        "-std=gnu11",
        "-O2",
        "-g",
        "-pthread",
        "-I",
        str(source_dir),
        str(test_source),
        str(source_dir / "stepcompress.c"),
        str(source_dir / "msgblock.c"),
        "-lm",
        "-o",
        str(executable),
    ]
    try:
        compile_result = subprocess.run(
            compile_command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        assert compile_result.returncode == 0, compile_result.stderr

        run_result = subprocess.run(
            [str(executable)],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        assert run_result.returncode == 0, run_result.stdout + run_result.stderr
    finally:
        executable.unlink(missing_ok=True)
