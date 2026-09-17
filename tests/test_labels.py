"""The labelling workflow: candidate pairs with evidence and verdicts, the CSV round trip, precision and recall."""

import csv

from typer.testing import CliRunner

from eww import labels, resolve, review
from eww.cli import app
from tests.conftest import eonet_item, gdacs_item, gdacs_source, ingest_items
from tests.test_identity import T0, austrian_floods


def seed(conn, data_dir):
    """One deterministic mirror pair (Croatia), one grey-zone pair (Austria), one unrelated far flood."""
    croatia = gdacs_item(1104153, "FL", "Flood in Croatia", 43.51, 16.44, "2026-09-08T01:00:00", iso3="HRV")
    mirror = eonet_item("EONET_24267", "Flood in Croatia 1104153", "floods", [16.31, 43.57], "2026-09-10T20:00:00Z", sources=(gdacs_source("FL", 1104153),))
    gdacs_flood, eonet_flood = austrian_floods()
    spain = gdacs_item(1104300, "FL", "Flood in Spain", 40.4, -3.7, "2026-09-01T00:00:00", iso3="ESP")
    ingest_items(conn, data_dir, "gdacs", [croatia, gdacs_flood, spain], T0)
    ingest_items(conn, data_dir, "eonet", [mirror, eonet_flood], T0)
    resolve.resolve(conn)


def test_candidate_pairs_carry_evidence_and_verdicts(conn, data_dir):
    seed(conn, data_dir)
    rows = labels.candidate_pairs(conn, days=3650)
    keys = {(r["source_a"], r["external_id_a"], r["source_b"], r["external_id_b"]) for r in rows}
    assert ("gdacs", "1104153", "eonet", "EONET_24267") in keys
    assert ("gdacs", "1104115", "eonet", "EONET_90001") in keys
    assert not any(r["external_id_a"] == "1104300" for r in rows)  # Spain blocks with nothing
    assert all(r["source_a"] == "gdacs" and r["source_b"] == "eonet" for r in rows)  # GDACS first, as in RESOLVE_SOURCE_ORDER
    croatia = next(r for r in rows if r["external_id_b"] == "EONET_24267")
    austria = next(r for r in rows if r["external_id_b"] == "EONET_90001")
    assert croatia["linked"] == "yes" and croatia["pipeline"] == "merged" and croatia["merged_by"] == "key" and croatia["key_conflict"] == "no"
    assert austria["linked"] == "no" and austria["pipeline"] == "proposed" and austria["merged_by"] == ""
    assert austria["same_event"] == "" and 0.6 <= austria["score"] < 0.9 and austria["days_apart"] == 2.0
    assert rows[0]["score"] >= rows[-1]["score"]
    assert set(rows[0]) == set(labels.CANDIDATE_COLUMNS)


def test_entities_keep_records_older_than_the_window(conn, data_dir):
    """A storm's early track points decide its merge; the verdict must see them even when the window starts later."""
    gdacs_storm = gdacs_item(1001303, "TC", "Tropical Cyclone LALA-26", 37.8, -179.2, "2026-08-12T15:00:00", eventname="LALA-26", datemodified="2026-08-28T07:00:00")
    lala = eonet_item("EONET_22563", "Tropical Storm Lala", "severeStorms", [-150.0, 15.0], "2026-08-12T18:00:00Z", magnitude=35.0, unit="kts")
    lala["geometry"].append({"magnitudeValue": 60.0, "magnitudeUnit": "kts", "date": "2026-08-27T18:00:00Z", "type": "Point", "coordinates": [-179.1, 38.1]})
    ingest_items(conn, data_dir, "gdacs", [gdacs_storm], T0)
    ingest_items(conn, data_dir, "eonet", [lala], T0)
    stats = resolve.resolve(conn)
    assert stats.events_merged == 1
    entities = {e.key: e for e in labels.source_entities(conn, "2026-08-20T00:00:00Z")}
    assert len(entities[("eonet", "EONET_22563")].record_ids) == 2  # the 12 August point is before the window but belongs to the entity
    rows = labels.candidate_pairs(conn, days=3650)
    lala_row = next(r for r in rows if r["external_id_b"] == "EONET_22563")
    assert (lala_row["pipeline"], lala_row["merged_by"]) == ("merged", "pipeline")


def test_csv_round_trip_and_evaluation(conn, data_dir, tmp_path):
    seed(conn, data_dir)
    rows = labels.candidate_pairs(conn, days=3650)
    path = labels.write_candidates(rows, tmp_path / "labels" / "merge_candidates.csv")
    with path.open(encoding="utf-8", newline="") as handle:
        header = next(csv.reader(handle))
    assert header == labels.CANDIDATE_COLUMNS
    pairs = labels.read_pairs(path)
    assert len(pairs) == len(rows) and all(p["same_event"] == "" for p in pairs)

    for pair in pairs:
        pair["same_event"] = "yes"
    result = labels.evaluate(conn, pairs)
    assert (result["true_pairs"], result["non_pairs"], result["auto_true"], result["auto_false"]) == (2, 0, 1, 0)
    assert result["precision"] == 1.0 and result["recall"] == 0.5 and result["proposed_true"] == 1 and result["missed"] == []
    assert result["passed"] is False  # recall below 60%
    text = labels.render_evaluation(result)
    assert "recall: 50.0%" in text and "FAIL  recall at least 60%" in text and "PASS  zero false merges" in text

    # the human accepts the Austrian proposal: recall of the automatic rule is unchanged, nothing is missed
    proposal = review.open_proposals(conn)[0]
    review.accept(proposal["proposal_id"], conn)
    result = labels.evaluate(conn, pairs)
    assert result["human_merged"] == 1 and result["recall"] == 0.5 and result["missed"] == [] and result["false_merges"] == []

    # relabelling the mirror as a non-pair turns the key link into a false merge
    for pair in pairs:
        if pair["external_id_b"] == "EONET_24267":
            pair["same_event"] = "no"
    result = labels.evaluate(conn, pairs)
    assert result["auto_false"] == 1 and result["precision"] == 0.0 and len(result["false_merges"]) == 1
    assert "false merges (labelled 'no' but on one event):" in labels.render_evaluation(result)

    # a pair the pipeline keeps apart and the human calls the same is 'missed'
    review.revert(review.recent_merges(conn)[0]["lineage_id"], conn)
    review.reject(proposal["proposal_id"], conn)
    for pair in pairs:
        pair["same_event"] = "yes"
    result = labels.evaluate(conn, pairs)
    assert len(result["missed"]) == 1 and result["missed"][0]["pipeline"] == "rejected"
    assert labels.parse_label(" Yes ") is True and labels.parse_label("0") is False and labels.parse_label("") is None


def test_cli_labels_and_eval(tmp_path, data_dir):
    from eww import db

    db_path = tmp_path / "labels.sqlite"
    connection = db.connect(db_path)
    db.init_db(connection)
    seed(connection, data_dir)
    connection.close()
    runner = CliRunner()
    candidates = tmp_path / "merge_candidates.csv"
    result = runner.invoke(app, ["--db", str(db_path), "labels", "candidates", "--days", "3650", "--out", str(candidates)])
    assert result.exit_code == 0, result.output
    assert "2 candidate pairs, 1 carrying a deterministic key" in result.stdout
    pairs_path = tmp_path / "merge_pairs.csv"
    rows = labels.read_pairs(candidates)
    for row in rows:
        row["same_event"] = "yes"
    with pairs_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=labels.CANDIDATE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    result = runner.invoke(app, ["--db", str(db_path), "eval", "merges", "--pairs", str(pairs_path)])
    assert result.exit_code == 0, result.output  # nothing missed, no false merge: recall alone does not fail the command
    assert "precision of the auto-merge rule: 100.0%  recall: 50.0%" in result.stdout
    missing = runner.invoke(app, ["--db", str(db_path), "eval", "merges", "--pairs", str(tmp_path / "nope.csv")])
    assert missing.exit_code == 2
