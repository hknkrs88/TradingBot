from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tradingbot.data.ohlcv.archive_import import (
    ArchiveImportError,
    ArchiveImportRequest,
    CURRENT_FILENAME,
    experimental_series_dir,
    import_archive_klines,
    resolve_archive_series_input,
    resolve_current_generation,
)
from tradingbot.data.ohlcv.archive_zip import ArchiveHttpResponse, monthly_kline_archive_ref

HOUR = 3_600_000
NOW_MS = 2_000_000_000_000
DEC_START = int(datetime(2024, 12, 1, tzinfo=UTC).timestamp()) * 1000
JAN_START = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp()) * 1000
GEN_A = "v-" + "a" * 32
GEN_B = "v-" + "b" * 32


@dataclass
class FakeHttpClient:
    responses: dict[str, ArchiveHttpResponse]
    calls: list[str] = field(default_factory=list)

    def fetch(self, url: str) -> ArchiveHttpResponse:
        self.calls.append(url)
        if url not in self.responses:
            return ArchiveHttpResponse(status=404, body=b"", headers={})
        return self.responses[url]


def _csv_line(open_ms: int) -> str:
    return f"{open_ms},1,1,1,1,1,{open_ms + HOUR - 1},0,0,0,0,0"


def _zip_csv(member: str, lines: list[str]) -> bytes:
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(member, payload)
    return buf.getvalue()


def _add_ref(client: FakeHttpClient, ref, lines: list[str]) -> None:
    payload = _zip_csv(ref.csv_name, lines)
    digest = hashlib.sha256(payload).hexdigest()
    client.responses[ref.checksum_url] = ArchiveHttpResponse(
        200, f"{digest}  {ref.zip_name}\n".encode(), {}
    )
    client.responses[ref.zip_url] = ArchiveHttpResponse(200, payload, {})


def _request(tmp_path: Path) -> ArchiveImportRequest:
    return ArchiveImportRequest(
        symbol="BTCUSDT",
        timeframe="1h",
        start_ms=DEC_START,
        end_exclusive_ms=JAN_START,
        cadence="monthly",
        dest_root=tmp_path / "experimental",
        now_ms=NOW_MS,
    )


def _client() -> tuple[FakeHttpClient, object]:
    ref = monthly_kline_archive_ref("BTCUSDT", "1h", 2024, 12)
    client = FakeHttpClient({})
    _add_ref(client, ref, [_csv_line(DEC_START)])
    return client, ref


def _series(tmp_path: Path) -> Path:
    return experimental_series_dir(tmp_path / "experimental", "BTCUSDT", "1h")


def _import_ok(tmp_path: Path, generation_id: str = GEN_A) -> None:
    client, _ = _client()
    import_archive_klines(_request(tmp_path), client=client, generation_id=generation_id)


def _crash(tmp_path: Path, crash_at: str, generation_id: str = GEN_A) -> subprocess.CompletedProcess[bytes]:
    repo = Path(__file__).resolve().parents[2]
    payload = f"""
from pathlib import Path
import hashlib, io, zipfile
from tradingbot.data.ohlcv.archive_import import ArchiveImportRequest, import_archive_klines
from tradingbot.data.ohlcv.archive_zip import ArchiveHttpResponse, monthly_kline_archive_ref
tmp = Path(r'{tmp_path}')
ref = monthly_kline_archive_ref('BTCUSDT', '1h', 2024, 12)
open_ms = {DEC_START}
line = f'{{open_ms}},1,1,1,1,1,{{open_ms + 3600000 - 1}},0,0,0,0,0'
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w') as z:
    z.writestr(ref.csv_name, (line + chr(10)).encode())
body = buf.getvalue()
digest = hashlib.sha256(body).hexdigest()
class C:
    def fetch(self, url):
        if url.endswith('.CHECKSUM'):
            return ArchiveHttpResponse(200, f'{{digest}}  {{ref.zip_name}}'.encode() + b'\\n', {{}})
        if url.endswith('.zip'):
            return ArchiveHttpResponse(200, body, {{}})
        return ArchiveHttpResponse(404, b'', {{}})
req = ArchiveImportRequest(
    symbol='BTCUSDT', timeframe='1h', start_ms={DEC_START},
    end_exclusive_ms={JAN_START}, cadence='monthly',
    dest_root=tmp / 'experimental', now_ms=2000000000000,
)
import_archive_klines(req, client=C(), crash_at={crash_at!r}, generation_id={generation_id!r})
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", payload],
        cwd=str(repo),
        env=env,
        capture_output=True,
        check=False,
    )


def _reopen(tmp_path: Path) -> subprocess.CompletedProcess[bytes]:
    repo = Path(__file__).resolve().parents[2]
    code = (
        "from pathlib import Path\n"
        "from tradingbot.data.ohlcv.archive_import import experimental_series_dir, resolve_current_generation\n"
        f"series = experimental_series_dir(Path(r'{tmp_path}') / 'experimental', 'BTCUSDT', '1h')\n"
        "print(resolve_current_generation(series).name)\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(repo),
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )


def test_crash_before_generation_leaves_no_live_set(tmp_path: Path) -> None:
    crashed = _crash(tmp_path, "before_generation")
    assert crashed.returncode == 91
    series = _series(tmp_path)
    assert not list(series.glob("v-*")) if series.exists() else True
    assert not (series / CURRENT_FILENAME).is_file() if series.exists() else True
    reopened = _reopen(tmp_path)
    assert reopened.returncode != 0


def test_crash_after_checksum_generation_not_live(tmp_path: Path) -> None:
    crashed = _crash(tmp_path, "after_checksum")
    assert crashed.returncode == 91
    gen = _series(tmp_path) / GEN_A
    assert gen.is_dir()
    assert not (_series(tmp_path) / CURRENT_FILENAME).is_file()
    reopened = _reopen(tmp_path)
    assert reopened.returncode != 0
    assert "current.json missing" in (reopened.stderr or "") + (reopened.stdout or "") or reopened.returncode != 0


def test_crash_after_provenance_not_live(tmp_path: Path) -> None:
    assert _crash(tmp_path, "after_provenance").returncode == 91
    assert (_series(tmp_path) / GEN_A / "provenance.json").is_file()
    assert _reopen(tmp_path).returncode != 0


def test_crash_after_candles_not_live(tmp_path: Path) -> None:
    assert _crash(tmp_path, "after_candles").returncode == 91
    assert (_series(tmp_path) / GEN_A / "candles.json").is_file()
    assert not (_series(tmp_path) / GEN_A / "import_report.json").is_file()
    assert _reopen(tmp_path).returncode != 0


def test_crash_after_report_before_pointer(tmp_path: Path) -> None:
    assert _crash(tmp_path, "after_report").returncode == 91
    assert (_series(tmp_path) / GEN_A / "import_report.json").is_file()
    assert not (_series(tmp_path) / CURRENT_FILENAME).is_file()
    assert _reopen(tmp_path).returncode != 0


def test_crash_after_validated_before_pointer(tmp_path: Path) -> None:
    assert _crash(tmp_path, "after_generation_validated").returncode == 91
    assert not (_series(tmp_path) / CURRENT_FILENAME).is_file()
    assert _reopen(tmp_path).returncode != 0


def test_crash_after_current_is_live(tmp_path: Path) -> None:
    assert _crash(tmp_path, "after_current").returncode == 91
    live = resolve_current_generation(_series(tmp_path))
    assert live.name == GEN_A
    reopened = _reopen(tmp_path)
    assert reopened.returncode == 0
    assert reopened.stdout.strip() == GEN_A


def test_second_import_crash_keeps_first_generation(tmp_path: Path) -> None:
    _import_ok(tmp_path, GEN_A)
    first = resolve_current_generation(_series(tmp_path))
    assert first.name == GEN_A
    assert _crash(tmp_path, "after_candles", generation_id=GEN_B).returncode == 91
    assert (_series(tmp_path) / GEN_B / "candles.json").is_file()
    reopened = _reopen(tmp_path)
    assert reopened.returncode == 0
    assert reopened.stdout.strip() == GEN_A
    assert resolve_current_generation(_series(tmp_path)).name == GEN_A
    assert (_series(tmp_path) / GEN_A).is_dir()
    assert (_series(tmp_path) / GEN_B).is_dir()


def test_corrupt_pointer_fail_closed(tmp_path: Path) -> None:
    _import_ok(tmp_path)
    (_series(tmp_path) / CURRENT_FILENAME).write_text("{bad", encoding="utf-8")
    with pytest.raises(ArchiveImportError, match="invalid"):
        resolve_current_generation(_series(tmp_path))
    reopened = _reopen(tmp_path)
    assert reopened.returncode != 0


def test_missing_generation_file_fail_closed(tmp_path: Path) -> None:
    _import_ok(tmp_path)
    live = resolve_current_generation(_series(tmp_path))
    (live / "candles.json").unlink()
    with pytest.raises(ArchiveImportError, match="candles.json missing"):
        resolve_current_generation(_series(tmp_path))
    assert _reopen(tmp_path).returncode != 0


@pytest.mark.parametrize(
    "dst_name",
    [
        "BTCUSDT-1h-2024-12.zip.CHECKSUM",
        "provenance.json",
        "candles.json",
        "import_report.json",
        CURRENT_FILENAME,
    ],
)
def test_replace_fn_failure_does_not_flip_live_pointer(tmp_path: Path, dst_name: str) -> None:
    _import_ok(tmp_path, GEN_A)
    live_before = resolve_current_generation(_series(tmp_path)).name
    client, _ = _client()

    def flaky(src: Path, dst: Path) -> None:
        if dst.name == dst_name:
            raise OSError("injected")
        os.replace(src, dst)

    with pytest.raises(ArchiveImportError):
        import_archive_klines(
            _request(tmp_path),
            client=client,
            replace_fn=flaky,
            generation_id=GEN_B,
        )
    assert resolve_current_generation(_series(tmp_path)).name == live_before == GEN_A
    assert (_series(tmp_path) / GEN_A).is_dir()
    pointer = json.loads((_series(tmp_path) / CURRENT_FILENAME).read_text(encoding="utf-8"))
    assert pointer["generation"] == GEN_A


def test_tampered_candles_are_rejected(tmp_path: Path) -> None:
    _import_ok(tmp_path)
    live = resolve_current_generation(_series(tmp_path))
    candles = live / "candles.json"
    candles.write_text(candles.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ArchiveImportError, match="candles hash mismatch"):
        resolve_current_generation(_series(tmp_path))


def test_tampered_checksum_file_hash_is_rejected(tmp_path: Path) -> None:
    _import_ok(tmp_path)
    live = resolve_current_generation(_series(tmp_path))
    checksum = next((live / "checksums").iterdir())
    checksum.write_text(checksum.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ArchiveImportError, match="checksum file hash mismatch"):
        resolve_current_generation(_series(tmp_path))


def test_checksum_zip_sha256_mismatch_is_rejected(tmp_path: Path) -> None:
    _import_ok(tmp_path)
    live = resolve_current_generation(_series(tmp_path))
    checksum = next((live / "checksums").iterdir())
    zip_name = checksum.name.removesuffix(".CHECKSUM")
    checksum.write_text(f"{'0' * 64}  {zip_name}\n", encoding="utf-8")
    with pytest.raises(ArchiveImportError, match="checksum zip sha256 mismatch"):
        resolve_current_generation(_series(tmp_path))


def test_missing_checksum_is_rejected(tmp_path: Path) -> None:
    _import_ok(tmp_path)
    live = resolve_current_generation(_series(tmp_path))
    checksum = next((live / "checksums").iterdir())
    checksum.unlink()
    with pytest.raises(ArchiveImportError, match="checksum missing"):
        resolve_current_generation(_series(tmp_path))


def test_pointer_path_traversal_is_rejected(tmp_path: Path) -> None:
    _import_ok(tmp_path)
    pointer = _series(tmp_path) / CURRENT_FILENAME
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    payload["generation"] = payload["path"] = "../" + payload["generation"]
    pointer.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ArchiveImportError, match="invalid|escapes"):
        resolve_current_generation(_series(tmp_path))


def test_resolve_archive_series_input_uses_live_generation(tmp_path: Path) -> None:
    _import_ok(tmp_path)
    series = _series(tmp_path)
    live = resolve_current_generation(series)
    expected = live / "candles.json"
    assert resolve_archive_series_input(series) == expected
    assert resolve_archive_series_input(live) == expected
    assert resolve_archive_series_input(expected) == expected


def test_resolve_archive_series_input_rejects_leftover_root_file(tmp_path: Path) -> None:
    _import_ok(tmp_path)
    leftover = _series(tmp_path) / "candles.json"
    leftover.write_text("LEGACY", encoding="utf-8")
    with pytest.raises(ArchiveImportError, match="legacy root candles.json is not live"):
        resolve_archive_series_input(leftover)
    live = resolve_current_generation(_series(tmp_path)) / "candles.json"
    assert resolve_archive_series_input(live) == live
    assert leftover.read_text(encoding="utf-8") == "LEGACY"


def test_resolve_archive_series_input_rejects_legacy_root(tmp_path: Path) -> None:
    dest = _series(tmp_path)
    dest.mkdir(parents=True)
    leftover = dest / "candles.json"
    leftover.write_text("LEGACY", encoding="utf-8")
    with pytest.raises(ArchiveImportError, match="legacy root candles.json is not live"):
        resolve_archive_series_input(dest)
    assert leftover.read_text(encoding="utf-8") == "LEGACY"


def test_import_report_paths_match_resolver(tmp_path: Path) -> None:
    client, _ = _client()
    report = import_archive_klines(_request(tmp_path), client=client, generation_id=GEN_A)
    series = _series(tmp_path)
    live = resolve_current_generation(series)
    assert report["generation"] == live.name == GEN_A
    assert report["current_path"] == (series / CURRENT_FILENAME).as_posix()
    assert report["candles_path"] == (live / "candles.json").as_posix()
    assert resolve_archive_series_input(series).as_posix() == report["candles_path"]


def test_import_cli_prints_resolver_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import importlib.util

    script = Path(__file__).resolve().parents[2] / "scripts" / "import_binance_archive.py"
    spec = importlib.util.spec_from_file_location("import_binance_archive", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    client, _ = _client()
    rc = mod.main(
        [
            "--root",
            str(tmp_path),
            "--symbol",
            "BTCUSDT",
            "--timeframe",
            "1h",
            "--start",
            "2024-12-01",
            "--end",
            "2025-01-01",
            "--dest-root",
            str(tmp_path / "experimental"),
        ],
        client=client,
    )
    assert rc == 0
    printed = capsys.readouterr().out
    series = _series(tmp_path)
    live = resolve_current_generation(series)
    candles = (live / "candles.json").as_posix()
    current = (series / CURRENT_FILENAME).as_posix()
    assert f"generation={live.name}" in printed
    assert f"current={current}" in printed
    assert f"candles_path={candles}" in printed
    assert resolve_archive_series_input(series).as_posix() == candles


def test_legacy_root_files_are_not_treated_as_live(tmp_path: Path) -> None:
    dest = _series(tmp_path)
    dest.mkdir(parents=True)
    (dest / "candles.json").write_text("LEGACY", encoding="utf-8")
    with pytest.raises(ArchiveImportError, match="current.json missing"):
        resolve_current_generation(dest)
    _import_ok(tmp_path, GEN_A)
    assert dest.joinpath("candles.json").read_text(encoding="utf-8") == "LEGACY"
    assert resolve_current_generation(dest).name == GEN_A
