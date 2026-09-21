"""The M3 commands through the CLI: geonames load from a folder, enrich with a stubbed collector, extract, embed, attach, eval, purge, doctor."""

import csv

import httpx
from typer.testing import CliRunner

from eww import config, db, embed, extract, geocode
from eww.cli import app
from eww.enrich import gdelt
from tests.test_attach import BagEncoder, fake_ner
from tests.test_enrich import articles
from tests.test_geocode import dump  # noqa: F401


def test_m3_commands_end_to_end(tmp_path, data_dir, dump, monkeypatch):
    monkeypatch.setattr(config, "PROVIDER_LOG", tmp_path / "logs" / "providers.jsonl")
    monkeypatch.setattr(config, "ATTACHMENT_SAMPLE_CSV", tmp_path / "labels" / "attachment_sample.csv")
    monkeypatch.setattr(config, "DOCS_DIR", tmp_path / "docs")
    embed.set_encoder(BagEncoder())
    extract.set_ner(fake_ner)
    try:
        db_path = tmp_path / "cli.sqlite"
        conn = db.connect(db_path)
        db.init_db(conn)
        from eww import resolve
        from tests.conftest import gdacs_item, ingest_items

        ingest_items(conn, data_dir, "gdacs", [gdacs_item(1104124, "FL", "Flood in Nepal", 27.8, 85.3, "2026-09-10T00:00:00", todate="2026-09-16T00:00:00", alertlevel="Red", iso3="NPL")], "2026-09-16T15:10:00Z")
        resolve.resolve(conn)
        conn.close()
        runner = CliRunner()
        base = ["--db", str(db_path)]

        result = runner.invoke(app, [*base, "geonames", "load", "--from", str(dump)])
        assert result.exit_code == 0, result.output
        assert "gazetteer loaded: cities=8 admin1=3" in result.stdout

        def handler(request):
            return httpx.Response(200, json=articles(3))

        client = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": config.USER_AGENT})
        monkeypatch.setattr(gdelt, "collector", lambda http=None, sleep=None: gdelt.GdeltCollector(http=client, sleep=lambda s: None))
        result = runner.invoke(app, [*base, "enrich", "--source", "gdelt"])
        assert result.exit_code == 0, result.output
        assert "gdelt: events considered=1 queried=1" in result.stdout and "new=3" in result.stdout
        result = runner.invoke(app, [*base, "enrich", "--source", "nope"])
        assert result.exit_code == 2

        result = runner.invoke(app, [*base, "extract", "--no-remote"])
        assert result.exit_code == 0, result.output
        assert "extracted 3 documents: classified=3 located=3" in result.stdout
        result = runner.invoke(app, [*base, "embed"])
        assert result.exit_code == 0, result.output
        assert "embedded 3 documents model=bag-of-words-test dim=64" in result.stdout
        result = runner.invoke(app, [*base, "attach"])
        assert result.exit_code == 0, result.output
        assert "attached=3" in result.stdout and "event_document attached by pipeline: 3" in result.stdout
        result = runner.invoke(app, ["attach", "--rebuild"])  # refuses to rebuild the default database
        assert result.exit_code == 2

        result = runner.invoke(app, [*base, "eval", "attachments", "--sample", "2", "--seed", "1"])
        assert result.exit_code == 0, result.output
        sample = config.ATTACHMENT_SAMPLE_CSV
        rows = list(csv.DictReader(sample.open(encoding="utf-8")))
        assert len(rows) == 2 and rows[0]["correct"] == "" and rows[0]["event_title"] == "Flood in Nepal"
        for row in rows:
            row["correct"], row["labelled_by"] = "yes", "test"
        rows[0]["correct"], rows[0]["cause"] = "no", "wrong country"
        with sample.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        result = runner.invoke(app, [*base, "eval", "attachments"])
        assert result.exit_code == 1, result.output  # 50% precision is below the target
        assert "precision: 50.0%" in result.stdout and "wrong country (1)" in result.stdout
        report_text = config.milestone_doc(3).read_text(encoding="utf-8")
        assert config.milestone_doc(3).name == "m3.md"  # one document per milestone, named after it
        assert "# M3: headlines on pins" in report_text and "wrong country" in report_text and "Flood in Nepal" in report_text

        result = runner.invoke(app, [*base, "purge", "--dry-run"])
        assert result.exit_code == 0 and "would delete documents=0" in result.stdout
        result = runner.invoke(app, [*base, "doctor"])
        assert result.exit_code == 0, result.output
        assert "documents: 3 (gdelt/article 3)" in result.stdout
        assert "coverage: 1 of 1 events with severity >= 0.66" in result.stdout and "PASS" in result.stdout
        assert "gdelt: calls=1" in result.stdout and "user-agent carries" in result.stdout
        assert "gdelt spacing >= 5 s: PASS" in result.stdout
        result = runner.invoke(app, [*base, "identity"])
        assert result.exit_code == 0 and "attachment: weights spatial 0.45 temporal 0.25 text 0.3" in result.stdout
    finally:
        embed.set_encoder(None)
        extract.set_ner(None)
